"""Казино: монетка, слоты, рулетка."""
import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import delete_later, gif_embed, gif_file, send

# Слоты: символ -> (вес выпадения, множитель за три одинаковых)
SLOTS = {
    "🍒": (30, 5),
    "🍋": (25, 8),
    "🍇": (20, 10),
    "🔔": (12, 15),
    "💎": (8, 25),
    "7️⃣": (5, 50),
}
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
COINFLIP_WIN_CHANCE = 0.48  # небольшое преимущество казино


class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def check_bet(self, interaction: discord.Interaction, bet: int) -> bool:
        max_bet = await self.bot.db.get_int("max_bet")
        if bet > max_bet:
            await send(interaction, f"❌ Максимальная ставка: {await self.bot.db.fmt(max_bet)}.")
            return False
        if not await self.bot.db.try_remove(interaction.user.id, bet):
            await send(interaction, f"❌ {interaction.user.mention}, у тебя не хватает монет.")
            return False
        return True  # ставка уже списана

    async def result_embed(self, title, text, bet, payout):
        db = self.bot.db
        net = payout - bet
        if net > 0:
            color, line = discord.Color.green(), f"✅ Выигрыш: **+{await db.fmt(net)}**"
        elif net == 0:
            color, line = discord.Color.light_grey(), "➖ Ставка возвращена"
        else:
            color, line = discord.Color.red(), f"❌ Проигрыш: **-{await db.fmt(bet)}**"
        return discord.Embed(title=title, description=f"{text}\n\n{line}", color=color)

    async def play_gif(self, interaction: discord.Interaction, game: str, title: str, text: str):
        """Показывает гифку игры и ждёт config.GIF_SECONDS[game] секунд.
        Возвращает сообщение (его потом редактируют на результат)."""
        file = gif_file(game)
        embed = gif_embed(game, title, text)
        if file:
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        await asyncio.sleep(config.GIF_SECONDS.get(game, 3))
        return message

    async def show_result(self, interaction: discord.Interaction, message, embed):
        """Заменяет сообщение с гифкой на результат (гифка убирается)."""
        try:
            await interaction.edit_original_response(embed=embed, attachments=[])
        except discord.HTTPException:
            await interaction.followup.send(embed=embed)
        delete_later(message)

    def player(self, interaction) -> str:
        return interaction.user.mention

    @app_commands.command(name="coinflip", description="Орёл или решка")
    @app_commands.describe(bet="Ставка", side="Твоя сторона")
    @app_commands.choices(side=[
        app_commands.Choice(name="Орёл", value="heads"),
        app_commands.Choice(name="Решка", value="tails"),
    ])
    async def coinflip(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1], side: app_commands.Choice[str]):
        if not await self.check_bet(interaction, bet):
            return
        message = await self.play_gif(
            interaction, "coinflip", "🪙 Монетка",
            f"{interaction.user.mention} подбрасывает монетку… 🪙")
        win = random.random() < COINFLIP_WIN_CHANCE
        result = side.value if win else ("tails" if side.value == "heads" else "heads")
        names = {"heads": "Орёл", "tails": "Решка"}
        payout = bet * 2 if win else 0
        await self.bot.db.add_balance(interaction.user.id, payout)
        embed = await self.result_embed("🪙 Монетка", f"Ты выбрал: **{names[side.value]}**\nВыпало: **{names[result]}**", bet, payout)
        balance = await self.bot.db.get_balance(interaction.user.id)
        embed.set_footer(text=f"{interaction.user.display_name} · Баланс: {balance}")
        await self.show_result(interaction, message, embed)

    @app_commands.command(name="slots", description="Игровой автомат")
    @app_commands.describe(bet="Ставка")
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await self.check_bet(interaction, bet):
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="🎰 Слоты", description=f"{interaction.user.mention} крутит барабаны… 🎰")
        )
        message = await interaction.original_response()

        symbols = list(SLOTS.keys())
        weights = [SLOTS[s][0] for s in symbols]
        reels = random.choices(symbols, weights=weights, k=3)

        if reels[0] == reels[1] == reels[2]:
            multiplier = SLOTS[reels[0]][1]
        elif len(set(reels)) == 2:
            multiplier = 1  # две одинаковых — ставка возвращается
        else:
            multiplier = 0

        payout = bet * multiplier
        await self.bot.db.add_balance(interaction.user.id, payout)
        await asyncio.sleep(1.2)

        embed = await self.result_embed("🎰 Слоты", f"**[ {' | '.join(reels)} ]**", bet, payout)
        if multiplier > 1:
            embed.add_field(name="Множитель", value=f"x{multiplier}")
        balance = await self.bot.db.get_balance(interaction.user.id)
        embed.set_footer(text=f"{interaction.user.display_name} · Баланс: {balance}")
        await interaction.edit_original_response(embed=embed)
        delete_later(message)

    @app_commands.command(name="roulette", description="Рулетка: красное, чёрное или зелёное")
    @app_commands.describe(bet="Ставка", color="На что ставишь")
    @app_commands.choices(color=[
        app_commands.Choice(name="🔴 Красное (x2)", value="red"),
        app_commands.Choice(name="⚫ Чёрное (x2)", value="black"),
        app_commands.Choice(name="🟢 Зелёное (x14)", value="green"),
    ])
    async def roulette(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1], color: app_commands.Choice[str]):
        if not await self.check_bet(interaction, bet):
            return
        message = await self.play_gif(
            interaction, "roulette", "🎡 Рулетка",
            f"{interaction.user.mention} ставит **{color.name}**. Шарик крутится… 🎡")
        number = random.randint(0, 36)
        if number == 0:
            result, emoji = "green", "🟢"
        elif number in RED_NUMBERS:
            result, emoji = "red", "🔴"
        else:
            result, emoji = "black", "⚫"

        payout = 0
        if color.value == result:
            payout = bet * (14 if result == "green" else 2)
        await self.bot.db.add_balance(interaction.user.id, payout)
        await self.bot.db.add_roulette(int(time.time()), interaction.user.id, number, result)

        embed = await self.result_embed("🎡 Рулетка", f"Шарик упал на **{emoji} {number}**\nТвоя ставка: **{color.name}**", bet, payout)
        balance = await self.bot.db.get_balance(interaction.user.id)
        embed.set_footer(text=f"{interaction.user.display_name} · Баланс: {balance}")
        await self.show_result(interaction, message, embed)

    @app_commands.command(name="log", description="Какие цвета и числа выпадали в рулетке")
    async def log(self, interaction: discord.Interaction):
        rows = await self.bot.db.get_roulette(20)
        if not rows:
            return await send(interaction, "🎡 В рулетку ещё никто не играл.")
        emojis = {"red": "🔴", "black": "⚫", "green": "🟢"}
        # от старых к новым: последняя игра — справа
        history = "  ".join(f"{emojis[r['color']]}**{r['number']}**" for r in reversed(rows))
        counts = await self.bot.db.roulette_color_counts()
        total = sum(counts.values())
        embed = discord.Embed(title="🎡 Журнал рулетки", description=history, color=discord.Color.dark_red())
        embed.add_field(
            name="Всего выпадений",
            value=f"🔴 {counts.get('red', 0)} · ⚫ {counts.get('black', 0)} · 🟢 {counts.get('green', 0)} (игр: {total})",
            inline=False,
        )
        embed.set_footer(text=f"Последние {len(rows)} игр — слева старые, справа самая свежая")
        await send(interaction, embed=embed)


async def setup(bot):
    await bot.add_cog(Casino(bot))
