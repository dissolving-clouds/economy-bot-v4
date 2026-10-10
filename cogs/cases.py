"""Кейсы: выпадают за сообщения и голосовой онлайн, открываются в /case.

Призы попадают в инвентарь (/inventory → «Инвентарь»):
  • монеты — копятся в «монетах инвентаря», забираются на баланс кнопкой «Забрать монеты»;
  • роль / роль с правами — «Перенести в хранилище», дальше надевается/снимается как обычная роль;
  • кастомная комната — «Использовать» → окно с названием и цветом (как при покупке);
  • любовная комната — «Использовать» → выбор партнёра → предложение в #корзина.

Шансы и призы настраиваются в /panel_case (config.CASE_ADMIN_IDS).
Роли из кейсов и срок роли с правами можно менять и из /panel (кнопка «Роли из кейсов»).
"""
import asyncio
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from cards import RARITY_NAMES, build_case_card, fmt_n
from checks import is_bot_admin
from database import RARITIES
from utils import (TempView, add_role, delete_later, fetch_member, gif_embed, gif_file, remove_role,
                   role_is_manageable, send)

RARITY_EMOJI = {"common": "⚪", "rare": "🔵", "legendary": "🟡"}
RARITY_COLOR = {
    "common": discord.Color.light_grey(),
    "rare": discord.Color.blue(),
    "legendary": discord.Color.gold(),
}
KIND_NAMES = {
    "coins": "Монеты",
    "role": "Роль",
    "permrole": "Роль с правами",
    "croom": "Кастомная комната",
    "loveroom": "Любовная комната",
}
KIND_EMOJI = {"coins": "💰", "role": "🎭", "permrole": "⚡", "croom": "🔊", "loveroom": "💞"}
PERM_WARNING = ("⚠️ **Роль с правами** выдана для удовольствия, а не для наказаний. Не злоупотребляй правами — "
                "администрация может снять эту роль в любой момент без возврата.")


def is_case_admin(user) -> bool:
    return user.id in config.CASE_ADMIN_IDS


def days_text(days: int) -> str:
    return "навсегда" if days <= 0 else f"{days} дн."


def prize_label(kind: str, role: discord.Role | None = None, days: int = 0, amount: int = 0) -> str:
    if kind == "coins":
        return f"{fmt_n(amount)} монет"
    if kind in ("role", "permrole"):
        name = role.name if role else "удалённая роль"
        prefix = "Роль с правами" if kind == "permrole" else "Роль"
        return f"{prefix} «{name}» ({days_text(days)})"
    return KIND_NAMES.get(kind, kind)


def loot_label(entry, guild: discord.Guild) -> str:
    kind = entry["kind"]
    if kind == "coins":
        lo, hi = entry["amount_min"], entry["amount_max"]
        return f"Монеты {fmt_n(lo)}–{fmt_n(hi)}" if lo != hi else f"Монеты {fmt_n(lo)}"
    if kind in ("role", "permrole"):
        role = guild.get_role(entry["role_id"])
        name = f"@{role.name}" if role else f"удалённая роль {entry['role_id']}"
        days = "срок из настроек" if kind == "permrole" else days_text(entry["days"])
        return f"{KIND_NAMES[kind]} {name} ({days})"
    return KIND_NAMES.get(kind, kind)


def item_label(item, guild: discord.Guild) -> str:
    role = guild.get_role(item["role_id"]) if item["role_id"] else None
    return f"{KIND_EMOJI.get(item['kind'], '🎁')} {prize_label(item['kind'], role, item['days'])}"


def _valid_loot(loot, guild: discord.Guild):
    """Убирает призы-роли, которых больше нет на сервере (чтобы не выдать «пустышку»)."""
    out = []
    for e in loot:
        if e["weight"] <= 0:
            continue
        if e["kind"] in ("role", "permrole") and guild.get_role(e["role_id"]) is None:
            continue
        out.append(e)
    return out


async def already_has_role(db, user: discord.Member, role_id: int, now: int) -> bool:
    if any(r.id == role_id for r in user.roles):
        return True
    item = await db.get_item(user.id, role_id)
    if item and (item["expires_at"] == 0 or item["expires_at"] > now):
        return True
    return await db.has_case_role_item(user.id, role_id)


async def grant_prize(bot, guild: discord.Guild, user: discord.Member, rarity: str, entry, chance: float):
    """Кладёт приз в инвентарь. Возвращает (подпись, доп. текст)."""
    db = bot.db
    now = int(time.time())
    kind = entry["kind"]
    extra = ""
    if kind == "coins":
        lo, hi = sorted((entry["amount_min"], entry["amount_max"]))
        amount = random.randint(lo, hi)
        await db.add_wallet(user.id, amount)
        label = prize_label("coins", amount=amount)
        extra = "Монеты лежат в /inventory → «Инвентарь» → «Забрать монеты»."
    elif kind in ("role", "permrole") and await already_has_role(db, user, entry["role_id"], now):
        # роль уже есть (надета, в хранилище или ждёт в инвентаре) — даём замену монетами
        role = guild.get_role(entry["role_id"])
        amount = await db.get_int("case_dup_coins")
        await db.add_wallet(user.id, amount)
        label = f"{fmt_n(amount)} монет (замена за роль «{role.name if role else '—'}»)"
        extra = (f"У тебя уже есть эта роль, поэтому вместо неё — **{fmt_n(amount)} монет**.\n"
                 f"Они лежат в /inventory → «Инвентарь» → «Забрать монеты».")
    else:
        role = guild.get_role(entry["role_id"]) if entry["role_id"] else None
        days = entry["days"]
        if kind == "permrole":
            days = await db.get_int("case_perm_role_days")
        await db.add_case_item(user.id, kind, entry["role_id"], days, rarity, now)
        label = prize_label(kind, role, days)
        if kind in ("role", "permrole"):
            extra = "Забери её в /inventory → «Инвентарь» → «Перенести в хранилище», потом надень."
        else:
            extra = "Используй её в /inventory → «Инвентарь» → «Использовать»."
            if kind == "croom":
                cdays = await db.get_int("case_croom_days")
                extra += f"\nКомната из кейса действует **{days_text(cdays)}**."
        sell = await db.get_int(f"sell_{kind}")
        if sell > 0:
            extra += f"\nНе нужен предмет? Обменяй его на **{fmt_n(sell)} монет** там же — кнопка «Обменять на монеты»."
        if kind == "permrole":
            extra += "\n\n" + PERM_WARNING
    await db.add_case_log(now, user.id, rarity, kind, label, chance)
    return label, extra


