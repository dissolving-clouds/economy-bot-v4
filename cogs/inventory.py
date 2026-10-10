"""Инвентарь ролей: надеть, убрать в хранилище, продлить, подарить, удалить."""
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from cards import build_inventory_card
from cogs.cases import ItemsView
from cogs.shop import parse_colour
from utils import mark_self_delete, TempView, add_role, calc_fee, delete_later, remove_role, send


# ---------------------------------------------------------------- логика

async def renew_item(bot, user, role_id: int) -> tuple[bool, str]:
    """Продлевает роль. Автор продлевает свой лот, покупатель — свою покупку."""
    db = bot.db
    now = int(time.time())
    days = await db.get_int("user_role_days")

    item = await db.get_item(user.id, role_id)
    if not item or (item["expires_at"] and item["expires_at"] <= now):
        return False, "❌ Этой роли нет в твоём инвентаре."
    if item["expires_at"] == 0:
        return False, "♾ Эта роль выдана навсегда — продлевать не нужно."
    cr = await db.get_custom_role(role_id)
    if cr is None:
        return False, "🎁 Эта роль из кейса — её нельзя продлить, она действует до указанного срока."

    price = cr["price"]

    if cr["creator_id"] == user.id:  # продление лота автором
        if not await db.try_remove(user.id, price):
            return False, f"❌ Не хватает монет. Продление стоит {await db.fmt(price)}."
        new_exp = max(cr["expires_at"], now) + days * 86400
        await db.update_custom_expiry(role_id, new_exp)
        await db.set_item_expiry(user.id, role_id, new_exp)
        return True, f"✅ Роль продлена до <t:{new_exp}:D> за {await db.fmt(price)}."

    # продление покупателем
    if cr["expires_at"] - now < 86400:
        return False, "❌ Автор скоро не продлит эту роль, продление недоступно."
    if not await db.try_remove(user.id, price):
        return False, f"❌ Не хватает монет. Продление стоит {await db.fmt(price)}."
    percent = await db.get_int("role_commission")
    fee = calc_fee(price, percent)
    await db.add_balance(cr["creator_id"], price - fee)
    await db.add_transaction(now, user.id, cr["creator_id"], price, fee, "role_renew", f"роль <@&{role_id}>")
    await db.add_role_sale(now, role_id, user.id, price - fee, "renew")
    new_exp = max(item["expires_at"], now) + days * 86400
    await db.set_item_expiry(user.id, role_id, new_exp)
    return True, f"✅ Роль продлена до <t:{new_exp}:D> за {await db.fmt(price)}. Автор получил {await db.fmt(price - fee)}."


# ---------------------------------------------------------------- подарок

