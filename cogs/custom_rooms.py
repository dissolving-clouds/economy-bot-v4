"""Кастомные голосовые комнаты.

Покупка — в /shop (пункт «Кастомная голосовая комната»). Управление — командой /room
(работает в каналах из config.ROOM_CHANNEL_IDS). Управлять комнатой может только владелец
и администраторы бота (config.ADMIN_ROLE_ID).

Доступ в комнату даётся через роль комнаты: владелец приглашает участника, в #корзина
приходит приглашение, и роль выдаётся только после нажатия «Принять».
"""
import asyncio
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from checks import is_bot_admin
from utils import place_new_role, mark_self_delete, TempView, add_role, delete_later, fetch_member, remove_role, role_is_manageable, send

NAME_MAX = 32


# ================================================================ helpers

def parse_colour(raw: str) -> discord.Colour | None:
    raw = raw.strip()
    if re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        raw = "#" + raw
    try:
        return discord.Colour.from_str(raw)
    except ValueError:
        return None


async def _category(bot, guild: discord.Guild) -> discord.CategoryChannel | None:
    cat_id = await bot.db.get_int("croom_category_id")
    found = guild.get_channel(cat_id) if cat_id else None
    if isinstance(found, discord.CategoryChannel):
        return found
    join = guild.get_channel(config.LOVE_JOIN_CHANNEL_ID)  # запасной вариант — рядом с «Войти»
    return join.category if join is not None else None


def _overwrites(guild: discord.Guild, role: discord.Role, owner: discord.Member | None) -> dict:
    """Видят все, заходить могут только владельцы роли комнаты."""
    access = discord.PermissionOverwrite(
        view_channel=True, connect=True, speak=True, stream=True, use_voice_activation=True
    )
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        role: access,
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, move_members=True
        ),
    }
    if owner is not None:
        ow[owner] = access
    return ow


async def _create_channel(bot, guild, role, owner, name):
    return await guild.create_voice_channel(
        name=name[:100],
        category=await _category(bot, guild),
        overwrites=_overwrites(guild, role, owner),
        reason=f"Кастомная комната {owner or ''}",
    )


async def ensure_room(bot, guild: discord.Guild, room):
    """Проверяет, что канал и роль комнаты существуют.
    Если администратор удалил канал или роль — комната удаляется целиком (и из базы),
    возвращается (None, None, None)."""
    role = guild.get_role(room["role_id"]) if room["role_id"] else None
    channel = guild.get_channel(room["channel_id"]) if room["channel_id"] else None
    if role is None or channel is None:
        try:
            await delete_room(bot, guild, room, "Канал или роль комнаты удалены администратором")
        except discord.HTTPException:
            await bot.db.delete_croom(room["owner_id"])
        return None, None, None
    return channel, role, room


async def delete_room(bot, guild: discord.Guild, room, reason: str):
    """Удаляет канал, роль и запись в базе."""
    channel = guild.get_channel(room["channel_id"]) if room["channel_id"] else None
    if channel is not None:
        try:
            mark_self_delete(channel.id)
            await channel.delete(reason=reason)
        except discord.NotFound:
            pass
    role = guild.get_role(room["role_id"]) if room["role_id"] else None
    if role is not None:
        try:
            mark_self_delete(role.id)
            await role.delete(reason=reason)
        except discord.NotFound:
            pass
    await bot.db.delete_croom(room["owner_id"])


# ================================================================ покупка (вызывается из /shop)

class BuyRoomModal(discord.ui.Modal, title="Покупка голосовой комнаты"):
    room_name = discord.ui.TextInput(label="Название комнаты", max_length=NAME_MAX, placeholder="например: Уютная")
    color = discord.ui.TextInput(label="Цвет роли доступа (необязательно)", required=False,
                                 max_length=7, placeholder="#ff66aa")

    def __init__(self, bot, item_id: int | None = None, items_view=None):
        """item_id — комната из кейса (бесплатно, предмет списывается из инвентаря)."""
        super().__init__(title="Кастомная комната из кейса" if item_id else "Покупка голосовой комнаты")
        self.bot = bot
        self.item_id = item_id
        self.items_view = items_view

    async def on_submit(self, interaction: discord.Interaction):
        await buy_custom_room(self.bot, interaction, self.room_name.value, self.color.value, item_id=self.item_id)
        if self.items_view is not None:
            await self.items_view.refresh(interaction)