# ================================================================ /case

class CaseView(TempView):
    def __init__(self, cog, user: discord.Member, cases: dict):
        super().__init__(owner_id=user.id, timeout=config.MENU_LIFETIME)
        self.cog = cog
        self.user = user
        self.buttons = {}
        styles = {"common": discord.ButtonStyle.secondary, "rare": discord.ButtonStyle.primary,
                  "legendary": discord.ButtonStyle.success}
        for rarity in RARITIES:
            btn = discord.ui.Button(style=styles[rarity], emoji=RARITY_EMOJI[rarity])
            btn.callback = self._make_callback(rarity)
            self.buttons[rarity] = btn
            self.add_item(btn)
        self.update_labels(cases)

    def update_labels(self, cases: dict):
        for rarity, btn in self.buttons.items():
            n = cases[rarity]["count"]
            btn.label = f"Открыть {RARITY_NAMES[rarity].lower()} ({n})"
            btn.disabled = n <= 0

    def _make_callback(self, rarity):
        async def callback(interaction: discord.Interaction):
            await self.cog.open_case(interaction, rarity, self)
        return callback


class Cases(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_cooldown: dict[int, float] = {}
        self.opening: set[int] = set()

    # ---------- выпадение ----------
    async def roll_drop(self, user_id: int, source: str):
        """source: msg / voice. Выпадает максимум один кейс за раз, начиная с самого редкого."""
        db = self.bot.db
        for rarity in ("legendary", "rare", "common"):
            p = await db.get_float(f"case_{source}_{rarity}")
            if p > 0 and random.random() * 100 < p:
                await db.add_cases(user_id, rarity, 1)
                await db.add_case_drop(int(time.time()), user_id, rarity, source, 1)  # для /case_history
                await self._notify_drop(user_id, rarity)
                return

    async def _notify_drop(self, user_id: int, rarity: str):
        channel = self.bot.get_channel(config.CART_CHANNEL_ID)
        if channel is None:
            return
        embed = discord.Embed(
            description=f"🎁 <@{user_id}>, тебе выпал **{RARITY_NAMES[rarity].lower()} кейс**! Открыть — /case",
            color=RARITY_COLOR[rarity],
        )
        kwargs = {"delete_after": config.CASE_NOTIFY_LIFETIME} if config.CASE_NOTIFY_LIFETIME > 0 else {}
        try:
            await channel.send(content=f"<@{user_id}>", embed=embed,
                               allowed_mentions=discord.AllowedMentions(users=True), **kwargs)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        now = time.monotonic()
        if now - self.msg_cooldown.get(message.author.id, 0) < config.CASE_MESSAGE_COOLDOWN:
            return
        self.msg_cooldown[message.author.id] = now
        try:
            await self.roll_drop(message.author.id, "msg")
        except Exception:
            logging.exception("Ошибка выпадения кейса (сообщения)")

    async def add_voice(self, user_ids: list[int]):
        """Вызывается из voice_tick раз в минуту."""
        try:
            for uid in set(user_ids):
                await self.roll_drop(uid, "voice")
        except Exception:
            logging.exception("Ошибка выпадения кейса (голосовые)")

    # ---------- открытие ----------
    async def open_case(self, interaction: discord.Interaction, rarity: str, view: CaseView | None = None):
        db, guild, user = self.bot.db, interaction.guild, interaction.user
        if user.id in self.opening:
            return await send(interaction, "⏳ Подожди, предыдущий кейс ещё открывается.", ephemeral=True)
        loot = _valid_loot(await db.get_loot(rarity), guild)
        total = sum(e["weight"] for e in loot)
        if not loot or total <= 0:
            return await send(interaction, "📭 Этот кейс пока пуст — администрация ещё не настроила призы.",
                              ephemeral=True)
        if not await db.take_case(user.id, rarity):
            return await send(interaction, f"❌ У тебя нет кейсов «{RARITY_NAMES[rarity]}».", ephemeral=True)

        self.opening.add(user.id)
        try:
            entry = random.choices(loot, weights=[e["weight"] for e in loot], k=1)[0]
            chance = entry["weight"] / total
            label, extra = await grant_prize(self.bot, guild, user, rarity, entry, chance)

            # гифка, потом результат
            title = f"{RARITY_EMOJI[rarity]} {RARITY_NAMES[rarity]} кейс"
            gif_key = f"case_{rarity}" if gif_file(f"case_{rarity}") else "case"  # своя гифка для каждой редкости
            embed = gif_embed(gif_key, title, f"{user.mention} открывает кейс…", RARITY_COLOR[rarity])
            file = gif_file(gif_key)
            if file:
                await interaction.response.send_message(embed=embed, file=file)
            else:
                await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()
            await asyncio.sleep(config.GIF_SECONDS.get(gif_key, config.GIF_SECONDS.get("case", 3)))

            result = discord.Embed(
                title=f"{title} открыт!",
                description=f"{user.mention} получает: **{label}**\nШанс: {chance * 100:.2f}%\n\n{extra}",
                color=RARITY_COLOR[rarity],
            )
            try:
                await interaction.edit_original_response(embed=result, attachments=[])
            except discord.HTTPException:
                message = await interaction.followup.send(embed=result, wait=True)
            lifetime = getattr(config, "CASE_RESULT_LIFETIME", 90)
            if lifetime > 0:
                delete_later(message, lifetime)
        finally:
            self.opening.discard(user.id)

        if view is not None and view.message is not None:  # обновляем картинку и кнопки /case
            try:
                view.update_labels(await db.get_cases(user.id))
                await view.message.edit(attachments=[await build_case_card(self.bot, user)], view=view)
            except discord.HTTPException:
                pass

    @app_commands.command(name="case", description="Твои кейсы: открыть и посмотреть")
    @app_commands.guild_only()
    async def case(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = interaction.user
        file = await build_case_card(self.bot, user)
        view = CaseView(self, user, await self.bot.db.get_cases(user.id))
        view.message = await interaction.followup.send(file=file, view=view, wait=True)

    @app_commands.command(name="case_history", description="История кейсов: когда выпадали и что из них выпало")
    @app_commands.describe(member="Чью историю показать (по умолчанию твою)")
    @app_commands.guild_only()
    async def case_history(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        view = CaseHistoryView(self.bot, interaction.user.id, member)
        embed = await view.build()
        view.message = await send(interaction, embed=embed, view=view)

    # ---------- панель кейсов ----------
    @app_commands.command(name="panel_case", description="Настройка кейсов: шансы и призы")
    @app_commands.guild_only()
    async def panel_case(self, interaction: discord.Interaction):
        if not is_case_admin(interaction.user):
            return await send(interaction, "⛔ Панель кейсов тебе недоступна.", ephemeral=True)
        await open_case_panel(self.bot, interaction, roles_only=False)


# ================================================================ /case_history

SOURCE_NAMES = {"msg": "💬 за сообщение", "voice": "🎙 за голосовой онлайн", "admin": "🛠 от администрации"}


class CaseHistoryView(TempView):
    """Две вкладки: «Получение кейсов» и «Что выпало», по 10 записей на странице."""

    PAGE = 10

    def __init__(self, bot, owner_id: int, member: discord.Member):
        super().__init__(owner_id=owner_id, timeout=config.MENU_LIFETIME)
        self.bot = bot
        self.member = member
        self.tab = "loot"  # loot — что выпало из кейсов, drops — получение кейсов
        self.page = 0
        self.total = 0

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.PAGE))

    async def build(self) -> discord.Embed:
        db = self.bot.db
        uid = self.member.id
        if self.tab == "loot":
            self.total = await db.count_case_log(uid)
            self.page = min(self.page, self.pages - 1)
            rows = await db.get_case_log(uid, self.PAGE, self.page * self.PAGE)
            lines = [f"<t:{r['ts']}:f> {RARITY_EMOJI.get(r['rarity'], '🎁')} {RARITY_NAMES.get(r['rarity'], r['rarity'])} → "
                     f"{KIND_EMOJI.get(r['kind'], '🎁')} **{r['label']}** · шанс {r['chance'] * 100:.2f}%"
                     for r in rows]
            title = f"🎁 Что выпало из кейсов — {self.member.display_name}"
            empty = "Кейсы ещё не открывались. Открыть — /case"
            opened = await db.total_opened(uid)
            head = f"Всего открыто кейсов: **{opened}**"
        else:
            self.total = await db.count_case_drops(uid)
            self.page = min(self.page, self.pages - 1)
            rows = await db.get_case_drops(uid, self.PAGE, self.page * self.PAGE)
            lines = []
            for r in rows:
                n = f"{r['count']} × " if r["count"] > 1 else ""
                lines.append(f"<t:{r['ts']}:f> {RARITY_EMOJI.get(r['rarity'], '🎁')} {n}**{RARITY_NAMES.get(r['rarity'], r['rarity'])} кейс** "
                             f"· {SOURCE_NAMES.get(r['source'], r['source'])}")
            title = f"📥 Получение кейсов — {self.member.display_name}"
            empty = "Кейсы пока не выпадали. Они выпадают за сообщения и голосовой онлайн."
            totals = await db.case_drop_totals(uid)
            head = "Получено: " + " · ".join(f"{RARITY_EMOJI[r]} {totals.get(r, 0)}" for r in RARITIES)
        embed = discord.Embed(title=title, color=discord.Color.gold(),
                              description=head + "\n\n" + ("\n".join(lines) if lines else empty))
        embed.set_footer(text=f"Страница {self.page + 1}/{self.pages} · записей: {self.total}")
        self.loot_tab.disabled = self.tab == "loot"
        self.drops_tab.disabled = self.tab == "drops"
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.pages - 1
        return embed

    async def _show(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self.build(), view=self)

    @discord.ui.button(label="Что выпало", emoji="🎁", style=discord.ButtonStyle.primary, row=0)
    async def loot_tab(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tab, self.page = "loot", 0
        await self._show(interaction)

    @discord.ui.button(label="Получение кейсов", emoji="📥", style=discord.ButtonStyle.primary, row=0)
    async def drops_tab(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tab, self.page = "drops", 0
        await self._show(interaction)

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="Дальше", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self._show(interaction)


# ================================================================ инвентарь предметов (используется в /inventory)

class ItemsView(TempView):
    """Предметы из кейсов и монеты инвентаря. Открывается кнопкой «Инвентарь» в /inventory."""

    def __init__(self, bot, guild: discord.Guild, user: discord.Member):
        super().__init__(owner_id=user.id, timeout=config.MENU_LIFETIME)
        self.bot = bot
        self.guild = guild
        self.user = user
        self.selected: int | None = None
        self.select_item: discord.ui.Select | None = None
        self.role_selected: int | None = None
        self.role_select: discord.ui.Select | None = None

    async def case_roles(self):
        """Роли из кейсов, которые уже лежат в хранилище (их можно надевать и скрывать)."""
        rows = await self.bot.db.get_inventory(self.user.id, int(time.time()))
        return [r for r in rows if r["source"] in ("case", "perm") and self.guild.get_role(r["role_id"])]

    async def build(self) -> discord.Embed:
        db = self.bot.db
        items = await db.get_case_items(self.user.id)
        wallet = await db.get_wallet(self.user.id)
        stored = await self.case_roles()
        if self.select_item is not None:
            self.remove_item(self.select_item)
            self.select_item = None
        if self.role_select is not None:
            self.remove_item(self.role_select)
            self.role_select = None
        role_options = []
        for r in stored[:25]:
            role = self.guild.get_role(r["role_id"])
            state = "надета" if r["equipped"] else "скрыта"
            term = "навсегда" if not r["expires_at"] else "срок до " + time.strftime("%d.%m", time.localtime(r["expires_at"]))
            role_options.append(discord.SelectOption(
                label=role.name[:100], value=str(role.id), emoji="👕" if r["equipped"] else "🙈",
                description=f"{state} · {term}" + (" · с правами" if r["source"] == "perm" else ""),
                default=(role.id == self.role_selected),
            ))
        if role_options:
            rsel = discord.ui.Select(placeholder="Роли из кейсов: надеть / скрыть…", options=role_options, row=2)

            async def on_role_select(interaction: discord.Interaction):
                self.role_selected = int(rsel.values[0])
                await interaction.response.edit_message(embed=await self.build(), view=self)

            rsel.callback = on_role_select
            self.role_select = rsel
            self.add_item(rsel)
        options = []
        for it in items[:25]:
            options.append(discord.SelectOption(
                label=item_label(it, self.guild)[:100], value=str(it["id"]),
                description=f"Из кейса: {RARITY_NAMES.get(it['rarity'], '')}"[:100],
                default=(it["id"] == self.selected),
            ))
        if options:
            sel = discord.ui.Select(placeholder="Выбери предмет…", options=options, row=0)

            async def on_select(interaction: discord.Interaction):
                self.selected = int(sel.values[0])
                await interaction.response.edit_message(embed=await self.build(), view=self)

            sel.callback = on_select
            self.select_item = sel
            self.add_item(sel)

        lines = [f"💰 **Монеты в инвентаре:** {await db.fmt(wallet)}", ""]
        if items:
            prices = {k: await db.get_int(f"sell_{k}") for k in ("role", "permrole", "croom", "loveroom")}
            for i, it in enumerate(items[:25]):
                price = prices.get(it["kind"], 0)
                tail = f" · обмен: {fmt_n(price)} монет" if price > 0 else ""
                lines.append(f"{i + 1}. {item_label(it, self.guild)}{tail}")
            if len(items) > 25:
                lines.append(f"…и ещё {len(items) - 25}")
        else:
            lines.append("Предметов нет. Они выпадают из кейсов — /case")
        if stored:
            lines += ["", "**🎭 Роли из кейсов в хранилище:**"]
            for r in stored[:25]:
                state = "👕 надета" if r["equipped"] else "🙈 скрыта"
                term = "навсегда" if not r["expires_at"] else f"до <t:{r['expires_at']}:D>"
                lines.append(f"<@&{r['role_id']}> — {state}, {term}")
        embed = discord.Embed(title="📦 Инвентарь", description="\n".join(lines)[:4000],
                              color=discord.Color.blurple())
        embed.set_footer(text="Роли: выбери → «Перенести в хранилище». Комнаты: выбери → «Использовать». "
                              "Ненужный предмет: выбери → «Обменять на монеты».")
        return embed

    async def refresh(self, interaction: discord.Interaction):
        embed = await self.build()
        try:
            if interaction.response.is_done():
                await self.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except (discord.HTTPException, AttributeError):
            pass

    async def selected_item(self, interaction: discord.Interaction, kinds: tuple):
        if self.selected is None:
            await interaction.response.send_message("Сначала выбери предмет в списке.", ephemeral=True)
            return None
        item = await self.bot.db.get_case_item(self.selected)
        if item is None or item["user_id"] != self.user.id:
            await interaction.response.send_message("❌ Этого предмета уже нет.", ephemeral=True)
            return None
        if item["kind"] not in kinds:
            what = "перенести в хранилище можно только роли" if "role" in kinds else "использовать можно только комнаты"
            await interaction.response.send_message(f"❌ Этот предмет так не работает: {what}.", ephemeral=True)
            return None
        return item

    @discord.ui.button(label="Перенести в хранилище", emoji="📦", style=discord.ButtonStyle.primary, row=1)
    async def to_storage(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.selected_item(interaction, ("role", "permrole"))
        if not item:
            return
        db = self.bot.db
        role = self.guild.get_role(item["role_id"])
        if role is None:
            await db.take_case_item(item["id"], self.user.id)
            await interaction.response.send_message("❌ Эта роль удалена с сервера — предмет убран.", ephemeral=True)
            return await self.refresh(interaction)
        if not await db.take_case_item(item["id"], self.user.id):
            return await interaction.response.send_message("❌ Этого предмета уже нет.", ephemeral=True)
        now = int(time.time())
        days = item["days"]
        exp = 0 if days <= 0 else now + days * 86400
        existing = await db.get_item(self.user.id, role.id)
        if existing and (existing["expires_at"] == 0 or existing["expires_at"] > now):
            if existing["expires_at"] == 0:
                exp = 0
            elif exp:
                exp = max(existing["expires_at"], now) + days * 86400
            await db.set_item_expiry(self.user.id, role.id, exp)
        else:
            await db.add_item(self.user.id, role.id, exp, 0, "perm" if item["kind"] == "permrole" else "case")
        term = "навсегда" if exp == 0 else f"до <t:{exp}:D>"
        text = (f"✅ Роль **{role.name}** перенесена в хранилище ({term}). "
                f"Надевай и скрывай её здесь же: список «Роли из кейсов» → «Надеть роль» / «Скрыть роль». Дарить её нельзя.")
        if item["kind"] == "permrole":
            text += "\n\n" + PERM_WARNING
        await interaction.response.send_message(text, ephemeral=True)
        self.selected = None
        await self.refresh(interaction)

    @discord.ui.button(label="Использовать", emoji="✨", style=discord.ButtonStyle.success, row=1)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.selected_item(interaction, ("croom", "loveroom"))
        if not item:
            return
        db = self.bot.db
        if item["kind"] == "croom":
            from cogs.custom_rooms import BuyRoomModal  # здесь, чтобы не было циклического импорта
            if await db.get_croom(self.user.id):
                return await interaction.response.send_message(
                    "❌ У тебя уже есть кастомная комната (/room). Предмет останется в инвентаре.", ephemeral=True)
            self.selected = None
            return await interaction.response.send_modal(BuyRoomModal(self.bot, item_id=item["id"], items_view=self))
        # любовная комната
        if await db.get_room_of(self.user.id):
            return await interaction.response.send_message(
                "❌ У тебя уже есть любовная комната. Предмет останется в инвентаре.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(LovePartnerSelect(self.bot, item["id"]))
        await interaction.response.send_message("💞 **Выберите партнёра:**", view=view, ephemeral=True)

    async def selected_case_role(self, interaction: discord.Interaction):
        if self.role_selected is None:
            await interaction.response.send_message("Сначала выбери роль в списке «Роли из кейсов».", ephemeral=True)
            return None, None
        item = await self.bot.db.get_item(self.user.id, self.role_selected)
        role = self.guild.get_role(self.role_selected)
        if item is None or role is None or (item["expires_at"] and item["expires_at"] <= int(time.time())):
            await interaction.response.send_message("❌ Этой роли уже нет в хранилище.", ephemeral=True)
            return None, None
        return item, role

    @discord.ui.button(label="Надеть роль", emoji="👕", style=discord.ButtonStyle.primary, row=3)
    async def wear(self, interaction: discord.Interaction, button: discord.ui.Button):
        item, role = await self.selected_case_role(interaction)
        if not item:
            return
        if not await add_role(self.guild, self.user.id, role, "Надел роль из кейса"):
            return await interaction.response.send_message(
                "❌ Не удалось выдать роль (роль бота должна быть выше неё).", ephemeral=True)
        await self.bot.db.set_equipped(self.user.id, role.id, 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Скрыть роль", emoji="🙈", style=discord.ButtonStyle.secondary, row=3)
    async def hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        item, role = await self.selected_case_role(interaction)
        if not item:
            return
        if role.id in config.NO_HIDE_ROLE_IDS:
            return await interaction.response.send_message("⛔ Эту роль нельзя скрыть.", ephemeral=True)
        db = self.bot.db
        await db.set_equipped(self.user.id, role.id, 0)  # сначала помечаем — чтобы запись не удалилась
        if not await remove_role(self.guild, self.user.id, role, "Скрыл роль из кейса"):
            await db.set_equipped(self.user.id, role.id, item["equipped"])
            return await interaction.response.send_message("❌ Не удалось снять роль.", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Забрать монеты", emoji="💰", style=discord.ButtonStyle.secondary, row=1)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        wallet = await self.bot.db.get_wallet(self.user.id)
        if wallet <= 0:
            return await interaction.response.send_message("💰 Монет в инвентаре нет.", ephemeral=True)
        await interaction.response.send_modal(WithdrawModal(self, wallet))

    @discord.ui.button(label="Обменять на монеты", emoji="💱", style=discord.ButtonStyle.danger, row=1)
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.selected_item(interaction, ("role", "permrole", "croom", "loveroom"))
        if not item:
            return
        price = await self.bot.db.get_int(f"sell_{item['kind']}")
        if price <= 0:
            return await interaction.response.send_message(
                "❌ Этот предмет нельзя обменять на монеты (администрация отключила обмен).", ephemeral=True)
        await interaction.response.send_message(
            f"💱 Обменять **{item_label(item, self.guild)}** на **{await self.bot.db.fmt(price)}**?\n"
            f"Предмет исчезнет из инвентаря, вернуть его будет нельзя.",
            view=SellConfirm(self, item["id"]), ephemeral=True)


class SellConfirm(discord.ui.View):
    def __init__(self, items_view: "ItemsView", item_id: int):
        super().__init__(timeout=60)
        self.items_view = items_view
        self.item_id = item_id

    @discord.ui.button(label="Да, обменять", emoji="💱", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        iv = self.items_view
        db = iv.bot.db
        item = await db.get_case_item(self.item_id)
        if item is None or item["user_id"] != interaction.user.id:
            return await interaction.response.edit_message(content="❌ Этого предмета уже нет.", view=None)
        price = await db.get_int(f"sell_{item['kind']}")
        if price <= 0:
            return await interaction.response.edit_message(content="❌ Обмен этого предмета отключён.", view=None)
        if not await db.take_case_item(item["id"], interaction.user.id):  # защита от двойного нажатия
            return await interaction.response.edit_message(content="❌ Этого предмета уже нет.", view=None)
        label = item_label(item, iv.guild)
        balance = await db.add_balance(interaction.user.id, price)
        await interaction.response.edit_message(
            content=f"✅ {label} обменян(а) на **{await db.fmt(price)}**. Баланс: **{await db.fmt(balance)}**.",
            view=None)
        if iv.selected == item["id"]:
            iv.selected = None
        await iv.refresh(interaction)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Отменено.", view=None)


class WithdrawModal(discord.ui.Modal, title="Забрать монеты на баланс"):
    def __init__(self, items_view: ItemsView, wallet: int):
        super().__init__()
        self.items_view = items_view
        self.amount = discord.ui.TextInput(label=f"Сколько забрать? (доступно {wallet})", default=str(wallet),
                                           max_length=12)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        db = self.items_view.bot.db
        try:
            amount = int(self.amount.value.strip().replace(" ", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Нужно целое число больше нуля.", ephemeral=True)
        user = interaction.user
        if not await db.wallet_to_balance(user.id, amount):
            left = await db.get_wallet(user.id)
            return await interaction.response.send_message(f"❌ В инвентаре только {await db.fmt(left)}.",
                                                           ephemeral=True)
        balance = await db.get_balance(user.id)
        left = await db.get_wallet(user.id)
        await interaction.response.send_message(
            f"💰 {user.mention} забрал(а) **{await db.fmt(amount)}** из инвентаря на баланс.\n"
            f"Баланс: **{await db.fmt(balance)}** · осталось в инвентаре: **{await db.fmt(left)}**"
        )
        delete_later(await interaction.original_response())
        await self.items_view.refresh(interaction)


class LovePartnerSelect(discord.ui.UserSelect):
    def __init__(self, bot, item_id: int):
        super().__init__(placeholder="Выберите партнёра")
        self.bot = bot
        self.item_id = item_id

    async def callback(self, interaction: discord.Interaction):
        from cogs.rooms import post_love_offer
        db = self.bot.db
        owner = interaction.user
        partner = await fetch_member(interaction.guild, self.values[0].id)
        item = await db.get_case_item(self.item_id)
        if item is None or item["user_id"] != owner.id:
            return await interaction.response.edit_message(content="❌ Этого предмета уже нет.", view=None)
        if partner is None or partner.bot or partner.id == owner.id:
            return await interaction.response.send_message("❌ Выбери другого человека (не себя и не бота).",
                                                           ephemeral=True)
        if await db.get_room_of(owner.id) or await db.get_room_of(partner.id):
            return await interaction.response.send_message("❌ У тебя или у партнёра уже есть любовная комната.",
                                                           ephemeral=True)
        ok = await post_love_offer(self.bot, owner, partner, item_id=self.item_id)
        if not ok:
            return await interaction.response.send_message("❌ Не удалось отправить предложение.", ephemeral=True)
        await interaction.response.edit_message(
            content=f"💌 Предложение для {partner.mention} отправлено в <#{config.CART_CHANNEL_ID}>. "
                    f"Комната появится, когда партнёр согласится (предмет спишется только тогда).",
            view=None,
        )


# ================================================================ панель кейсов

async def build_case_panel_embed(bot, guild: discord.Guild, rarity: str, roles_only: bool) -> discord.Embed:
    db = bot.db
    title = "🎁 Роли из кейсов" if roles_only else "🎁 Панель кейсов"
    embed = discord.Embed(title=title, color=RARITY_COLOR[rarity])
    if not roles_only:
        lines = []
        for r in RARITIES:
            lines.append(f"{RARITY_EMOJI[r]} **{RARITY_NAMES[r]}**: "
                         f"сообщение — {await db.get_float(f'case_msg_{r}'):g}% · "
                         f"минута в голосовом — {await db.get_float(f'case_voice_{r}'):g}%")
        lines.append(f"\nЗащита от спама: не чаще 1 шанса в {config.CASE_MESSAGE_COOLDOWN} сек. за сообщения")
        embed.add_field(name="Шанс получить кейс", value="\n".join(lines), inline=False)
    embed.add_field(name="Срок роли с правами", value=days_text(await db.get_int("case_perm_role_days")))
    embed.add_field(name="Замена, если роль уже есть", value=f"{fmt_n(await db.get_int('case_dup_coins'))} монет")

    loot = await db.get_loot(rarity)
    total = sum(e["weight"] for e in loot if e["weight"] > 0)
    rows = []
    for e in loot:
        pct = (e["weight"] / total * 100) if total and e["weight"] > 0 else 0
        rows.append(f"`#{e['id']}` {KIND_EMOJI.get(e['kind'], '🎁')} {loot_label(e, guild)} — "
                    f"вес {e['weight']:g} (**{pct:.2f}%**)")
    embed.add_field(
        name=f"Призы: {RARITY_EMOJI[rarity]} {RARITY_NAMES[rarity]} кейс",
        value="\n".join(rows)[:1024] if rows else "Пусто — добавь призы кнопками ниже.",
        inline=False,
    )
    embed.set_footer(text="Шанс приза = его вес / сумма весов в этом кейсе. Вес можно дробным: 0.5")
    return embed


class CasePanel(discord.ui.View):
    def __init__(self, bot, roles_only: bool):
        super().__init__(timeout=900)
        self.bot = bot
        self.roles_only = roles_only
        self.rarity = "common"
        self.message = None
        self.add_item(RaritySelect(self))
        if roles_only:  # из /panel: только роли и срок роли с правами
            for item in (self.add_coins, self.add_croom, self.add_love, self.chances_msg, self.chances_voice,
                         self.give_case):
                self.remove_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_case_admin(interaction.user) or (self.roles_only and is_bot_admin(interaction.user)):
            return True
        await interaction.response.send_message("⛔ Нет доступа.", ephemeral=True)
        return False

    async def refresh(self, guild: discord.Guild):
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=await build_case_panel_embed(self.bot, guild, self.rarity, self.roles_only), view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Монеты", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_coins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LootModal(self, "coins"))

    @discord.ui.button(label="Роль", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_role_picker(self, interaction, "role")

    @discord.ui.button(label="Роль с правами", emoji="⚡", style=discord.ButtonStyle.success, row=1)
    async def add_perm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_role_picker(self, interaction, "permrole")

    @discord.ui.button(label="Кастомная комната", emoji="🔊", style=discord.ButtonStyle.success, row=1)
    async def add_croom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LootModal(self, "croom"))

    @discord.ui.button(label="Любовная комната", emoji="💞", style=discord.ButtonStyle.success, row=1)
    async def add_love(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LootModal(self, "loveroom"))

    @discord.ui.button(label="Изменить приз", emoji="✏️", style=discord.ButtonStyle.primary, row=2)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick_entry(interaction, "edit")

    @discord.ui.button(label="Удалить приз", emoji="🗑", style=discord.ButtonStyle.danger, row=2)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick_entry(interaction, "delete")

    @discord.ui.button(label="Шансы: сообщения", emoji="💬", style=discord.ButtonStyle.secondary, row=2)
    async def chances_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(await _chance_modal(self, "msg"))

    @discord.ui.button(label="Шансы: голосовой", emoji="🎙", style=discord.ButtonStyle.secondary, row=2)
    async def chances_voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(await _chance_modal(self, "voice"))

    @discord.ui.button(label="Срок роли с правами", emoji="⏳", style=discord.ButtonStyle.secondary, row=2)
    async def perm_days(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PermDaysModal(self, await self.bot.db.get_int("case_perm_role_days")))

    @discord.ui.button(label="Замена за дубль роли", emoji="🔁", style=discord.ButtonStyle.secondary, row=3)
    async def dup_coins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DupCoinsModal(self, await self.bot.db.get_int("case_dup_coins")))

    @discord.ui.button(label="Выдать кейс участнику", emoji="🎁", style=discord.ButtonStyle.success, row=3)
    async def give_case(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Кому выдать кейс?", view=GiveCaseView(self), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def reload(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=await build_case_panel_embed(self.bot, interaction.guild, self.rarity, self.roles_only), view=self)

    async def _pick_entry(self, interaction: discord.Interaction, action: str):
        loot = await self.bot.db.get_loot(self.rarity)
        if self.roles_only:
            loot = [e for e in loot if e["kind"] in ("role", "permrole")]
        if not loot:
            return await interaction.response.send_message("В этом кейсе нет подходящих призов.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(LootEntrySelect(self, loot[:25], action, interaction.guild))
        what = "изменить" if action == "edit" else "удалить"
        await interaction.response.send_message(f"Какой приз {what}?", view=view, ephemeral=True)


class RaritySelect(discord.ui.Select):
    def __init__(self, panel: CasePanel):
        options = [discord.SelectOption(label=f"{RARITY_NAMES[r]} кейс", value=r, emoji=RARITY_EMOJI[r],
                                        default=(r == "common")) for r in RARITIES]
        super().__init__(placeholder="Какой кейс настраиваем?", options=options, row=0)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        self.panel.rarity = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.panel.rarity
        await interaction.response.edit_message(
            embed=await build_case_panel_embed(self.panel.bot, interaction.guild, self.panel.rarity,
                                               self.panel.roles_only),
            view=self.panel,
        )


def _parse_weight(raw: str) -> float | None:
    try:
        w = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    return w if 0 < w <= 1_000_000 else None


class LootModal(discord.ui.Modal):
    """Добавление/изменение приза. entry=None — новый приз."""

    def __init__(self, panel: CasePanel, kind: str, role: discord.Role | None = None, entry=None):
        super().__init__(title=("Изменить приз" if entry else f"Новый приз: {KIND_NAMES[kind]}")[:45])
        self.panel, self.kind, self.role, self.entry = panel, kind, role, entry
        self.weight = discord.ui.TextInput(label="Вес (чем больше, тем чаще выпадает)",
                                           default=f"{entry['weight']:g}" if entry else "10", max_length=12)
        self.add_item(self.weight)
        self.amin = self.amax = self.days = None
        if kind == "coins":
            self.amin = discord.ui.TextInput(label="Монет от", default=str(entry["amount_min"]) if entry else "100",
                                             max_length=12)
            self.amax = discord.ui.TextInput(label="Монет до", default=str(entry["amount_max"]) if entry else "300",
                                             max_length=12)
            self.add_item(self.amin)
            self.add_item(self.amax)
        if kind == "role":
            self.days = discord.ui.TextInput(label="Срок роли в днях (0 = навсегда)",
                                             default=str(entry["days"]) if entry else "7", max_length=5)
            self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        db = self.panel.bot.db
        weight = _parse_weight(self.weight.value)
        if weight is None:
            return await interaction.response.send_message("❌ Вес — число больше 0 (можно дробное: 0.5).",
                                                           ephemeral=True)
        fields = {"weight": weight}
        try:
            if self.kind == "coins":
                lo, hi = int(self.amin.value), int(self.amax.value)
                if lo < 1 or hi < 1:
                    raise ValueError
                fields["amount_min"], fields["amount_max"] = min(lo, hi), max(lo, hi)
            if self.kind == "role":
                fields["days"] = int(self.days.value)
                if not 0 <= fields["days"] <= 3650:
                    raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Проверь числа: монеты ≥ 1, дни от 0 до 3650.",
                                                           ephemeral=True)
        if self.entry:
            await db.update_loot(self.entry["id"], **fields)
            text = "✅ Приз изменён."
        else:
            await db.add_loot(self.panel.rarity, self.kind, role_id=self.role.id if self.role else 0, **fields)
            text = f"✅ Приз добавлен в {RARITY_NAMES[self.panel.rarity].lower()} кейс."
        await interaction.response.send_message(text, ephemeral=True)
        await self.panel.refresh(interaction.guild)


def case_role_candidates(guild: discord.Guild, excluded: set[int]) -> list[discord.Role]:
    """Все роли, которые можно положить в кейс: бот может их выдать, нет права «Администратор»,
    не роль любовной/кастомной комнаты."""
    roles = []
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role.id in excluded or not role_is_manageable(guild, role) or role.permissions.administrator:
            continue
        roles.append(role)
    return roles


async def excluded_role_ids(db) -> set[int]:
    ids = {r["role_id"] for r in await db.all_rooms()} | {r["role_id"] for r in await db.all_crooms()}
    love_role = await db.get_int("room_role_id")
    if love_role:
        ids.add(love_role)
    ids.discard(0)
    return ids


class PickLootRole(discord.ui.View):
    """Список ВСЕХ подходящих ролей сервера по 25 штук на странице (листается кнопками)."""

    PAGE = 25

    def __init__(self, panel: CasePanel, kind: str, roles: list[discord.Role]):
        super().__init__(timeout=180)
        self.panel, self.kind, self.roles = panel, kind, roles
        self.page = 0
        self.select = None
        self.prev_btn = discord.ui.Button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
        self.next_btn = discord.ui.Button(label="Дальше", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
        self.prev_btn.callback = self.on_prev
        self.next_btn.callback = self.on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.render()

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.roles) // self.PAGE))

    def text(self) -> str:
        return (f"Выбери роль-приз ({'роль с правами' if self.kind == 'permrole' else 'обычная роль'}). "
                f"Всего подходящих ролей: **{len(self.roles)}** · страница {self.page + 1}/{self.pages}\n"
                f"Не показаны: роли с правом «Администратор», роли любовных и кастомных комнат, роли выше бота.")

    def render(self):
        if self.select is not None:
            self.remove_item(self.select)
        chunk = self.roles[self.page * self.PAGE:(self.page + 1) * self.PAGE]
        options = [discord.SelectOption(label=r.name[:100] or "без названия", value=str(r.id),
                                        description=f"участников: {len(r.members)}") for r in chunk]
        self.select = discord.ui.Select(placeholder=f"Роли — страница {self.page + 1}/{self.pages}",
                                        options=options, row=0)
        self.select.callback = self.on_pick
        self.add_item(self.select)
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.pages - 1

    async def on_prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.render()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def on_next(self, interaction: discord.Interaction):
        self.page = min(self.pages - 1, self.page + 1)
        self.render()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def on_pick(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.select.values[0]))
        if role is None or not role_is_manageable(interaction.guild, role) or role.permissions.administrator:
            return await interaction.response.send_message("❌ Эту роль нельзя положить в кейс.", ephemeral=True)
        await interaction.response.send_modal(LootModal(self.panel, self.kind, role=role))


async def open_role_picker(panel: CasePanel, interaction: discord.Interaction, kind: str):
    roles = case_role_candidates(interaction.guild, await excluded_role_ids(panel.bot.db))
    if not roles:
        return await interaction.response.send_message(
            "❌ Нет подходящих ролей. Роль бота должна стоять выше ролей, которые он выдаёт.", ephemeral=True)
    view = PickLootRole(panel, kind, roles)
    await interaction.response.send_message(view.text(), view=view, ephemeral=True)


# ---------- выдача кейса участнику

class GiveCaseView(discord.ui.View):
    def __init__(self, panel: CasePanel):
        super().__init__(timeout=180)
        self.panel = panel
        self.target: discord.Member | None = None
        for rarity in RARITIES:
            btn = discord.ui.Button(label=f"{RARITY_NAMES[rarity]} кейс", emoji=RARITY_EMOJI[rarity],
                                    style=discord.ButtonStyle.primary, row=1, disabled=True)
            btn.callback = self._make(rarity)
            self.add_item(btn)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Кому выдать кейс?", row=0)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = await fetch_member(interaction.guild, select.values[0].id)
        if member is None or member.bot:
            return await interaction.response.send_message("❌ Выбери участника сервера (не бота).", ephemeral=True)
        self.target = member
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
        await interaction.response.edit_message(
            content=f"Выдаём кейс участнику {member.mention}. Выбери редкость:", view=self)

    def _make(self, rarity):
        async def callback(interaction: discord.Interaction):
            if self.target is None:
                return await interaction.response.send_message("Сначала выбери участника.", ephemeral=True)
            await interaction.response.send_modal(GiveCaseModal(self.panel, self.target, rarity))
        return callback


class GiveCaseModal(discord.ui.Modal):
    def __init__(self, panel: CasePanel, target: discord.Member, rarity: str):
        super().__init__(title=f"Выдать: {RARITY_NAMES[rarity]} кейс")
        self.panel, self.target, self.rarity = panel, target, rarity
        self.count = discord.ui.TextInput(label="Сколько кейсов выдать (1–1000)", default="1", max_length=4)
        self.add_item(self.count)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.count.value)
            if not 1 <= n <= 1000:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Нужно число от 1 до 1000.", ephemeral=True)
        db = self.panel.bot.db
        await db.add_cases(self.target.id, self.rarity, n)
        await db.add_case_drop(int(time.time()), self.target.id, self.rarity, "admin", n)  # для /case_history
        have = (await db.get_cases(self.target.id))[self.rarity]["count"]
        await interaction.response.send_message(
            f"✅ {self.target.mention} получил(а) {n} × {RARITY_NAMES[self.rarity].lower()} кейс. Теперь у него: {have}.",
            ephemeral=True)
        channel = self.panel.bot.get_channel(config.CART_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                description=(f"🎁 {self.target.mention}, администрация выдала тебе "
                             f"**{n} × {RARITY_NAMES[self.rarity].lower()} кейс**! Открыть — /case"),
                color=RARITY_COLOR[self.rarity])
            kwargs = {"delete_after": config.CASE_NOTIFY_LIFETIME} if config.CASE_NOTIFY_LIFETIME > 0 else {}
            try:
                await channel.send(content=self.target.mention, embed=embed,
                                   allowed_mentions=discord.AllowedMentions(users=True), **kwargs)
            except discord.HTTPException:
                pass


class LootEntrySelect(discord.ui.Select):
    def __init__(self, panel: CasePanel, loot, action: str, guild: discord.Guild):
        options = [discord.SelectOption(label=f"#{e['id']} {loot_label(e, guild)}"[:100], value=str(e["id"]),
                                        description=f"вес {e['weight']:g}") for e in loot]
        super().__init__(placeholder="Выбери приз", options=options)
        self.panel, self.action = panel, action

    async def callback(self, interaction: discord.Interaction):
        db = self.panel.bot.db
        entry = await db.get_loot_entry(int(self.values[0]))
        if entry is None:
            return await interaction.response.edit_message(content="Этого приза уже нет.", view=None)
        if self.action == "delete":
            await db.delete_loot(entry["id"])
            await interaction.response.edit_message(content=f"🗑 Приз #{entry['id']} удалён.", view=None)
            return await self.panel.refresh(interaction.guild)
        await interaction.response.send_modal(LootModal(self.panel, entry["kind"], entry=entry))


class ChanceModal(discord.ui.Modal):
    def __init__(self, panel: CasePanel, source: str):
        super().__init__(title="Шанс кейса за сообщение, %" if source == "msg" else "Шанс кейса за минуту войса, %")
        self.panel, self.source = panel, source
        self.inputs = {}

    async def on_submit(self, interaction: discord.Interaction):
        db = self.panel.bot.db
        values = {}
        for rarity, field in self.inputs.items():
            try:
                v = float(field.value.strip().replace(",", "."))
            except ValueError:
                return await interaction.response.send_message("❌ Шанс — число от 0 до 100 (например 0.5).",
                                                               ephemeral=True)
            if not 0 <= v <= 100:
                return await interaction.response.send_message("❌ Шанс — число от 0 до 100.", ephemeral=True)
            values[rarity] = v
        for rarity, v in values.items():
            await db.set(f"case_{self.source}_{rarity}", f"{v:g}")
        await interaction.response.send_message("✅ Шансы сохранены.", ephemeral=True)
        await self.panel.refresh(interaction.guild)


class DupCoinsModal(discord.ui.Modal, title="Замена за дубль роли"):
    def __init__(self, panel: "CasePanel", current: int):
        super().__init__()
        self.panel = panel
        self.amount = discord.ui.TextInput(label="Сколько монет давать вместо роли", default=str(current),
                                           max_length=10)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if not 0 <= amount <= 100_000_000:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Нужно целое число от 0.", ephemeral=True)
        await self.panel.bot.db.set("case_dup_coins", str(amount))
        await interaction.response.send_message(f"✅ Замена за дубль роли: {amount} монет.", ephemeral=True)
        await self.panel.refresh(interaction.guild)


class PermDaysModal(discord.ui.Modal, title="Срок роли с правами"):
    def __init__(self, panel: CasePanel, current: int):
        super().__init__()
        self.panel = panel
        self.days = discord.ui.TextInput(label="Сколько дней действует (1–365)", default=str(current), max_length=3)
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            days = int(self.days.value)
            if not 1 <= days <= 365:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Нужно число от 1 до 365.", ephemeral=True)
        await self.panel.bot.db.set("case_perm_role_days", str(days))
        await interaction.response.send_message(f"✅ Роль с правами теперь выдаётся на {days} дн.", ephemeral=True)
        await self.panel.refresh(interaction.guild)


async def _chance_modal(panel: CasePanel, source: str) -> ChanceModal:
    modal = ChanceModal(panel, source)
    for rarity in RARITIES:
        current = await panel.bot.db.get_float(f"case_{source}_{rarity}")
        field = discord.ui.TextInput(label=f"{RARITY_NAMES[rarity]} кейс, %", default=f"{current:g}", max_length=10)
        modal.inputs[rarity] = field
        modal.add_item(field)
    return modal


async def open_case_panel(bot, interaction: discord.Interaction, roles_only: bool):
    panel = CasePanel(bot, roles_only)
    embed = await build_case_panel_embed(bot, interaction.guild, panel.rarity, roles_only)
    await send(interaction, embed=embed, view=panel, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Cases(bot))
