"""Любовные комнаты.

/marry — покупка (создаётся запись в БД и роль). Голосовой канал создаётся, только когда кто-то
из пары заходит в голосовой канал «Войти». Когда в комнате никого не осталось — удаляется только канал;
роль и запись в БД остаются до конца срока или до /devorce.
"""
import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils import mark_self_delete, add_role, delete_later, fetch_member, remove_role, send


async def extend_room(bot, user: discord.abc.User) -> tuple[bool, str]:
    """Продлевает комнату. Продлить может любой из пары: сначала тратится баланс комнаты,
    недостающее — с баланса нажавшего."""
    db = bot.db
    room = await db.get_room_of(user.id)
    if not room:
        return False, "❌ У тебя нет любовной комнаты."
    price = await db.get_int("room_price")
    days = await db.get_int("room_days")
    from_fund = min(room["fund"], price)
    rest = price - from_fund
    if from_fund and not await db.take_room_fund(room["owner_id"], from_fund):
        return False, "❌ Баланс комнаты изменился, попробуй ещё раз."
    if rest and not await db.try_remove(user.id, rest):
        if from_fund:
            await db.add_room_fund(room["owner_id"], from_fund)
        return False, (f"❌ Не хватает монет. Продление: {await db.fmt(price)} на {days} дн. "
                       f"(с баланса комнаты — {await db.fmt(from_fund)}, с тебя — {await db.fmt(rest)}).")
    new_expiry = max(room["expires_at"], int(time.time())) + days * 86400
    await db.set_room_expiry(room["owner_id"], new_expiry)
    parts = []
    if from_fund:
        parts.append(f"с баланса комнаты {await db.fmt(from_fund)}")
    if rest:
        parts.append(f"с {user.mention} {await db.fmt(rest)}")
    return True, f"✅ Любовная комната продлена до <t:{new_expiry}:D> ({', '.join(parts)})."


async def contribute_fund(bot, user: discord.abc.User, amount: int) -> tuple[bool, str]:
    """Скинуться в баланс любовной комнаты."""
    db = bot.db
    room = await db.get_room_of(user.id)
    if not room:
        return False, "❌ У тебя нет любовной комнаты."
    if amount <= 0:
        return False, "❌ Сумма должна быть больше нуля."
    if not await db.try_remove(user.id, amount):
        return False, f"❌ Не хватает монет. Твой баланс: {await db.fmt(await db.get_balance(user.id))}."
    await db.add_room_fund(room["owner_id"], amount)
    fresh = await db.get_room_of(user.id)
    return True, (f"💗 {user.mention} скинул(а) **{await db.fmt(amount)}** в баланс любовной комнаты. "
                  f"Теперь в копилке: **{await db.fmt(fresh['fund'])}**")


LOVE_RE = r"(?P<owner>[0-9]+):(?P<partner>[0-9]+):(?P<exp>[0-9]+):(?P<item>[0-9]+)"


async def post_love_offer(bot, owner: discord.Member, partner: discord.Member, item_id: int = 0,
                          channel=None) -> discord.Message | None:
    """Публикует предложение любовной комнаты в #корзина. item_id > 0 — комната из кейса (бесплатно).
    Кнопки работают config.LOVE_INVITE_HOURS часов, в том числе после перезапуска бота."""
    db = bot.db
    channel = channel or bot.get_channel(config.CART_CHANNEL_ID)
    if channel is None:
        return None
    days = await db.get_int("room_days")
    exp = int(time.time()) + config.LOVE_INVITE_HOURS * 3600
    if item_id:
        pay = "Комната выпала из кейса — платить ничего не нужно! 🎁"
    else:
        pay = f"Оплачивает {owner.mention}: **{await db.fmt(await db.get_int('room_price'))}**."
    embed = discord.Embed(
        title="💞 Предложение любовной комнаты",
        description=(
            f"{owner.mention} предлагает {partner.mention} общую комнату на {days} дн.\n{pay}\n\n"
            f"{partner.mention}, ты согласен(на)? Предложение действует до <t:{exp}:t> (<t:{exp}:R>)."
        ),
        color=discord.Color.magenta(),
    )
    view = discord.ui.View(timeout=None)
    view.add_item(LoveAccept(owner.id, partner.id, exp, item_id))
    view.add_item(LoveDecline(owner.id, partner.id, exp, item_id))
    try:
        return await channel.send(content=partner.mention, embed=embed, view=view,
                                  allowed_mentions=discord.AllowedMentions(users=True),
                                  delete_after=config.LOVE_INVITE_HOURS * 3600)
    except discord.HTTPException:
        return None


async def _love_finish(interaction: discord.Interaction, text: str, color: discord.Color):
    embed = discord.Embed(description=text, color=color)
    await interaction.response.edit_message(content=None, embed=embed, view=None)
    delete_later(interaction.message, 60)