async def buy_custom_room(bot, interaction: discord.Interaction, name: str, color: str = "",
                          item_id: int | None = None):
    db, guild, member = bot.db, interaction.guild, interaction.user

    async def say(text=None, **kw):
        await send(interaction, text, **kw)

    name = " ".join(name.split())[:NAME_MAX]
    if not name:
        return await say("❌ Название не может быть пустым.")
    colour = discord.Colour.default()
    if color.strip():
        colour = parse_colour(color)
        if colour is None:
            return await say("❌ Цвет нужно указать так: #ff66aa")
    if await db.get_croom(member.id):
        return await say("❌ У тебя уже есть кастомная комната. Управлять ей — командой /room.")

    days = await db.get_int("croom_days")
    item = None
    if item_id:  # комната из кейса — бесплатно и на ограниченный срок (настройка case_croom_days, по умолчанию 7 дн.)
        days = await db.get_int("case_croom_days")
        price = 0
        item = await db.get_case_item(item_id)
        if item is None or item["user_id"] != member.id or item["kind"] != "croom" \
                or not await db.take_case_item(item_id, member.id):
            return await say("❌ Этого предмета уже нет в инвентаре.")
    else:
        price = await db.get_int("croom_price")
        if not await db.try_remove(member.id, price):
            balance = await db.get_balance(member.id)
            return await say(f"❌ Не хватает монет. Цена комнаты: {await db.fmt(price)}, у тебя: {await db.fmt(balance)}.")

    async def refund():
        if item is not None:
            await db.restore_case_item(item)
        elif price:
            await db.add_balance(member.id, price)

    back = "Предмет возвращён в инвентарь." if item is not None else "Монеты возвращены."
    await interaction.response.defer()
    try:
        role = await guild.create_role(name=name, colour=colour, reason=f"Кастомная комната {member}")
    except discord.HTTPException:
        await refund()
        return await say(f"❌ Не удалось создать роль (нужно право «Управление ролями»). {back}")
    await place_new_role(guild, role)  # ставим над ролью из config.NEW_ROLES_ABOVE_ID
    try:
        channel = await _create_channel(bot, guild, role, member, name)
    except discord.HTTPException:
        try:
            mark_self_delete(role.id)
            await role.delete(reason="Не удалось создать канал")
        except discord.HTTPException:
            pass
        await refund()
        return await say(f"❌ Не удалось создать голосовой канал (нужно право «Управление каналами»). {back}")

    await add_role(guild, member.id, role, "Владелец кастомной комнаты")
    now = int(time.time())
    expires = now + days * 86400 if days > 0 else 0
    await db.add_croom(member.id, channel.id, role.id, name, now, expires)

    embed = discord.Embed(title="🔊 Новая голосовая комната!", color=colour if colour.value else discord.Color.blurple())
    if item is not None:
        embed.description = f"{member.mention} открыл(а) комнату {channel.mention} из кейса!"
    else:
        embed.description = f"{member.mention} купил(а) комнату {channel.mention} за **{await db.fmt(price)}**."
    embed.add_field(name="Роль доступа", value=role.mention)
    embed.add_field(name="Срок", value=f"до <t:{expires}:D>" if expires else "навсегда")
    embed.set_footer(text="Управление комнатой (название, цвет, приглашения) — команда /room")
    await say(embed=embed)


# ================================================================ приглашения (кнопки работают и после перезапуска)

INVITE_RE = r"(?P<owner>[0-9]+):(?P<target>[0-9]+):(?P<exp>[0-9]+)"


async def _invite_finish(interaction: discord.Interaction, text: str, color: discord.Color):
    embed = discord.Embed(description=text, color=color)
    await interaction.response.edit_message(content=None, embed=embed, view=None)
    delete_later(interaction.message)


