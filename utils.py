"""Мелкие вспомогательные функции."""
import asyncio
import logging
import os

import discord

import config


def calc_fee(amount: int, percent: int) -> int:
    """Комиссия в монетах (округляется вверх)."""
    return -(-amount * percent // 100)


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_voice(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    return f"{hours} ч {rest // 60} мин"


def role_is_manageable(guild: discord.Guild, role: discord.Role) -> bool:
    """Может ли бот выдавать эту роль (она ниже роли бота и не служебная)."""
    return not role.is_default() and not role.managed and role < guild.me.top_role


async def fetch_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


async def add_role(guild: discord.Guild, user_id: int, role: discord.Role | None, reason: str) -> bool:
    member = await fetch_member(guild, user_id)
    if member is None or role is None:
        return False
    try:
        await member.add_roles(role, reason=reason)
        return True
    except discord.HTTPException:
        return False


async def remove_role(guild: discord.Guild, user_id: int, role: discord.Role | None, reason: str) -> bool:
    """True, если роли у человека больше нет (убрали или её и не было)."""
    member = await fetch_member(guild, user_id)
    if member is None or role is None:
        return True
    if role not in member.roles:
        return True
    try:
        await member.remove_roles(role, reason=reason)
        return True
    except discord.HTTPException:
        return False


# ---------- сообщения: публичные, удаляются через config.MESSAGE_LIFETIME секунд ----------
_tasks: set = set()


async def safe_delete(message) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except discord.HTTPException:
        pass


def delete_later(message, delay: int | None = None) -> None:
    """Удалит сообщение через delay секунд (по умолчанию config.MESSAGE_LIFETIME)."""
    if message is None:
        return
    delay = config.MESSAGE_LIFETIME if delay is None else delay

    async def runner():
        await asyncio.sleep(delay)
        await safe_delete(message)

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def send(interaction: discord.Interaction, content=None, *, embed=None, view=None,
               keep: bool = False, ephemeral: bool = False):
    """Отправляет публичное сообщение (или followup, если ответ уже был) и планирует его удаление.
    keep=True — не удалять (профиль, баланс). Возвращает отправленное сообщение."""
    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if ephemeral:
        kwargs["ephemeral"] = True
    if interaction.response.is_done():
        msg = await interaction.followup.send(wait=True, **kwargs)
    else:
        await interaction.response.send_message(**kwargs)
        msg = await interaction.original_response()
    if view is not None and hasattr(view, "message"):
        view.message = msg
    if not keep and not ephemeral and view is None:
        delete_later(msg)
    return msg


def gif_file(game: str) -> discord.File | None:
    """Возвращает discord.File с гифкой игры (или None, если файла нет).
    Файл нужно создавать заново для каждой отправки."""
    name = config.GIFS.get(game)
    if not name:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.GIF_DIR, name)
    if not os.path.isfile(path):
        return None
    return discord.File(path, filename=name)


def gif_embed(game: str, title: str, text: str, color=None) -> discord.Embed:
    """Embed «игра идёт» с картинкой-гифкой (attachment://...)."""
    embed = discord.Embed(title=title, description=text, color=color or discord.Color.blurple())
    name = config.GIFS.get(game)
    if name:
        embed.set_image(url=f"attachment://{name}")
    return embed


class TempView(discord.ui.View):
    """Меню, которое исчезает через config.MESSAGE_LIFETIME секунд после последнего нажатия.
    owner_id — кто может нажимать кнопки (None = любой)."""

    def __init__(self, owner_id: int | None = None, timeout: float | None = None):
        super().__init__(timeout=config.MESSAGE_LIFETIME if timeout is None else timeout)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await send(interaction, "⛔ Это меню открыл другой участник. Вызови свою команду.")
            return False
        return True

    async def on_timeout(self):
        await safe_delete(self.message)


# ---------- удаления, которые делает сам бот ----------
# Когда роль или канал удаляет администратор, бот чистит их из базы (cogs/sync.py).
# Свои удаления бот помечает здесь, чтобы не путать их с удалениями администратора.
SELF_DELETED: set[int] = set()


def mark_self_delete(object_id: int) -> None:
    if object_id:
        SELF_DELETED.add(object_id)


def was_self_deleted(object_id: int) -> bool:
    if object_id in SELF_DELETED:
        SELF_DELETED.discard(object_id)
        return True
    return False


async def place_new_role(guild: discord.Guild, role: discord.Role) -> None:
    """Ставит только что созданную роль сразу над ролью config.NEW_ROLES_ABOVE_ID.
    Если та роль не найдена или стоит выше бота — роль остаётся где есть (это не ошибка)."""
    anchor_id = getattr(config, "NEW_ROLES_ABOVE_ID", 0)
    anchor = guild.get_role(anchor_id) if anchor_id else None
    if anchor is None:
        return
    target = anchor.position + 1
    if target >= guild.me.top_role.position:
        logging.warning("Не могу поставить роль %s над %s: роль бота должна быть выше", role.id, anchor_id)
        return
    try:
        await role.edit(position=target, reason="Новые роли — над заданной ролью")
    except discord.HTTPException:
        logging.exception("Не удалось переместить роль %s", role.id)