class LoveAccept(discord.ui.DynamicItem[discord.ui.Button], template=r"love:acc:" + LOVE_RE):
    def __init__(self, owner_id: int, partner_id: int, exp: int, item_id: int):
        super().__init__(discord.ui.Button(
            label="Согласиться", emoji="💞", style=discord.ButtonStyle.success,
            custom_id=f"love:acc:{owner_id}:{partner_id}:{exp}:{item_id}",
        ))
        self.owner_id, self.partner_id, self.exp, self.item_id = owner_id, partner_id, exp, item_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["owner"]), int(match["partner"]), int(match["exp"]), int(match["item"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.partner_id:
            return await interaction.response.send_message("⛔ Это предложение адресовано не тебе.", ephemeral=True)
        if time.time() > self.exp:
            return await _love_finish(interaction, "⌛ Время вышло — предложение не принято.", discord.Color.greyple())
        cog = interaction.client.get_cog("Rooms")
        guild = interaction.guild
        owner = await fetch_member(guild, self.owner_id)
        if cog is None or owner is None:
            return await _love_finish(interaction, "❌ Автор предложения больше не на сервере.", discord.Color.red())
        await interaction.response.defer()
        ok, text = await cog.create_room(guild, owner, interaction.user, item_id=self.item_id)
        embed = discord.Embed(description=text, color=discord.Color.green() if ok else discord.Color.red())
        await interaction.edit_original_response(content=None, embed=embed, view=None)
        if not ok:
            delete_later(interaction.message, 60)


class LoveDecline(discord.ui.DynamicItem[discord.ui.Button], template=r"love:dec:" + LOVE_RE):
    def __init__(self, owner_id: int, partner_id: int, exp: int, item_id: int):
        super().__init__(discord.ui.Button(
            label="Отказаться", style=discord.ButtonStyle.danger,
            custom_id=f"love:dec:{owner_id}:{partner_id}:{exp}:{item_id}",
        ))
        self.owner_id, self.partner_id = owner_id, partner_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["owner"]), int(match["partner"]), int(match["exp"]), int(match["item"]))

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in (self.partner_id, self.owner_id):
            return await interaction.response.send_message("⛔ Это предложение адресовано не тебе.", ephemeral=True)
        if uid == self.partner_id:
            text = f"💔 <@{self.partner_id}> отказался(лась) от комнаты с <@{self.owner_id}>."
        else:
            text = "🚫 Предложение отменено."
        await _love_finish(interaction, text, discord.Color.greyple())


