"""Синхронизация с сервером: если администратор удалил роль или комнату — убираем их из базы.

• Роль удалена → она пропадает из магазина, ролей игроков, инвентарей, призов и предметов кейсов.
  Если это роль кастомной комнаты — комната удаляется целиком.
• Канал кастомной комнаты удалён → комната удаляется целиком (вместе с ролью).
• Канал любовной комнаты удалён администратором (пока в нём сидели) → любовная комната удаляется,
  баланс комнаты возвращается паре пополам.
Удаления, которые делает сам бот (истёк срок, комната опустела и т.п.), сюда не попадают.
Плюс раз в 10 минут проверка: всё, чего уже нет на сервере (например, удалили, пока бот был выключен), чистится.
"""
import logging

import discord
from discord.ext import commands, tasks

from utils import mark_self_delete, was_self_deleted


class Sync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.periodic.start()

    async def cog_unload(self):
        self.periodic.cancel()

    async def _drop_croom(self, guild: discord.Guild, room, reason: str):
        """Удаляет кастомную комнату: оставшийся канал/роль и запись в базе."""
        for obj in (guild.get_channel(room["channel_id"]), guild.get_role(room["role_id"])):
            if obj is not None:
                mark_self_delete(obj.id)
                try:
                    await obj.delete(reason=reason)
                except discord.HTTPException:
                    pass
        await self.bot.db.delete_croom(room["owner_id"])

    # ---------- события ----------
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if was_self_deleted(role.id):
            return
        try:
            db = self.bot.db
            await db.purge_role(role.id)
            if await db.get_int("room_role_id") == role.id:
                await db.set("room_role_id", "0")
            room = await db.get_croom_by(role_id=role.id)
            if room:
                await self._drop_croom(role.guild, room, "Роль комнаты удалена администратором")
            logging.info("Роль %s удалена с сервера — убрана из базы", role.id)
        except Exception:
            logging.exception("Ошибка уборки после удаления роли")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if was_self_deleted(channel.id):
            return
        try:
            db = self.bot.db
            room = await db.get_croom_by(channel_id=channel.id)
            if room:
                await self._drop_croom(channel.guild, room, "Канал комнаты удалён администратором")
                return
            love = await db.get_room_by_channel(channel.id)
            if love:
                rooms_cog = self.bot.get_cog("Rooms")
                if rooms_cog is not None:
                    await rooms_cog.finish_room(love, "Любовная комната удалена администратором")
                else:
                    await db.delete_room(love["owner_id"])
        except Exception:
            logging.exception("Ошибка уборки после удаления канала")

    # ---------- периодическая проверка ----------
    @tasks.loop(minutes=10)
    async def periodic(self):
        try:
            guild = self.bot.main_guild()
            if guild is None or not guild.roles:
                return
            db = self.bot.db
            existing = {r.id for r in guild.roles}
            for role_id in await db.known_role_ids():
                if role_id not in existing:
                    await db.purge_role(role_id)
            for room in await db.all_crooms():
                if guild.get_channel(room["channel_id"]) is None or guild.get_role(room["role_id"]) is None:
                    await self._drop_croom(guild, room, "Канал или роль комнаты удалены")
            love_role = await db.get_int("room_role_id")
            if love_role and love_role not in existing:
                await db.set("room_role_id", "0")
        except Exception:
            logging.exception("Ошибка периодической уборки")

    @periodic.before_loop
    async def before_periodic(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Sync(bot))