class InviteAccept(discord.ui.DynamicItem[discord.ui.Button], template=r"croom:acc:" + INVITE_RE):
    def __init__(self, owner_id: int, target_id: int, exp: int):
        super().__init__(discord.ui.Button(
            label="Принять", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"croom:acc:{owner_id}:{target_id}:{exp}",
        ))
        self.owner_id, self.target_id, self.exp = owner_id, target_id, exp

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["owner"]), int(match["target"]), int(match["exp"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("⛔ Это приглашение адресовано не тебе.", ephemeral=True)
        if time.time() > self.exp:
            return await _invite_finish(interaction, "⌛ Приглашение истекло.", discord.Color.greyple())
        bot = interaction.client
        room = await bot.db.get_croom(self.owner_id)
        role = interaction.guild.get_role(room["role_id"]) if room else None
        if room is None or role is None:
            return await _invite_finish(interaction, "❌ Этой комнаты больше нет.", discord.Color.red())
        if role in interaction.user.roles:
            return await _invite_finish(interaction, f"✅ {interaction.user.mention}, у тебя уже есть доступ.",
                                        discord.Color.green())
        if not await add_role(interaction.guild, interaction.user.id, role, "Приглашение в кастомную комнату"):
            return await interaction.response.send_message(
                "❌ Не удалось выдать роль — роль бота должна быть выше роли комнаты.", ephemeral=True
            )
        channel = interaction.guild.get_channel(room["channel_id"])
        where = channel.mention if channel else f"**{room['name']}**"
        await _invite_finish(
            interaction,
            f"✅ {interaction.user.mention} принял(а) приглашение <@{self.owner_id}> и получил(а) доступ в {where}!",
            discord.Color.green(),
        )


class InviteDecline(discord.ui.DynamicItem[discord.ui.Button], template=r"croom:dec:" + INVITE_RE):
    def __init__(self, owner_id: int, target_id: int, exp: int):
        super().__init__(discord.ui.Button(
            label="Отказаться", style=discord.ButtonStyle.danger,
            custom_id=f"croom:dec:{owner_id}:{target_id}:{exp}",
        ))
        self.owner_id, self.target_id, self.exp = owner_id, target_id, exp

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["owner"]), int(match["target"]), int(match["exp"]))

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in (self.target_id, self.owner_id) and not is_bot_admin(interaction.user):
            return await interaction.response.send_message("⛔ Это приглашение адресовано не тебе.", ephemeral=True)
        text = (f"💨 {interaction.user.mention} отказался(лась) от приглашения в комнату <@{self.owner_id}>."
                if uid == self.target_id else "🚫 Приглашение отменено.")
        await _invite_finish(interaction, text, discord.Color.greyple())


# ================================================================ панель /room

async def build_room_embed(bot, guild: discord.Guild, room, role, channel) -> discord.Embed:
    colour = role.colour if role and role.colour.value else discord.Color.blurple()
    embed = discord.Embed(title=f"🔊 Комната «{room['name']}»", color=colour)
    embed.add_field(name="Владелец", value=f"<@{room['owner_id']}>")
    embed.add_field(name="Канал", value=channel.mention if channel else "—")
    embed.add_field(name="Срок", value=f"до <t:{room['expires_at']}:D>" if room["expires_at"] else "навсегда")
    if role:
        hex_colour = f"#{role.colour.value:06x}" if role.colour.value else "без цвета"
        embed.add_field(name="Роль доступа", value=f"{role.mention} · `{hex_colour}`", inline=False)
        guests = [m for m in role.members if m.id != room["owner_id"] and not m.bot]
        if guests:
            shown = ", ".join(m.mention for m in guests[:20])
            if len(guests) > 20:
                shown += f" и ещё {len(guests) - 20}"
        else:
            shown = "пока никого — пригласи кнопкой «Пригласить»"
        embed.add_field(name=f"Есть доступ ({len(guests)})", value=shown, inline=False)
    embed.set_footer(text="Приглашение приходит в #корзина, участник должен нажать «Принять».")
    return embed


