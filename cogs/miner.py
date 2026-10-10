"""Сапёр (/miner): поле из кнопок 5x4 (20 клеток) — просто нажимай на клетки мышкой.

Discord разрешает максимум 25 кнопок в сообщении: 20 клеток + кнопка «Уйти с выигрышем».
Количество бомб (miner_mines_min–miner_mines_max) и прибавка за клетку (miner_step, % от ставки)
настраиваются в /panel → «Казино и дуэли». Бомба — «Бум!!!», ставка сгорает.
"""
import random

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import delete_later, send

ROWS, COLS = 4, 5
CELLS = ROWS * COLS
HIDDEN, SAFE, BOMB, BOOM = "❔", "💎", "💣", "💥"


class Cell(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(emoji=HIDDEN, style=discord.ButtonStyle.secondary, row=index // COLS)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.click(interaction, self.index)


class MinerGame(discord.ui.View):
    def __init__(self, cog, user: discord.Member, bet: int, mines: int, step: int):
        super().__init__(timeout=config.MINER_TIMEOUT)
        self.cog, self.user, self.bet, self.step = cog, user, bet, step
        self.mines = set(random.sample(range(CELLS), mines))
        self.opened: set[int] = set()
        self.exploded: int | None = None
        self.finished = False
        self.message: discord.Message | None = None
        self.cells = [Cell(i) for i in range(CELLS)]
        for c in self.cells:
            self.add_item(c)
        self.cash_btn = discord.ui.Button(label="Уйти с выигрышем", emoji="💰",
                                          style=discord.ButtonStyle.success, row=ROWS)
        self.cash_btn.callback = self.on_cash
        self.add_item(self.cash_btn)
        self.sync()

    @property
    def multiplier(self) -> float:
        return 1 + self.step / 100 * len(self.opened)

    @property
    def win(self) -> int:
        return int(self.bet * self.multiplier)

    def sync(self):
        for c in self.cells:
            i = c.index
            c.label = None
            if i == self.exploded:
                c.emoji, c.label, c.style, c.disabled = BOOM, "Бум!!!", discord.ButtonStyle.danger, True
            elif i in self.opened:
                c.emoji, c.style, c.disabled = SAFE, discord.ButtonStyle.success, True
            elif self.finished and i in self.mines:
                c.emoji, c.style, c.disabled = BOMB, discord.ButtonStyle.danger, True
            else:
                c.emoji, c.style, c.disabled = HIDDEN, discord.ButtonStyle.secondary, self.finished
        self.cash_btn.disabled = self.finished or not self.opened
        self.cash_btn.label = f"Уйти с выигрышем ({self.win})" if self.opened else "Уйти с выигрышем"

    async def embed(self, status: str = "", color=None) -> discord.Embed:
        db = self.cog.bot.db
        e = discord.Embed(title="💣 Сапёр", color=color or discord.Color.blurple(),
                          description=status or "Нажимай на клетки ❔. Пусто — выигрыш растёт, бомба — проигрыш.")
        e.add_field(name="Ставка", value=await db.fmt(self.bet))
        e.add_field(name="Бомб", value=f"{len(self.mines)} из {CELLS}")
        e.add_field(name="За клетку", value=f"+{self.step}% от ставки")
        e.add_field(name="Открыто", value=str(len(self.opened)))
        e.add_field(name="Множитель", value=f"x{self.multiplier:.2f}")
        e.add_field(name="Тотальный выигрыш", value=f"**{await db.fmt(self.win)}**")
        e.set_footer(text=self.user.display_name)
        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Это чужая игра. Начни свою: /miner", ephemeral=True)
            return False
        return True

    async def click(self, interaction: discord.Interaction, index: int):
        if self.finished or index in self.opened:
            return await interaction.response.defer()
        db = self.cog.bot.db
        if index in self.mines:
            self.exploded = index
            await self.finish()
            self.sync()
            status = f"💥 **Бум!!!** Ты попал на бомбу. Ставка {await db.fmt(self.bet)} сгорела."
            await interaction.response.edit_message(embed=await self.embed(status, discord.Color.red()), view=self)
            return delete_later(self.message, 60)
        self.opened.add(index)
        if len(self.opened) == CELLS - len(self.mines):
            return await self.cash_out(interaction, "🏆 Открыты все пустые клетки!")
        self.sync()
        await interaction.response.edit_message(
            embed=await self.embed("💎 Пусто! Открывай дальше или уходи с выигрышем."), view=self)

    async def on_cash(self, interaction: discord.Interaction):
        await self.cash_out(interaction, "💰 Ты ушёл с выигрышем!")

    async def cash_out(self, interaction: discord.Interaction | None, title: str):
        if self.finished:
            return
        db = self.cog.bot.db
        win = self.win if self.opened else self.bet  # ничего не открыл — ставка возвращается
        await self.finish()
        await db.add_balance(self.user.id, win)
        self.sync()
        balance = await db.get_balance(self.user.id)
        status = f"{title}\nВыигрыш: **{await db.fmt(win)}** (ставка {await db.fmt(self.bet)}). Баланс: {balance}"
        embed = await self.embed(status, discord.Color.green())
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        delete_later(self.message, 60)

    async def finish(self):
        self.finished = True
        self.stop()
        self.cog.active.discard(self.user.id)

    async def on_timeout(self):
        await self.cash_out(None, "⌛ Время вышло — выигрыш забран автоматически.")


class Miner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active: set[int] = set()

    @app_commands.command(name="miner", description="Сапёр: открывай клетки, не попади на бомбу")
    @app_commands.describe(bet="Ставка")
    @app_commands.guild_only()
    async def miner(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        db = self.bot.db
        if interaction.user.id in self.active:
            return await send(interaction, "❌ У тебя уже идёт игра в сапёра — доиграй её.")
        max_bet = await db.get_int("max_bet")
        if bet > max_bet:
            return await send(interaction, f"❌ Максимальная ставка: {await db.fmt(max_bet)}.")
        if not await db.try_remove(interaction.user.id, bet):
            return await send(interaction, f"❌ {interaction.user.mention}, у тебя не хватает монет.")
        lo = max(1, await db.get_int("miner_mines_min"))
        hi = max(1, await db.get_int("miner_mines_max"))
        lo, hi = min(lo, hi), max(lo, hi)
        mines = min(random.randint(lo, hi), CELLS - 1)
        step = max(1, await db.get_int("miner_step"))
        self.active.add(interaction.user.id)
        game = MinerGame(self, interaction.user, bet, mines, step)
        await interaction.response.send_message(embed=await game.embed(), view=game)
        game.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Miner(bot))
