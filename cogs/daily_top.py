"""Топ дня: в 00:00 (часовой пояс config.QUEST_TZ_OFFSET_HOURS) самый активный в голосовых за прошедшие сутки
получает награду (voice_top_reward в /panel), а в канал config.VOICE_TOP_CHANNEL_ID приходит сообщение
с картинкой его /profile. Прошлое такое сообщение удаляется.

Проверка идёт раз в минуту: если бот был выключен в полночь, топ подведётся сразу после запуска.
"""
import logging
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

import config
import quests
from profile_card import build_profile_card
from utils import fetch_member, fmt_voice


def yesterday() -> str:
    tz = quests._tz()
    return (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")


class DailyTop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.check.start()

    async def cog_unload(self):
        self.check.cancel()

    @tasks.loop(minutes=1)
    async def check(self):
        try:
            day = yesterday()
            if await self.bot.db.get("voice_top_last_day") == day:
                return
            await self.run(day)
        except Exception:
            logging.exception("Ошибка топа дня")

    @check.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def run(self, day: str):
        db = self.bot.db
        guild = self.bot.main_guild()
        channel = self.bot.get_channel(config.VOICE_TOP_CHANNEL_ID)
        if guild is None or channel is None:
            return  # попробуем через минуту

        winner, seconds = None, 0
        for row in await db.top_daily_voice(day, 10):
            member = await fetch_member(guild, row["user_id"])
            if member is not None and not member.bot:
                winner, seconds = member, row["seconds"]
                break

        await db.set("voice_top_last_day", day)  # отмечаем сразу, чтобы не наградить дважды
        await db.cleanup_daily_voice((datetime.now(quests._tz()) - timedelta(days=7)).strftime("%Y-%m-%d"))
        if winner is None or seconds <= 0:
            return

        reward = await db.get_int("voice_top_reward")
        if reward > 0:
            await db.add_balance(winner.id, reward)

        # удаляем прошлое сообщение
        old_id = await db.get_int("voice_top_msg_id")
        if old_id:
            try:
                old = await channel.fetch_message(old_id)
                await old.delete()
            except discord.HTTPException:
                pass

        date_text = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")
        embed = discord.Embed(
            title="🏆 Самый активный в голосовых за день",
            description=(
                f"{winner.mention} провёл(а) в голосовых **{fmt_voice(seconds)}** за {date_text}!\n"
                + (f"Награда: **+{await db.fmt(reward)}** 🎉" if reward > 0 else "")
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Новый победитель — каждый день в 00:00")
        file = await build_profile_card(self.bot, winner)
        embed.set_image(url="attachment://profile.png")
        msg = await channel.send(content=winner.mention, embed=embed, file=file,
                                 allowed_mentions=discord.AllowedMentions(users=True))
        await db.set("voice_top_msg_id", str(msg.id))


async def setup(bot):
    await bot.add_cog(DailyTop(bot))
