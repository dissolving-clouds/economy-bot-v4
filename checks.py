"""Проверки доступа."""
import discord
from discord import app_commands

import config


class NotBotAdmin(app_commands.CheckFailure):
    pass


def is_bot_admin(member) -> bool:
    """Есть ли у участника роль администратора бота."""
    return isinstance(member, discord.Member) and any(r.id == config.ADMIN_ROLE_ID for r in member.roles)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_bot_admin(interaction.user):
            return True
        raise NotBotAdmin("⛔ Панель управления доступна только администраторам бота.")

    return app_commands.check(predicate)
