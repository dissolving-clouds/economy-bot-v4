"""Дуэли на монеты."""
import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import delete_later, gif_embed, gif_file, send

MOVES = [
    "наносит мощный удар мечом",
    "бросает огненный шар",
    "делает хитрый подкат",
    "выпускает град стрел",
    "использует секретный приём",
    "ставит блок и контратакует",
]


class DuelView(discord.ui.View):
    """Вызов на дуэль. Если opponent не указан — принять может любой, кроме автора вызова."""

    def __init__(self, bot, challenger: discord.Member, opponent: discord.Member | None, bet: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.challenger = challenger
        self.opponent = opponent
        self.bet = bet
        self.message: discord.Message | None = None
        self.finished = False

    async def on_timeout(self):
        if self.message and not self.finished:
            embed = discord.Embed(description="⌛ Время вышло — дуэль не состоялась.", color=discord.Color.greyple())
            try:
                await self.message.edit(content=None, embed=embed, view=None)
            except discord.HTTPException:
                pass
            delete_later(self.message)

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if self.finished:
            return
        if user.id == self.challenger.id:
            return await send(interaction, "⛔ Нельзя принять собственный вызов.")
        if self.opponent is not None and user.id != self.opponent.id:
            return await send(interaction, f"⛔ Этот вызов адресован {self.opponent.mention}.")
        if user.bot:
            return
        self.finished = True  # блокируем повторное принятие, пока идёт расчёт
        db = self.bot.db

        if not await db.try_remove(user.id, self.bet):
            self.finished = False  # вызов остаётся открытым — может принять кто-то другой
            return await send(interaction, f"❌ {user.mention}, у тебя не хватает монет для ставки.")
        if not await db.try_remove(self.challenger.id, self.bet):
            await db.add_balance(user.id, self.bet)
            self.stop()
            await interaction.response.edit_message(
                embed=discord.Embed(description="❌ У вызвавшего не хватает монет.", color=discord.Color.red()), view=None
            )
            return delete_later(self.message)

        self.stop()
        # 1) показываем гифку дуэли (кнопки убираем), 2) ждём, 3) показываем результат
        gif_embed_ = gif_embed("duel", "⚔️ Дуэль началась!",
                               f"{self.challenger.mention} ⚔️ {user.mention}\n\nСтавка: **{await db.fmt(self.bet)}** с каждого…",
                               discord.Color.orange())
        file = gif_file("duel")
        if file:
            await interaction.response.edit_message(content=None, embed=gif_embed_, attachments=[file], view=None)
        else:
            await interaction.response.edit_message(content=None, embed=gif_embed_, view=None)
        await asyncio.sleep(config.GIF_SECONDS.get("duel", 5))

        winner = random.choice([self.challenger, user])
        pot = self.bet * 2
        commission_pct = await db.get_int("duel_commission")
        prize = pot - pot * commission_pct // 100
        await db.add_balance(winner.id, prize)

        log = "\n".join(
            f"• {random.choice([self.challenger, user]).display_name} {random.choice(MOVES)}"
            for _ in range(3)
        )
        embed = discord.Embed(
            title="⚔️ Дуэль завершена!",
            description=f"{self.challenger.mention} ⚔️ {user.mention}\n\n{log}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="🏆 Победитель", value=winner.mention)
        embed.add_field(name="Выигрыш", value=await db.fmt(prize))
        embed.set_footer(text=f"Комиссия: {commission_pct}%")
        try:
            await interaction.edit_original_response(content=None, embed=embed, attachments=[], view=None)
        except discord.HTTPException:
            await interaction.followup.send(embed=embed)
        delete_later(self.message)

    @discord.ui.button(label="Отказаться / отменить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if self.finished:
            return
        if user.id == self.challenger.id:
            text = f"🏳️ {user.mention} отменил(а) свой вызов на дуэль."
        elif self.opponent is not None and user.id == self.opponent.id:
            text = f"🏳️ {user.mention} отказался от дуэли."
        else:
            return await send(interaction, "⛔ Отказаться может только тот, кого вызвали, или автор вызова.")
        self.finished = True
        self.stop()
        embed = discord.Embed(description=text, color=discord.Color.greyple())
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        delete_later(self.message)


class Duel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="duel", description="Вызвать на дуэль на монеты (соперник необязателен — принять сможет любой)")
    @app_commands.describe(bet="Ставка", opponent="Кого вызвать (если не указать — вызов для всех)")
    @app_commands.guild_only()
    async def duel(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1],
                   opponent: discord.Member | None = None):
        db = self.bot.db
        if opponent is not None and (opponent.bot or opponent.id == interaction.user.id):
            return await send(interaction, "❌ Выбери другого живого участника или оставь поле пустым.")
        max_bet = await db.get_int("max_bet")
        if bet > max_bet:
            return await send(interaction, f"❌ Максимальная ставка: {await db.fmt(max_bet)}.")
        if await db.get_balance(interaction.user.id) < bet:
            return await send(interaction, "❌ У тебя не хватает монет.")
        if opponent is not None and await db.get_balance(opponent.id) < bet:
            return await send(interaction, "❌ У соперника не хватает монет.")

        view = DuelView(self.bot, interaction.user, opponent, bet)
        target = opponent.mention if opponent else "**любого желающего**"
        embed = discord.Embed(
            title="⚔️ Вызов на дуэль!",
            description=f"{interaction.user.mention} вызывает {target}\nСтавка: **{await db.fmt(bet)}** с каждого.\n"
                        f"Принять вызов можно в течение 2 минут.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(content=opponent.mention if opponent else None, embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Duel(bot))
