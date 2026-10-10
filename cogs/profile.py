"""Профиль участника + подсчёт сообщений и времени в голосовых каналах."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import quests
from cards import build_love_card
from cogs.rooms import contribute_fund, extend_room
from profile_card import build_profile_card
from utils import fmt_num, fmt_voice, send


class ProfileView(discord.ui.View):
    """Старая кнопка продления (для профилей, отправленных до обновления). Новые профили её не используют."""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Продлить любовную комнату", emoji="💞", style=discord.ButtonStyle.primary,
                       custom_id="profile:extend_room")
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, text = await extend_room(self.bot, interaction.user)
        await send(interaction, text)


# ---------------------------------------------------------------- любовный профиль
# Все кнопки «постоянные» (DynamicItem): в custom_id зашит ID участника, чей это профиль,
# поэтому они работают и после перезапуска бота.

async def _pair_check(interaction: discord.Interaction, member_id: int):
    """Возвращает комнату, если нажал кто-то из пары. Иначе отвечает отказом и возвращает None."""
    room = await interaction.client.db.get_room_of(member_id)
    if room is None:
        await interaction.response.send_message("💔 Любовной комнаты больше нет.", ephemeral=True)
        return None
    if interaction.user.id not in (room["owner_id"], room["partner_id"]):
        await interaction.response.send_message("⛔ Эти кнопки только для этой пары.", ephemeral=True)
        return None
    return room


def profile_view(member_id: int, has_room: bool) -> discord.ui.View | None:
    if not has_room:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(LoveOpen(member_id))
    return view


def love_view(member_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for cls in (LoveExtend, LoveFund, LoveDivorce, LoveBack):
        view.add_item(cls(member_id))
    return view


async def show_love(interaction: discord.Interaction, member_id: int, room):
    """Меняет картинку сообщения на любовный профиль."""
    member = interaction.guild.get_member(member_id) or interaction.user
    file = await build_love_card(interaction.client, member, room)
    await interaction.edit_original_response(attachments=[file], view=love_view(member_id))


class LoveOpen(discord.ui.DynamicItem[discord.ui.Button], template=r"profile:love:(?P<mid>[0-9]+)"):
    def __init__(self, member_id: int):
        super().__init__(discord.ui.Button(label="Любовный профиль", emoji="💞", style=discord.ButtonStyle.primary,
                                           custom_id=f"profile:love:{member_id}"))
        self.member_id = member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]))

    async def callback(self, interaction: discord.Interaction):
        room = await _pair_check(interaction, self.member_id)
        if room is None:
            return
        await interaction.response.defer()
        await show_love(interaction, self.member_id, room)


class LoveExtend(discord.ui.DynamicItem[discord.ui.Button], template=r"love:ext:(?P<mid>[0-9]+)"):
    def __init__(self, member_id: int):
        super().__init__(discord.ui.Button(label="Продлить любовную комнату", emoji="⏳",
                                           style=discord.ButtonStyle.success, custom_id=f"love:ext:{member_id}"))
        self.member_id = member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]))

    async def callback(self, interaction: discord.Interaction):
        if await _pair_check(interaction, self.member_id) is None:
            return
        await interaction.response.defer()
        ok, text = await extend_room(interaction.client, interaction.user)
        await send(interaction, text)
        room = await interaction.client.db.get_room_of(self.member_id)
        if room:
            await show_love(interaction, self.member_id, room)


class FundModal(discord.ui.Modal, title="Скинуться в баланс комнаты"):
    amount = discord.ui.TextInput(label="Сколько монет положить в копилку", max_length=12, placeholder="например 500")

    def __init__(self, member_id: int):
        super().__init__()
        self.member_id = member_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value.strip().replace(" ", ""))
        except ValueError:
            return await interaction.response.send_message("❌ Нужно целое число.", ephemeral=True)
        await interaction.response.defer()
        ok, text = await contribute_fund(interaction.client, interaction.user, amount)
        await send(interaction, text)
        room = await interaction.client.db.get_room_of(self.member_id)
        if ok and room:
            await show_love(interaction, self.member_id, room)


class LoveFund(discord.ui.DynamicItem[discord.ui.Button], template=r"love:fund:(?P<mid>[0-9]+)"):
    def __init__(self, member_id: int):
        super().__init__(discord.ui.Button(label="Скинуться в баланс комнаты", emoji="💗",
                                           style=discord.ButtonStyle.primary, custom_id=f"love:fund:{member_id}"))
        self.member_id = member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]))

    async def callback(self, interaction: discord.Interaction):
        if await _pair_check(interaction, self.member_id) is None:
            return
        await interaction.response.send_modal(FundModal(self.member_id))


class DivorceConfirm(discord.ui.View):
    def __init__(self, member_id: int):
        super().__init__(timeout=60)
        self.member_id = member_id

    @discord.ui.button(label="Да, развестись", emoji="💔", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client
        room = await bot.db.get_room_of(interaction.user.id)
        if room is None:
            return await interaction.response.edit_message(content="Комнаты уже нет.", view=None)
        await interaction.response.edit_message(content="⏳ Удаляю комнату…", view=None)
        partner_id = room["partner_id"] if room["owner_id"] == interaction.user.id else room["owner_id"]
        cog = bot.get_cog("Rooms")
        if cog and await cog.finish_room(room, f"Развод ({interaction.user})"):
            await interaction.edit_original_response(content="💔 Готово. Комната удалена.")
            await send(interaction, f"💔 {interaction.user.mention} и <@{partner_id}> развелись. "
                                    f"Баланс комнаты возвращён обоим пополам.")
        else:
            await interaction.edit_original_response(content="❌ Не удалось удалить канал комнаты.")

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Отменено.", view=None)


class LoveDivorce(discord.ui.DynamicItem[discord.ui.Button], template=r"love:div:(?P<mid>[0-9]+)"):
    def __init__(self, member_id: int):
        super().__init__(discord.ui.Button(label="Развестись", emoji="💔", style=discord.ButtonStyle.danger,
                                           custom_id=f"love:div:{member_id}"))
        self.member_id = member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]))

    async def callback(self, interaction: discord.Interaction):
        if await _pair_check(interaction, self.member_id) is None:
            return
        await interaction.response.send_message(
            "⚠️ Точно развестись? Комната и её роль удалятся, баланс комнаты вернётся вам пополам.",
            view=DivorceConfirm(self.member_id), ephemeral=True)


class LoveBack(discord.ui.DynamicItem[discord.ui.Button], template=r"love:back:(?P<mid>[0-9]+)"):
    def __init__(self, member_id: int):
        super().__init__(discord.ui.Button(label="Вернуться в профиль", emoji="↩️",
                                           style=discord.ButtonStyle.secondary, custom_id=f"love:back:{member_id}"))
        self.member_id = member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]))

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.member_id)
        if member is None:
            return await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
        await interaction.response.defer()
        bot = interaction.client
        file = await build_profile_card(bot, member)
        has_room = await bot.db.get_room_of(member.id) is not None
        await interaction.edit_original_response(attachments=[file], view=profile_view(member.id, has_room))


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ProfileView(self.bot))
        self.bot.add_dynamic_items(LoveOpen, LoveExtend, LoveFund, LoveDivorce, LoveBack)
        self.voice_tick.start()

    async def cog_unload(self):
        self.voice_tick.cancel()

    # ---------- сбор статистики ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        await self.bot.db.add_message(message.author.id)

    @tasks.loop(seconds=60)
    async def voice_tick(self):
        """Раз в минуту добавляет по 60 секунд (и монеты за онлайн) всем, кто сидит в голосовых.
        AFK-канал не считается."""
        try:
            ids = []
            for guild in self.bot.guilds:
                for channel in [*guild.voice_channels, *guild.stage_channels]:
                    if channel == guild.afk_channel:
                        continue
                    ids.extend(m.id for m in channel.members if not m.bot)
            if ids:
                reward = await self.bot.db.get_int("voice_reward")
                await self.bot.db.add_voice(ids, 60, reward)
                await self.bot.db.add_daily_voice(ids, quests.today(), 60)  # для топа дня
                quests_cog = self.bot.get_cog("Quests")
                if quests_cog is not None:
                    await quests_cog.add_voice(ids, 60)  # прогресс ежедневных заданий
                cases_cog = self.bot.get_cog("Cases")
                if cases_cog is not None:
                    await cases_cog.add_voice(ids)  # шанс получить кейс
        except Exception:
            logging.exception("Ошибка подсчёта голосовых")

    @voice_tick.before_loop
    async def before_voice_tick(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    # ---------- /profile ----------
    @app_commands.command(name="profile", description="Профиль участника")
    @app_commands.describe(member="Чей профиль показать (по умолчанию твой)")
    @app_commands.guild_only()
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        db = self.bot.db
        member = member or interaction.user
        await interaction.response.defer()  # обычный (публичный) ответ — его видят все

        room = await db.get_room_of(member.id)
        # картинка профиля с реальными данными из базы; если есть пара — кнопка «Любовный профиль»
        file = await build_profile_card(self.bot, member)
        view = profile_view(member.id, room is not None)
        if view:
            await interaction.followup.send(file=file, view=view)  # профиль не удаляется
        else:
            await interaction.followup.send(file=file)


async def setup(bot):
    await bot.add_cog(Profile(bot))
