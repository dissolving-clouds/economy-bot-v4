"""Ежедневные задания: подсчёт прогресса, выдача наград и уведомления в #корзина.

Сообщения считаются здесь (on_message), минуты в голосовых передаёт cogs/profile.py (voice_tick).
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import quests as q
from utils import send


class Quests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- прогресс ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        try:
            await self.bot.db.quest_add([message.author.id], q.today(), messages=1)
            await self.check_user(message.author.id)
        except Exception:
            logging.exception("Ошибка заданий (сообщения)")

    async def add_voice(self, user_ids: list[int], seconds: int):
        """Вызывается из voice_tick раз в минуту."""
        if not user_ids:
            return
        try:
            await self.bot.db.quest_add(user_ids, q.today(), voice_seconds=seconds)
            for uid in set(user_ids):
                await self.check_user(uid)
        except Exception:
            logging.exception("Ошибка заданий (голосовые)")

    async def check_user(self, user_id: int):
        """Выдаёт награды за задания, которые только что выполнены."""
        db = self.bot.db
        day = q.today()
        for quest in await q.user_quests(db, user_id):
            if quest["done"] or quest["progress"] < quest["goal"]:
                continue
            if not await db.quest_mark_done(user_id, day, quest["key"]):
                continue  # уже выдано
            await db.add_balance(user_id, quest["reward"])
            await db.add_quest_done(user_id)  # для /top «Выполненные задания»
            await self._notify(user_id, quest)

    async def _notify(self, user_id: int, quest: dict):
        channel = self.bot.get_channel(config.CART_CHANNEL_ID)
        if channel is None:
            return
        db = self.bot.db
        embed = discord.Embed(
            title="🎯 Задание выполнено!",
            description=(
                f"<@{user_id}> выполнил(а) задание **«{quest['title']}»**\n"
                f"Награда: **+{await db.fmt(quest['reward'])}**"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Все задания на сегодня — в /profile и /quests")
        kwargs = {}
        if config.QUEST_NOTIFY_LIFETIME > 0:
            kwargs["delete_after"] = config.QUEST_NOTIFY_LIFETIME
        try:
            # без пинга: упоминание внутри embed не присылает уведомление
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
        except discord.HTTPException:
            pass

    # ---------- /quests ----------
    @app_commands.command(name="quests", description="Ежедневные задания и награды за них")
    @app_commands.describe(member="Чьи задания показать (по умолчанию твои)")
    @app_commands.guild_only()
    async def quests_cmd(self, interaction: discord.Interaction, member: discord.Member | None = None):
        db = self.bot.db
        member = member or interaction.user
        items = await q.user_quests(db, member.id)
        if not items:
            return await send(interaction, "📋 Сейчас нет активных заданий.")
        lines = []
        for it in items:
            mark = "✅" if it["done"] else "▫️"
            if it["kind"] == "voice":
                prog = f"{q.fmt_minutes(it['progress'])} / {q.fmt_minutes(it['goal'])}"
            else:
                prog = f"{it['progress']} / {it['goal']}"
            lines.append(f"{mark} **{it['title']}** — {prog} · награда {await db.fmt(it['reward'])}")
        left = q.seconds_to_reset()
        embed = discord.Embed(
            title=f"📋 Задания на сегодня — {member.display_name}",
            description="\n".join(lines), color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Обновятся через {left // 3600} ч {left % 3600 // 60} мин")
        await send(interaction, embed=embed)


async def setup(bot):
    await bot.add_cog(Quests(bot))
