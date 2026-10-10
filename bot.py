"""Главный файл. Запуск: python bot.py"""
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import config
from checks import NotBotAdmin
from database import Database
from utils import send

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

EXTENSIONS = [
    "cogs.economy",
    "cogs.shop",
    "cogs.inventory",
    "cogs.rooms",
    "cogs.duel",
    "cogs.casino",
    "cogs.profile",
    "cogs.custom_rooms",
    "cogs.quests",
    "cogs.cases",
    "cogs.miner",
    "cogs.daily_top",
    "cogs.sync",
    "cogs.admin",
]

logging.basicConfig(level=logging.INFO)


class EconomyTree(app_commands.CommandTree):
    """Пускает команды только в канале #корзина (/room — ещё и в каналах из config.ROOM_CHANNEL_IDS)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        name = (interaction.data or {}).get("name")
        if name == "room" and interaction.channel_id in config.ROOM_CHANNEL_IDS:
            return True
        if interaction.channel_id != config.CART_CHANNEL_ID:
            await interaction.response.send_message(
                f"❌ Бот работает только в канале <#{config.CART_CHANNEL_ID}>.", ephemeral=True
            )
            return False
        return True


class EconomyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # нужно для ролей, профилей и подсчёта голосовых
        super().__init__(command_prefix="!", intents=intents, tree_cls=EconomyTree)
        self.db = Database("economy.db")

    def main_guild(self) -> discord.Guild | None:
        if GUILD_ID:
            return self.get_guild(int(GUILD_ID))
        return self.guilds[0] if self.guilds else None

    async def setup_hook(self):
        await self.db.connect()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logging.info("Загружен модуль %s", ext)

        self.tree.on_error = self.on_tree_error

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)  # команды появятся сразу
        else:
            await self.tree.sync()

    async def on_ready(self):
        logging.info("Бот запущен как %s", self.user)

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, NotBotAdmin):
            msg = str(error)
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "⛔ У тебя нет прав для этой команды."
        else:
            logging.exception("Ошибка команды", exc_info=error)
            msg = "❌ Что-то пошло не так. Подробности смотри в консоли бота."
        # сообщение видно всем и удаляется через 20 секунд
        await send(interaction, msg)

    async def close(self):
        await self.db.close()
        await super().close()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не найден DISCORD_TOKEN. Создай файл .env (смотри .env.example).")
    EconomyBot().run(TOKEN)