class RoomPanel(TempView):
    """Панель управления комнатой. Нажимать могут владелец и администраторы бота."""

    def __init__(self, bot, owner_id: int):
        super().__init__(timeout=config.ROOM_PANEL_LIFETIME)
        self.bot = bot
        self.owner_id_room = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id_room or is_bot_admin(interaction.user):
            return True
        await interaction.response.send_message("⛔ Управлять комнатой может только её владелец.", ephemeral=True)
        return False

    async def room(self, interaction: discord.Interaction):
        """Свежая запись + канал + роль. None — если комнаты уже нет."""
        room = await self.bot.db.get_croom(self.owner_id_room)
        if room is None:
            await interaction.response.send_message("❌ Этой комнаты больше нет.", ephemeral=True)
            return None
        channel, role, room = await ensure_room(self.bot, interaction.guild, room)
        if room is None:
            await interaction.response.send_message("❌ Эта комната удалена администрацией.", ephemeral=True)
            return None
        return room, channel, role

    async def refresh(self, guild: discord.Guild):
        room = await self.bot.db.get_croom(self.owner_id_room)
        if room is None or self.message is None:
            return
        role = guild.get_role(room["role_id"])
        channel = guild.get_channel(room["channel_id"])
        try:
            await self.message.edit(embed=await build_room_embed(self.bot, guild, room, role, channel), view=self)
        except discord.HTTPException:
            pass

    # ---- row 0
    @discord.ui.button(label="Название", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        got = await self.room(interaction)
        if got:
            await interaction.response.send_modal(RenameModal(self, got[0]["name"]))

    @discord.ui.button(label="Цвет роли", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def colour(self, interaction: discord.Interaction, button: discord.ui.Button):
        got = await self.room(interaction)
        if got:
            role = got[2]
            current = f"#{role.colour.value:06x}" if role.colour.value else ""
            await interaction.response.send_modal(ColourModal(self, current))

    # ---- row 1
    @discord.ui.button(label="Пригласить", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.room(interaction):
            view = discord.ui.View(timeout=120)
            view.add_item(InviteSelect(self))
            await interaction.response.send_message("Кого пригласить в комнату?", view=view, ephemeral=True)

    @discord.ui.button(label="Забрать доступ", emoji="➖", style=discord.ButtonStyle.secondary, row=1)
    async def revoke(self, interaction: discord.Interaction, button: discord.ui.Button):
        got = await self.room(interaction)
        if not got:
            return
        room, channel, role = got
        guests = await room_guests(interaction.guild, role, room["owner_id"])
        if not guests:
            return await interaction.response.send_message("Доступа пока ни у кого нет.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(RevokeSelect(self, guests[:25]))
        more = f"\nПоказаны первые 25 из {len(guests)}." if len(guests) > 25 else ""
        await interaction.response.send_message(f"У кого забрать доступ? (только участники этой комнаты){more}",
                                                view=view, ephemeral=True)

    # ---- row 2
    @discord.ui.button(label="Продлить", emoji="⏳", style=discord.ButtonStyle.secondary, row=2)
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        got = await self.room(interaction)
        if not got:
            return
        room = got[0]
        db = self.bot.db
        if room["expires_at"] == 0:
            return await interaction.response.send_message("♾ Комната и так навсегда — продлевать не нужно.",
                                                           ephemeral=True)
        price = await db.get_int("croom_price")
        days = await db.get_int("croom_days")
        if not await db.try_remove(interaction.user.id, price):
            return await interaction.response.send_message(f"❌ Не хватает монет. Продление: {await db.fmt(price)}.",
                                                           ephemeral=True)
        now = int(time.time())
        new_exp = 0 if days <= 0 else max(room["expires_at"], now) + days * 86400
        await db.update_croom(room["owner_id"], expires_at=new_exp)
        text = "♾ Комната теперь навсегда!" if new_exp == 0 else f"✅ Комната продлена до <t:{new_exp}:D>."
        await interaction.response.send_message(f"{text} Списано {await db.fmt(price)}.", ephemeral=True)
        await self.refresh(interaction.guild)

    @discord.ui.button(label="Удалить комнату", emoji="🗑", style=discord.ButtonStyle.danger, row=2)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.room(interaction):
            await interaction.response.send_message(
                "⚠️ Удалить комнату навсегда? Канал и роль удалятся, монеты **не** возвращаются.",
                view=ConfirmDelete(self), ephemeral=True,
            )

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def reload(self, interaction: discord.Interaction, button: discord.ui.Button):
        got = await self.room(interaction)
        if got:
            room, channel, role = got
            await interaction.response.edit_message(
                embed=await build_room_embed(self.bot, interaction.guild, room, role, channel), view=self
            )


async def room_guests(guild: discord.Guild, role: discord.Role, owner_id: int) -> list[discord.Member]:
    """Только участники комнаты: у кого есть роль доступа (без владельца и ботов)."""
    if not guild.chunked:  # подгружаем участников, если кэш ещё не полный
        try:
            await guild.chunk()
        except discord.HTTPException:
            pass
    guests = [m for m in role.members if m.id != owner_id and not m.bot]
    return sorted(guests, key=lambda m: m.display_name.lower())


class RenameModal(discord.ui.Modal, title="Название комнаты"):
    def __init__(self, panel: RoomPanel, current: str):
        super().__init__()
        self.panel = panel
        self.name_input = discord.ui.TextInput(label="Новое название", default=current, max_length=NAME_MAX)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = " ".join(self.name_input.value.split())[:NAME_MAX]
        if not name:
            return await interaction.response.send_message("❌ Название не может быть пустым.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot, guild = self.panel.bot, interaction.guild
        room = await bot.db.get_croom(self.panel.owner_id_room)
        if room is None:
            return await interaction.followup.send("❌ Этой комнаты больше нет.", ephemeral=True)
        channel, role = guild.get_channel(room["channel_id"]), guild.get_role(room["role_id"])
        try:
            if role is not None:
                await role.edit(name=name, reason=f"Переименование комнаты ({interaction.user})")
            if channel is not None:
                # Discord разрешает переименовывать канал только 2 раза за 10 минут — не ждём бесконечно
                await asyncio.wait_for(channel.edit(name=name, reason=f"Переименование ({interaction.user})"), 10)
        except asyncio.TimeoutError:
            await bot.db.update_croom(room["owner_id"], name=name)
            await interaction.followup.send(
                "⏳ Роль переименована, а канал Discord разрешает переименовывать только 2 раза за 10 минут. "
                "Попробуй ещё раз чуть позже.", ephemeral=True)
            return await self.panel.refresh(guild)
        except discord.HTTPException:
            return await interaction.followup.send("❌ Не удалось переименовать. Проверь права бота.", ephemeral=True)
        await bot.db.update_croom(room["owner_id"], name=name)
        await interaction.followup.send(f"✅ Комната теперь называется **{name}**.", ephemeral=True)
        await self.panel.refresh(guild)


class ColourModal(discord.ui.Modal, title="Цвет роли доступа"):
    def __init__(self, panel: RoomPanel, current: str):
        super().__init__()
        self.panel = panel
        self.value_input = discord.ui.TextInput(label="Цвет в формате #RRGGBB (0 = без цвета)",
                                                default=current, max_length=7, placeholder="#ff66aa")
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        colour = discord.Colour.default() if raw in ("", "0") else parse_colour(raw)
        if colour is None:
            return await interaction.response.send_message("❌ Цвет нужно указать так: #ff66aa", ephemeral=True)
        bot, guild = self.panel.bot, interaction.guild
        room = await bot.db.get_croom(self.panel.owner_id_room)
        role = guild.get_role(room["role_id"]) if room else None
        if role is None:
            return await interaction.response.send_message("❌ Роль комнаты не найдена — нажми «Обновить».",
                                                           ephemeral=True)
        try:
            await role.edit(colour=colour, reason=f"Цвет комнаты ({interaction.user})")
        except discord.HTTPException:
            return await interaction.response.send_message(
                "❌ Не удалось поменять цвет — роль бота должна быть выше роли комнаты.", ephemeral=True)
        await interaction.response.send_message("🎨 Цвет роли обновлён.", ephemeral=True)
        await self.panel.refresh(guild)


class InviteSelect(discord.ui.UserSelect):
    def __init__(self, panel: RoomPanel):
        super().__init__(placeholder="Выбери участника")
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        bot, guild = self.panel.bot, interaction.guild
        target = self.values[0]
        room = await bot.db.get_croom(self.panel.owner_id_room)
        role = guild.get_role(room["role_id"]) if room else None
        if room is None or role is None:
            return await interaction.response.send_message("❌ Комната не найдена — нажми «Обновить».", ephemeral=True)
        member = await fetch_member(guild, target.id)
        if member is None or member.bot:
            return await interaction.response.send_message("❌ Выбери участника сервера (не бота).", ephemeral=True)
        if member.id == room["owner_id"]:
            return await interaction.response.send_message("❌ Владелец и так имеет доступ.", ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message(f"✅ У {member.mention} уже есть доступ.", ephemeral=True)

        cart = bot.get_channel(config.CART_CHANNEL_ID)
        if cart is None:
            return await interaction.response.send_message("❌ Не найден канал для приглашений.", ephemeral=True)
        exp = int(time.time()) + config.ROOM_INVITE_HOURS * 3600
        view = discord.ui.View(timeout=None)
        view.add_item(InviteAccept(room["owner_id"], member.id, exp))
        view.add_item(InviteDecline(room["owner_id"], member.id, exp))
        embed = discord.Embed(
            title="🔊 Приглашение в голосовую комнату",
            description=(
                f"<@{room['owner_id']}> приглашает {member.mention} в комнату **{room['name']}**.\n"
                f"Нажми «Принять», чтобы получить роль {role.mention} и доступ в канал."
            ),
            color=role.colour if role.colour.value else discord.Color.blurple(),
        )
        embed.add_field(name="Действует до", value=f"<t:{exp}:R>")
        try:
            await cart.send(content=member.mention, embed=embed, view=view,
                            allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                            delete_after=config.ROOM_INVITE_HOURS * 3600)
        except discord.HTTPException:
            return await interaction.response.send_message("❌ Не удалось отправить приглашение.", ephemeral=True)
        await interaction.response.edit_message(
            content=f"📨 Приглашение для {member.mention} отправлено в <#{config.CART_CHANNEL_ID}>.", view=None
        )


class RevokeSelect(discord.ui.Select):
    def __init__(self, panel: RoomPanel, guests: list[discord.Member]):
        options = [discord.SelectOption(label=m.display_name[:100], description=f"@{m.name}"[:100], value=str(m.id))
                   for m in guests]
        super().__init__(placeholder="Выбери участника", options=options)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        bot, guild = self.panel.bot, interaction.guild
        room = await bot.db.get_croom(self.panel.owner_id_room)
        role = guild.get_role(room["role_id"]) if room else None
        if room is None or role is None:
            return await interaction.response.send_message("❌ Комната не найдена.", ephemeral=True)
        uid = int(self.values[0])
        if uid == room["owner_id"] or not any(m.id == uid for m in role.members):
            return await interaction.response.send_message("❌ У этого участника нет доступа в комнату.", ephemeral=True)
        if not await remove_role(guild, uid, role, f"Доступ забрал {interaction.user}"):
            return await interaction.response.send_message("❌ Не удалось забрать роль.", ephemeral=True)
        member = guild.get_member(uid)
        if member and member.voice and member.voice.channel and member.voice.channel.id == room["channel_id"]:
            try:
                await member.move_to(None, reason="Доступ в комнату забран")
            except discord.HTTPException:
                pass
        await interaction.response.edit_message(content=f"✅ Доступ у <@{uid}> забран.", view=None)
        await self.panel.refresh(guild)


class ConfirmDelete(discord.ui.View):
    def __init__(self, panel: RoomPanel):
        super().__init__(timeout=60)
        self.panel = panel

    @discord.ui.button(label="Да, удалить", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = self.panel.bot
        room = await bot.db.get_croom(self.panel.owner_id_room)
        if room is None:
            return await interaction.response.edit_message(content="Комнаты уже нет.", view=None)
        await interaction.response.defer()
        try:
            await delete_room(bot, interaction.guild, room, f"Комнату удалил {interaction.user}")
        except discord.HTTPException:
            return await interaction.edit_original_response(content="❌ Не удалось удалить канал или роль.", view=None)
        await interaction.edit_original_response(content="🗑 Комната удалена.", view=None)
        self.panel.stop()
        try:
            await self.panel.message.delete()
        except (discord.HTTPException, AttributeError):
            pass

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Отменено.", view=None)


class AdminRoomPick(discord.ui.Select):
    """Для администраторов: выбор комнаты из списка."""

    def __init__(self, cog, rooms, guild):
        options = []
        for r in rooms[:25]:
            owner = guild.get_member(r["owner_id"])
            options.append(discord.SelectOption(
                label=r["name"][:100], value=str(r["owner_id"]),
                description=f"Владелец: {owner.display_name if owner else r['owner_id']}"[:100],
            ))
        super().__init__(placeholder="Выбери комнату", options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.open_panel(interaction, int(self.values[0]))


# ================================================================ cog

class CustomRooms(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(InviteAccept, InviteDecline)
        self.expire.start()

    async def cog_unload(self):
        self.expire.cancel()

    async def open_panel(self, interaction: discord.Interaction, owner_id: int):
        room = await self.bot.db.get_croom(owner_id)
        if room is None:
            return await send(interaction, "❌ Комната не найдена.", ephemeral=True)
        channel, role, room = await ensure_room(self.bot, interaction.guild, room)
        if room is None:
            return await send(interaction, "❌ Канал или роль этой комнаты были удалены администрацией — "
                                           "комната больше не существует.", ephemeral=True)
        if not role_is_manageable(interaction.guild, role):
            warn = "\n⚠️ Роль комнаты выше роли бота — приглашения и цвет работать не будут."
        else:
            warn = ""
        panel = RoomPanel(self.bot, owner_id)
        embed = await build_room_embed(self.bot, interaction.guild, room, role, channel)
        await send(interaction, warn or None, embed=embed, view=panel, ephemeral=True)

    @app_commands.command(name="room", description="Панель управления твоей голосовой комнатой")
    @app_commands.describe(member="Чьей комнатой управлять (только для администраторов)")
    @app_commands.guild_only()
    async def room(self, interaction: discord.Interaction, member: discord.Member | None = None):
        db = self.bot.db
        admin = is_bot_admin(interaction.user)
        if member is not None and member.id != interaction.user.id:
            if not admin:
                return await send(interaction, "⛔ Управлять чужой комнатой могут только администраторы.",
                                  ephemeral=True)
            if not await db.get_croom(member.id):
                return await send(interaction, f"❌ У {member.mention} нет кастомной комнаты.", ephemeral=True)
            return await self.open_panel(interaction, member.id)

        if await db.get_croom(interaction.user.id):
            return await self.open_panel(interaction, interaction.user.id)

        if admin:
            rooms = await db.all_crooms()
            if not rooms:
                return await send(interaction, "На сервере пока нет кастомных комнат.", ephemeral=True)
            view = discord.ui.View(timeout=120)
            view.add_item(AdminRoomPick(self, rooms, interaction.guild))
            return await send(interaction, "🛠 Какой комнатой управлять?", view=view, ephemeral=True)

        price = await db.get_int("croom_price")
        await send(interaction, f"У тебя нет голосовой комнаты. Купить её можно в /shop "
                                f"за {await db.fmt(price)}.", ephemeral=True)

    # ---------- автоудаление просроченных комнат ----------
    @tasks.loop(minutes=5)
    async def expire(self):
        try:
            guild = self.bot.main_guild()
            if guild is None:
                return
            now = int(time.time())
            for room in await self.bot.db.all_crooms():
                if room["expires_at"] and room["expires_at"] <= now:
                    try:
                        await delete_room(self.bot, guild, room, "Срок кастомной комнаты истёк")
                    except discord.HTTPException:
                        continue
                    cart = self.bot.get_channel(config.CART_CHANNEL_ID)
                    if cart:
                        try:
                            await cart.send(f"⌛ <@{room['owner_id']}>, срок твоей комнаты **{room['name']}** "
                                            f"закончился, и она удалена.")
                        except discord.HTTPException:
                            pass
        except Exception:
            logging.exception("Ошибка при удалении просроченных кастомных комнат")

    @expire.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(CustomRooms(bot))