class Rooms(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.locks: dict[int, asyncio.Lock] = {}  # owner_id -> замок, чтобы не создать канал дважды
        self.busy: set[int] = set()               # каналы, которые только что созданы

    async def cog_load(self):
        self.bot.add_dynamic_items(LoveAccept, LoveDecline)
        self.cleanup.start()
        self.together.start()

    async def cog_unload(self):
        self.cleanup.cancel()
        self.together.cancel()

    @tasks.loop(seconds=60)
    async def together(self):
        """Считает, сколько пара просидела вместе в своей любовной комнате (оба в канале)."""
        try:
            for room in await self.bot.db.all_rooms():
                if not room["channel_id"]:
                    continue
                channel = self.bot.get_channel(room["channel_id"])
                if channel is None:
                    continue
                ids = {m.id for m in channel.members}
                if room["owner_id"] in ids and room["partner_id"] in ids:
                    await self.bot.db.add_room_together(room["owner_id"], 60)
        except Exception:
            logging.exception("Ошибка подсчёта времени вместе")

    @together.before_loop
    async def before_together(self):
        await self.bot.wait_until_ready()

    # ---------- покупка ----------
    async def create_room(self, guild: discord.Guild, owner: discord.Member, partner: discord.Member,
                          item_id: int = 0) -> tuple[bool, str]:
        """item_id > 0 — комната из кейса: бесплатно, предмет списывается из инвентаря владельца."""
        db = self.bot.db
        if await db.get_room_of(owner.id) or await db.get_room_of(partner.id):
            return False, "❌ У одного из вас уже есть любовная комната."

        price = await db.get_int("room_price")
        days = await db.get_int("room_days")
        if item_id:
            item = await db.get_case_item(item_id)
            if item is None or item["user_id"] != owner.id or item["kind"] != "loveroom" \
                    or not await db.take_case_item(item_id, owner.id):
                return False, "❌ Этот подарок из кейса уже использован."
            price = 0
        elif not await db.try_remove(owner.id, price):
            return False, f"❌ У {owner.mention} не хватает монет. Цена комнаты: {await db.fmt(price)}."

        role_id = await db.get_int("room_role_id")
        role = guild.get_role(role_id) if role_id else None
        roles_ok = True
        if role:
            first = await add_role(guild, owner.id, role, "Любовная комната")
            second = await add_role(guild, partner.id, role, "Любовная комната")
            roles_ok = first and second

        expires = int(time.time()) + days * 86400
        await db.add_room(owner.id, partner.id, expires, role.id if role else 0, int(time.time()))

        text = (
            f"💞 {owner.mention} и {partner.mention}, поздравляем — любовная комната куплена!\n"
            f"{'Из кейса 🎁' if item_id else f'Цена: {await db.fmt(price)}'} · действует до <t:{expires}:D> (<t:{expires}:R>).\n"
            f"Чтобы попасть в неё, зайдите в голосовой канал <#{config.LOVE_JOIN_CHANNEL_ID}>."
        )
        if role and roles_ok:
            text += f"\nВам выдана роль {role.mention}."
        elif role:
            text += "\n⚠️ Роль выдать не удалось — администратору нужно проверить, что роль бота выше неё."
        return True, text

    # ---------- голосовой канал комнаты ----------
    def _lock(self, owner_id: int) -> asyncio.Lock:
        return self.locks.setdefault(owner_id, asyncio.Lock())

    async def _target_category(self, guild: discord.Guild, join_channel) -> discord.CategoryChannel | None:
        if join_channel is not None and join_channel.category is not None:
            return join_channel.category  # та же категория, где стоит «Войти»
        cat_id = await self.bot.db.get_int("room_category_id")
        found = guild.get_channel(cat_id) if cat_id else None
        return found if isinstance(found, discord.CategoryChannel) else None

    async def _get_or_create_channel(self, guild, room, join_channel):
        """Возвращает голосовой канал комнаты, создавая его при необходимости."""
        async with self._lock(room["owner_id"]):
            room = await self.bot.db.get_room_of(room["owner_id"])  # свежие данные
            if room is None:
                return None
            if room["channel_id"]:
                channel = guild.get_channel(room["channel_id"])
                if channel is not None:
                    return channel

            owner = guild.get_member(room["owner_id"])
            partner = guild.get_member(room["partner_id"])
            pair = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True)
            # Видят все, заходить могут только двое
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, connect=True, manage_channels=True, move_members=True
                ),
            }
            for m in (owner, partner):
                if m is not None:
                    overwrites[m] = pair
            names = f" {config.LOVE_HEART} ".join(m.display_name for m in (owner, partner) if m) or "Любовная комната"
            channel = await guild.create_voice_channel(
                name=names[:100],
                category=await self._target_category(guild, join_channel),
                overwrites=overwrites,
                reason="Вход в любовную комнату",
            )
            self.busy.add(channel.id)
            await self.bot.db.set_room_channel(room["owner_id"], channel.id)
            return channel

    async def finish_room(self, room, reason: str) -> bool:
        """Полностью завершает комнату: канал, роли пары и запись в БД. False — если не получилось.
        Баланс комнаты возвращается паре пополам."""
        guild = self.bot.main_guild()
        fund = room["fund"] if "fund" in room.keys() else 0
        if room["channel_id"]:
            channel = self.bot.get_channel(room["channel_id"])
            if channel is not None:
                try:
                    mark_self_delete(channel.id)
                    await channel.delete(reason=reason)
                except discord.HTTPException:
                    return False
        if guild and room["role_id"]:
            role = guild.get_role(room["role_id"])
            for uid in (room["owner_id"], room["partner_id"]):
                await remove_role(guild, uid, role, reason)
        await self.bot.db.delete_room(room["owner_id"])
        if fund > 0:
            half = fund // 2
            await self.bot.db.add_balance(room["owner_id"], fund - half)
            await self.bot.db.add_balance(room["partner_id"], half)
        return True

    async def _notify(self, text: str):
        channel = self.bot.get_channel(config.CART_CHANNEL_ID)
        if channel:
            try:
                await channel.send(text, delete_after=config.MESSAGE_LIFETIME)
            except discord.HTTPException:
                pass

    # ---------- события голосовых каналов ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return
        try:
            # 1) вошёл в «Войти»
            if after.channel is not None and after.channel.id == config.LOVE_JOIN_CHANNEL_ID:
                await self._handle_join(member, after.channel)

            # 2) вышел из комнаты — если она опустела, удаляем только канал
            if before.channel is not None and before.channel.id != config.LOVE_JOIN_CHANNEL_ID:
                room = await self.bot.db.get_room_by_channel(before.channel.id)
                if room and not before.channel.members:
                    await self._drop_channel(room, before.channel)
        except Exception:
            logging.exception("Ошибка в обработке голосовых каналов комнат")

    async def _drop_channel(self, room, channel):
        self.busy.discard(channel.id)
        try:
            mark_self_delete(channel.id)
            await channel.delete(reason="Из любовной комнаты вышли все")
        except discord.NotFound:
            pass
        except discord.HTTPException:
            return
        await self.bot.db.set_room_channel(room["owner_id"], 0)

    async def _handle_join(self, member: discord.Member, join_channel: discord.VoiceChannel):
        guild = member.guild
        room = await self.bot.db.get_room_of(member.id)
        if not room or room["expires_at"] <= int(time.time()):
            try:
                await member.move_to(None, reason="Нет любовной комнаты")  # отключаем от «Войти»
            except discord.HTTPException:
                pass
            await self._notify(f"💔 {member.mention}, у тебя нет любовной комнаты. Купить её можно командой /marry.")
            return

        try:
            channel = await self._get_or_create_channel(guild, room, join_channel)
        except discord.HTTPException:
            await self._notify("❌ Не удалось создать комнату. Администратору нужно проверить права бота "
                               "(«Управление каналами» и «Перемещение участников»).")
            return
        if channel is None:
            return
        try:
            await member.move_to(channel, reason="Вход в любовную комнату")
        except discord.HTTPException:
            # участник успел выйти — если канал пустой, убираем его
            if not channel.members:
                fresh = await self.bot.db.get_room_of(member.id)
                if fresh:
                    await self._drop_channel(fresh, channel)
        finally:
            self.busy.discard(channel.id)

    # ---------- автоматическая очистка ----------
    @tasks.loop(minutes=5)
    async def cleanup(self):
        try:
            now = int(time.time())
            for room in await self.bot.db.all_rooms():
                if room["expires_at"] <= now:
                    price = await self.bot.db.get_int("room_price")
                    days = await self.bot.db.get_int("room_days")
                    if room["fund"] >= price and await self.bot.db.take_room_fund(room["owner_id"], price):
                        new_exp = max(room["expires_at"], now) + days * 86400  # автопродление из копилки
                        await self.bot.db.set_room_expiry(room["owner_id"], new_exp)
                        await self._notify(f"💞 <@{room['owner_id']}> и <@{room['partner_id']}>, любовная комната "
                                           f"продлена из баланса комнаты до <t:{new_exp}:D>.")
                        continue
                    await self.finish_room(room, "Срок любовной комнаты истёк")
                    continue
                if room["channel_id"] and room["channel_id"] not in self.busy:
                    channel = self.bot.get_channel(room["channel_id"])
                    if channel is None:
                        await self.bot.db.set_room_channel(room["owner_id"], 0)
                    elif not channel.members:
                        await self._drop_channel(room, channel)
        except Exception:
            logging.exception("Ошибка при очистке комнат")

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ---------- команды ----------
    @app_commands.command(name="marry", description="Предложить партнёру любовную комнату")
    @app_commands.describe(partner="С кем будет комната")
    @app_commands.guild_only()
    async def marry(self, interaction: discord.Interaction, partner: discord.Member):
        db = self.bot.db
        owner = interaction.user

        if partner.bot or partner.id == owner.id:
            return await send(interaction, "❌ Выбери другого человека (не себя и не бота).")
        if await db.get_room_of(owner.id) or await db.get_room_of(partner.id):
            return await send(interaction, "❌ У тебя или у партнёра уже есть любовная комната.")

        price = await db.get_int("room_price")
        days = await db.get_int("room_days")
        if await db.get_balance(owner.id) < price:
            return await send(interaction, f"❌ Не хватает монет. Цена комнаты: {await db.fmt(price)} на {days} дн.")

        await interaction.response.defer(ephemeral=True)
        msg = await post_love_offer(self.bot, owner, partner, channel=interaction.channel)
        if msg is None:
            return await interaction.followup.send("❌ Не удалось отправить предложение.", ephemeral=True)
        await interaction.followup.send(
            f"💌 Предложение отправлено! У {partner.mention} есть {config.LOVE_INVITE_HOURS} ч., чтобы ответить.",
            ephemeral=True)

    @app_commands.command(name="devorce", description="Удалить любовную комнату (без возврата денег)")
    async def devorce(self, interaction: discord.Interaction):
        room = await self.bot.db.get_room_of(interaction.user.id)
        if not room:
            return await send(interaction, "❌ У тебя нет любовной комнаты.")
        await interaction.response.defer()
        partner_id = room["partner_id"] if room["owner_id"] == interaction.user.id else room["owner_id"]
        if await self.finish_room(room, f"Расставание ({interaction.user})"):
            await send(interaction, f"💔 {interaction.user.mention} и <@{partner_id}> расстались. Комната удалена, "
                                    f"баланс комнаты возвращён обоим пополам.")
        else:
            await send(interaction, "❌ Не удалось удалить канал комнаты.")


async def setup(bot):
    await bot.add_cog(Rooms(bot))