class GiftOfferView(discord.ui.View):
    """Предложение подарка: публичное сообщение с кнопками для получателя."""

    def __init__(self, bot, giver, receiver, role_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.giver = giver
        self.receiver = receiver
        self.role_id = role_id
        self.message: discord.Message | None = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.receiver.id:
            await send(interaction, "⛔ Этот подарок адресован не тебе.")
            return False
        return True

    async def on_timeout(self):
        if self.message and not self.done:
            embed = discord.Embed(description="⌛ Время вышло — подарок не принят.", color=discord.Color.greyple())
            try:
                await self.message.edit(content=None, embed=embed, view=None)
            except discord.HTTPException:
                pass
            delete_later(self.message)

    async def transfer(self, guild: discord.Guild) -> tuple[bool, str]:
        """Подарить можно только роль, которую создал сам даритель. Он сохраняет её у себя,
        а получатель получает копию на оставшийся срок роли."""
        db = self.bot.db
        now = int(time.time())
        cr = await db.get_custom_role(self.role_id)
        role = guild.get_role(self.role_id)
        if cr is None or role is None or cr["expires_at"] <= now:
            return False, "❌ Подарок больше недоступен: роль удалена или её срок истёк."
        if cr["creator_id"] != self.giver.id:
            return False, "❌ Дарить можно только роли, которые ты сам создал."
        if role.permissions.administrator:
            return False, "❌ Роли с правами администратора дарить нельзя."
        if await db.get_item(self.receiver.id, self.role_id):
            return False, "❌ У получателя уже есть эта роль."

        await db.add_item(self.receiver.id, self.role_id, cr["expires_at"], 1)
        await add_role(guild, self.receiver.id, role, "Получил роль в подарок")
        return True, (f"🎁 {self.receiver.mention} принял подарок: роль {role.mention} от {self.giver.mention}! "
                      f"Роль действует до <t:{cr['expires_at']}:D>.")

    @discord.ui.button(label="Принять", emoji="🎁", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        self.stop()
        await interaction.response.defer()
        ok, text = await self.transfer(interaction.guild)
        embed = discord.Embed(description=text, color=discord.Color.green() if ok else discord.Color.red())
        msg = await interaction.edit_original_response(content=None, embed=embed, view=None)
        delete_later(msg)

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.done = True
        self.stop()
        embed = discord.Embed(
            description=f"🏳️ {self.receiver.mention} отказался от подарка.", color=discord.Color.greyple()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        delete_later(self.message)


class GiftTargetView(TempView):
    def __init__(self, bot, giver, role_id: int):
        super().__init__(owner_id=giver.id, timeout=60)
        self.bot = bot
        self.giver = giver
        self.role_id = role_id

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Кому подарить?")
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        target = select.values[0]
        if target.bot or target.id == self.giver.id:
            return await send(interaction, "❌ Выбери другого участника (не себя и не бота).")
        if await self.bot.db.get_item(target.id, self.role_id):
            return await send(interaction, "❌ У этого участника уже есть такая роль.")

        role = interaction.guild.get_role(self.role_id)
        view = GiftOfferView(self.bot, self.giver, target, self.role_id)
        embed = discord.Embed(
            title="🎁 Тебе хотят подарить роль!",
            description=f"{self.giver.mention} дарит {target.mention} роль {role.mention if role else '—'}.\n"
                        f"Ты принимаешь подарок? У тебя есть 3 минуты.",
            color=discord.Color.orange(),
        )
        channel = self.bot.get_channel(config.CART_CHANNEL_ID) or interaction.channel
        view.message = await channel.send(content=target.mention, embed=embed, view=view)
        await interaction.response.edit_message(content=f"✅ Предложение отправлено в <#{channel.id}>.", view=None)
        delete_later(self.message)
        self.stop()


# ---------------------------------------------------------------- удаление роли автором

class ConfirmDeleteView(TempView):
    def __init__(self, bot, user, role_id: int):
        super().__init__(owner_id=user.id, timeout=30)
        self.bot = bot
        self.user = user
        self.role_id = role_id

    @discord.ui.button(label="Да, удалить навсегда", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = self.bot.db
        cr = await db.get_custom_role(self.role_id)
        if cr is None or cr["creator_id"] != self.user.id:
            await interaction.response.edit_message(content="❌ Роль уже удалена или это не твоя роль.", view=None)
            return delete_later(self.message)
        role = interaction.guild.get_role(self.role_id)
        if role:
            try:
                mark_self_delete(role.id)
                await role.delete(reason="Автор удалил свою роль")
            except discord.HTTPException:
                await interaction.response.edit_message(content="❌ Не удалось удалить роль.", view=None)
                return delete_later(self.message)
        await db.delete_custom_role(self.role_id)
        await interaction.response.edit_message(content="🗑 Роль удалена. Откройте /inventory заново, чтобы обновить список.", view=None)
        delete_later(self.message)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Отменено.", view=None)
        delete_later(self.message)


# ---------------------------------------------------------------- сам инвентарь

class ItemSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Выбери роль…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: "InventoryView" = self.view
        view.selected = int(self.values[0])
        await interaction.response.defer()
        await view.refresh(interaction)


async def own_role_candidates(db, guild: discord.Guild, member: discord.Member, known: set[int]) -> list[discord.Role]:
    """Роли, которые уже есть у участника (не из магазина и не из кейса) и которые можно спрятать в хранилище.
    Не берём: служебные, выше бота, с правом «Администратор», роли любовных и кастомных комнат."""
    excluded = {r["role_id"] for r in await db.all_rooms()} | {r["role_id"] for r in await db.all_crooms()}
    love_role = await db.get_int("room_role_id")
    if love_role:
        excluded.add(love_role)
    result = []
    for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
        if role.id in known or role.id in excluded or role.id in config.NO_HIDE_ROLE_IDS:
            continue
        if role.is_default() or role.managed or role >= guild.me.top_role or role.permissions.administrator:
            continue
        result.append(role)
    return result


class InventoryView(TempView):
    def __init__(self, bot, guild: discord.Guild, user: discord.Member):
        super().__init__(owner_id=user.id, timeout=config.MENU_LIFETIME)
        self.bot = bot
        self.guild = guild
        self.user = user
        self.selected: int | None = None
        self.select_item: ItemSelect | None = None
        self.top_embed: discord.Embed | None = None  # картинка-карточка сверху

    def embeds(self, embed: discord.Embed) -> list[discord.Embed]:
        return [self.top_embed, embed] if self.top_embed else [embed]

    async def build(self) -> discord.Embed:
        """Собирает embed и пересоздаёт выпадающий список."""
        db = self.bot.db
        now = int(time.time())
        lines, options = [], []
        inventory = await db.get_inventory(self.user.id, now)

        for it in inventory:
            role = self.guild.get_role(it["role_id"])
            if role is None:
                continue
            state = "надета" if it["equipped"] else "в хранилище"
            icon = "👕" if it["equipped"] else "📦"
            term = "навсегда" if not it["expires_at"] else f"до <t:{it['expires_at']}:D> (<t:{it['expires_at']}:R>)"
            line = f"{icon} {role.mention} — {state}, {term}"
            cr = await db.get_custom_role(it["role_id"])
            if cr:
                if cr["creator_id"] == self.user.id:
                    sale = "" if cr["listed"] else ", снята с продажи"
                    line += f"\n└ ✍️ Твоя роль, цена {cr['price']}{sale}"
                else:
                    line += f"\n└ Автор <@{cr['creator_id']}>, цена продления {cr['price']}"
            if it["source"] in ("case", "perm"):
                line += "\n└ 🎁 Из кейса" + (" · роль с правами" if it["source"] == "perm" else "")
            elif it["source"] == "own":
                line += "\n└ 👤 Твоя роль (была у тебя раньше)"
            lines.append(line)
            if len(options) < 25:
                options.append(discord.SelectOption(
                    label=role.name[:100], value=str(role.id), description=state, default=(role.id == self.selected)
                ))

        # роли, которые были у участника до этого: их тоже можно спрятать
        member = self.guild.get_member(self.user.id) or self.user
        for role in await own_role_candidates(db, self.guild, member, {it["role_id"] for it in inventory}):
            lines.append(f"👕 {role.mention} — надета, навсегда\n└ 👤 Твоя роль — можно спрятать кнопкой «Снять»")
            if len(options) < 25:
                options.append(discord.SelectOption(
                    label=role.name[:100], value=str(role.id), description="надета · твоя роль",
                    default=(role.id == self.selected)
                ))

        if self.select_item is not None:
            self.remove_item(self.select_item)
            self.select_item = None
        if options:
            self.select_item = ItemSelect(options)
            self.add_item(self.select_item)

        embed = discord.Embed(
            title="🎒 Хранилище ролей",
            description="\n\n".join(lines[:25]) or "Ролей пока нет. Купи или создай роль в /shop.",
            color=discord.Color.blurple(),
        )
        items = len(await db.get_case_items(self.user.id))
        wallet = await db.get_wallet(self.user.id)
        if items or wallet:
            embed.add_field(
                name="📦 Ждут в инвентаре",
                value=f"Предметов из кейсов: **{items}** · монет: **{await db.fmt(wallet)}**\nНажми «Инвентарь».",
                inline=False,
            )
        embed.set_footer(text="Выбери роль в списке, затем нажми кнопку. Дарить можно только роли, которые ты создал сам.")
        return embed

    async def refresh(self, interaction: discord.Interaction):
        embed = await self.build()
        await interaction.edit_original_response(embeds=self.embeds(embed), view=self)

    async def require_selected(self, interaction: discord.Interaction):
        if self.selected is None:
            await send(interaction, "Сначала выбери роль в списке выше.")
            return None
        db = self.bot.db
        item = await db.get_item(self.user.id, self.selected)
        if not item:
            # роль, которая была у участника раньше: заносим в хранилище при первом действии
            member = self.guild.get_member(self.user.id) or self.user
            own = await own_role_candidates(db, self.guild, member, set())
            if any(r.id == self.selected for r in own):
                await db.add_item(self.user.id, self.selected, 0, 1, "own")
                item = await db.get_item(self.user.id, self.selected)
        if not item or (item["expires_at"] and item["expires_at"] <= int(time.time())):
            await send(interaction, "❌ Этой роли уже нет в инвентаре.")
            return None
        return item

    @discord.ui.button(label="Надеть", emoji="👕", style=discord.ButtonStyle.primary, row=1)
    async def equip(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.require_selected(interaction)
        if not item:
            return
        await interaction.response.defer()
        role = self.guild.get_role(item["role_id"])
        if role is None or not await add_role(self.guild, self.user.id, role, "Надел роль из инвентаря"):
            await send(interaction, "❌ Не удалось выдать роль (роль бота должна быть выше неё).")
        else:
            await self.bot.db.set_equipped(self.user.id, item["role_id"], 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Снять", emoji="📦", style=discord.ButtonStyle.secondary, row=1)
    async def store(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.require_selected(interaction)
        if not item:
            return
        await interaction.response.defer()
        role = self.guild.get_role(item["role_id"])
        if item["role_id"] in config.NO_HIDE_ROLE_IDS:
            return await send(interaction, "⛔ Эту роль нельзя скрыть.")
        # сначала помечаем «в хранилище», чтобы слежение за снятием ролей не удалило запись
        await self.bot.db.set_equipped(self.user.id, item["role_id"], 0)
        if not await remove_role(self.guild, self.user.id, role, "Убрал роль в хранилище"):
            await self.bot.db.set_equipped(self.user.id, item["role_id"], item["equipped"])
            await send(interaction, "❌ Не удалось снять роль.")
        await self.refresh(interaction)

    @discord.ui.button(label="Продлить", emoji="🔁", style=discord.ButtonStyle.success, row=1)
    async def renew(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.require_selected(interaction)
        if not item:
            return
        await interaction.response.defer()
        ok, text = await renew_item(self.bot, self.user, item["role_id"])
        await send(interaction, text)
        await self.refresh(interaction)

    @discord.ui.button(label="Подарить", emoji="🎁", style=discord.ButtonStyle.primary, row=1)
    async def gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.require_selected(interaction)
        if not item:
            return
        role = self.guild.get_role(item["role_id"])
        if role is None:
            return await send(interaction, "❌ Роль не найдена.")
        cr = await self.bot.db.get_custom_role(role.id)
        if not cr or cr["creator_id"] != self.user.id:
            return await send(interaction, "❌ Дарить можно только роли, которые ты создал сам (роли из кейсов дарить нельзя).")
        if role.permissions.administrator:
            return await send(interaction, "❌ Роли с правами администратора дарить нельзя.")
        view = GiftTargetView(self.bot, self.user, role.id)
        await send(interaction, f"Кому подарить роль {role.mention}? (у тебя она останется)", view=view)

    @discord.ui.button(label="Удалить роль", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.require_selected(interaction)
        if not item:
            return
        cr = await self.bot.db.get_custom_role(item["role_id"])
        if not cr or cr["creator_id"] != self.user.id:
            return await send(interaction, "❌ Удалить можно только роль, которую создал ты.")
        await send(
            interaction,
            "⚠️ Роль удалится у всех, кто её купил, без возврата денег. Точно удалить?",
            view=ConfirmDeleteView(self.bot, self.user, item["role_id"]),
        )

    @discord.ui.button(label="Инвентарь", emoji="🧳", style=discord.ButtonStyle.success, row=2)
    async def open_items(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ItemsView(self.bot, self.guild, self.user)
        embed = await view.build()
        await send(interaction, embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Управление ролью", emoji="🛠", style=discord.ButtonStyle.primary, row=2)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        roles = [cr for cr in await self.bot.db.get_custom_roles_of(self.user.id) if self.guild.get_role(cr["role_id"])]
        if not roles:
            return await send(interaction, "У тебя нет созданных ролей. Создать роль можно в /shop.", ephemeral=True)
        view = ManageRoleView(self.bot, self.guild, self.user, roles)
        await send(interaction, embed=await view.build(), view=view, ephemeral=True)


# ---------------------------------------------------------------- управление созданной ролью

class ManageRoleView(TempView):
    def __init__(self, bot, guild, user, roles):
        super().__init__(owner_id=user.id, timeout=config.MENU_LIFETIME)
        self.bot, self.guild, self.user = bot, guild, user
        self.role_id = roles[0]["role_id"]
        options = [discord.SelectOption(label=(guild.get_role(cr["role_id"]).name)[:100], value=str(cr["role_id"]))
                   for cr in roles[:25]]
        self.picker = discord.ui.Select(placeholder="Выбери свою роль", options=options, row=0)
        self.picker.callback = self.on_pick
        self.add_item(self.picker)

    async def on_pick(self, interaction: discord.Interaction):
        self.role_id = int(self.picker.values[0])
        await interaction.response.edit_message(embed=await self.build(), view=self)

    async def build(self) -> discord.Embed:
        db = self.bot.db
        role = self.guild.get_role(self.role_id)
        cr = await db.get_custom_role(self.role_id)
        for opt in self.picker.options:
            opt.default = opt.value == str(self.role_id)
        if role is None or cr is None:
            return discord.Embed(description="❌ Эта роль больше не существует.", color=discord.Color.red())
        st = await db.role_sale_stats(self.role_id, self.user.id)
        embed = discord.Embed(title=f"🛠 Управление ролью «{role.name}»",
                              color=role.colour if role.colour.value else discord.Color.blurple())
        embed.add_field(name="Роль", value=role.mention)
        embed.add_field(name="Цвет", value=f"#{role.colour.value:06x}" if role.colour.value else "без цвета")
        embed.add_field(name="Цена", value=await db.fmt(cr["price"]))
        embed.add_field(name="🛒 Купили (человек)", value=str(st["buyers"]))
        embed.add_field(name="🧾 Продаж и продлений", value=str(st["sales"]))
        embed.add_field(name="💰 Заработано", value=await db.fmt(st["earned"]))
        embed.add_field(name="Лот действует до", value=f"<t:{cr['expires_at']}:D>")
        embed.add_field(name="В магазине", value="да" if cr["listed"] else "снята с продажи")
        embed.set_footer(text="Заработок считается после комиссии. Продажи учитываются с момента обновления бота.")
        return embed

    @discord.ui.button(label="Изменить название и цвет", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = self.guild.get_role(self.role_id)
        cr = await self.bot.db.get_custom_role(self.role_id)
        if role is None or cr is None or cr["creator_id"] != self.user.id:
            return await send(interaction, "❌ Эта роль не найдена.", ephemeral=True)
        await interaction.response.send_modal(EditRoleModal(self, role))


class EditRoleModal(discord.ui.Modal, title="Изменить роль"):
    def __init__(self, manage_view: ManageRoleView, role: discord.Role):
        super().__init__()
        self.manage_view = manage_view
        self.role = role
        self.role_name = discord.ui.TextInput(label="Название", default=role.name, max_length=32)
        self.colour = discord.ui.TextInput(label="Цвет #RRGGBB (0 = без цвета)", required=False, max_length=7,
                                           default=f"#{role.colour.value:06x}" if role.colour.value else "")
        self.add_item(self.role_name)
        self.add_item(self.colour)

    async def on_submit(self, interaction: discord.Interaction):
        name = " ".join(self.role_name.value.split())
        if not name:
            return await interaction.response.send_message("❌ Название не может быть пустым.", ephemeral=True)
        raw = self.colour.value.strip()
        colour = discord.Colour.default() if raw in ("", "0") else parse_colour(raw)
        if colour is None:
            return await interaction.response.send_message("❌ Цвет нужно указать так: #ff66aa", ephemeral=True)
        try:
            await self.role.edit(name=name, colour=colour, reason=f"Автор изменил роль ({interaction.user})")
        except discord.HTTPException:
            return await interaction.response.send_message("❌ Не удалось изменить роль (проверь права бота).",
                                                           ephemeral=True)
        await interaction.response.edit_message(embed=await self.manage_view.build(), view=self.manage_view)


class Inventory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Если администрация сняла с участника «свою» роль или роль с правами из кейса,
        она пропадает и из хранилища — надеть её обратно будет нельзя."""
        removed = set(before.roles) - set(after.roles)
        if not removed:
            return
        db = self.bot.db
        for role in removed:
            item = await db.get_item(after.id, role.id)
            if item and item["equipped"] and item["source"] in ("own", "perm"):
                await db.remove_item(after.id, role.id)

    @app_commands.command(name="inventory", description="Твой инвентарь: роли, предметы из кейсов, твои роли")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = InventoryView(self.bot, interaction.guild, interaction.user)
        file = await build_inventory_card(self.bot, interaction.user)
        view.top_embed = discord.Embed(color=0x2B2D31).set_image(url="attachment://inventory.png")
        embed = await view.build()
        view.message = await interaction.followup.send(embeds=view.embeds(embed), file=file, view=view, wait=True)


async def setup(bot):
    await bot.add_cog(Inventory(bot))
