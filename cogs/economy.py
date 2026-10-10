"""Ежедневная награда (/timely), баланс, переводы (/give, с комиссией и подтверждением), топы."""
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import TempView, calc_fee, delete_later, fmt_num, fmt_voice, send

TOP_SIZE = 25


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="timely", description="Забрать ежедневную награду")
    async def timely(self, interaction: discord.Interaction):
        db = self.bot.db
        user = await db.get_user(interaction.user.id)
        now = int(time.time())
        cooldown = await db.get_int("daily_cooldown_hours") * 3600
        last = user["last_daily"]

        if now - last < cooldown:
            ready_at = last + cooldown
            return await send(interaction, f"⏳ {interaction.user.mention}, награда уже получена. "
                                           f"Следующая будет доступна <t:{ready_at}:R>.")

        if last != 0 and now - last <= cooldown + 86400:
            streak = user["streak"] + 1
        else:
            streak = 1

        base = await db.get_int("daily_amount")
        bonus = min(streak - 1, 6) * await db.get_int("daily_streak_bonus")
        reward = base + bonus

        await db.claim_daily(interaction.user.id, now, streak, reward)
        balance = await db.get_balance(interaction.user.id)

        embed = discord.Embed(title=f"🎁 Ежедневная награда — {interaction.user.display_name}", color=discord.Color.green())
        embed.add_field(name="Получено", value=await db.fmt(reward))
        embed.add_field(name="Серия", value=f"🔥 {streak} дн.")
        embed.add_field(name="Баланс", value=await db.fmt(balance))
        if bonus:
            embed.set_footer(text=f"В награду входит бонус за серию: +{bonus}")
        await send(interaction, embed=embed)

    @app_commands.command(name="balance", description="Показать баланс")
    @app_commands.describe(member="Чей баланс показать (по умолчанию твой)")
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        balance = await self.bot.db.get_balance(member.id)
        embed = discord.Embed(
            description=f"💰 Баланс {member.mention}: **{await self.bot.db.fmt(balance)}**",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, delete_after=20)  # удаляется через 20 секунд

    @app_commands.command(name="give", description="Передать монеты другому участнику (комиссия сверху)")
    @app_commands.describe(member="Кому", amount="Сколько получит участник (комиссия добавится сверху)")
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1]):
        db = self.bot.db
        if member.bot or member.id == interaction.user.id:
            return await send(interaction, "❌ Нельзя переводить себе или боту.")
        percent = await db.get_int("transfer_commission")
        fee = calc_fee(amount, percent)
        total = amount + fee
        balance = await db.get_balance(interaction.user.id)
        embed = discord.Embed(title="💸 Подтверди перевод", color=discord.Color.orange())
        embed.description = f"{interaction.user.mention} ➜ {member.mention}"
        embed.add_field(name="Получит", value=await db.fmt(amount))
        embed.add_field(name=f"Комиссия {percent}%", value=await db.fmt(fee))
        embed.add_field(name="Спишется с тебя", value=f"**{await db.fmt(total)}**")
        if balance < total:
            embed.color = discord.Color.red()
            embed.set_footer(text=f"Не хватает монет: у тебя {balance}, нужно {total}")
            return await send(interaction, embed=embed)
        embed.set_footer(text=f"Твой баланс: {balance} → {balance - total}")
        await send(interaction, embed=embed, view=GiveConfirm(self.bot, interaction.user, member, amount, fee, percent))

    @app_commands.command(name="history", description="История переводов и продаж ролей")
    @app_commands.describe(member="Чью историю показать (по умолчанию твою)")
    async def history(self, interaction: discord.Interaction, member: discord.Member | None = None):
        db = self.bot.db
        member = member or interaction.user
        rows = await db.get_transactions(member.id, 15)
        if not rows:
            return await send(interaction, f"📜 У {member.mention} пока нет переводов.")
        lines = []
        for r in rows:
            if r["from_id"] == member.id:
                what = {"pay": "Перевод", "role_buy": "Покупка роли", "role_renew": "Продление роли"}.get(r["kind"], "Платёж")
                extra = f" · {r['note']}" if r["note"] else ""
                lines.append(f"📤 <t:{r['ts']}:d> {what} ➜ <@{r['to_id']}>: **−{await db.fmt(r['amount'])}**{extra}")
            else:
                what = {"pay": "Перевод", "role_buy": "Продажа роли", "role_renew": "Продление роли"}.get(r["kind"], "Платёж")
                extra = f" · {r['note']}" if r["note"] else ""
                fee = f" (комиссия {await db.fmt(r['fee'])})" if r["fee"] else ""
                lines.append(
                    f"📥 <t:{r['ts']}:d> {what} ⬅ <@{r['from_id']}>: **+{await db.fmt(r['amount'] - r['fee'])}**{fee}{extra}"
                )
        embed = discord.Embed(
            title=f"📜 История переводов — {member.display_name}",
            description="\n".join(lines), color=discord.Color.blurple(),
        )
        embed.set_footer(text="Последние 15 операций")
        await send(interaction, embed=embed)

    @app_commands.command(name="top", description="Рейтинги участников (до 25 мест)")
    @app_commands.describe(category="По какому показателю (по умолчанию — монеты)")
    @app_commands.choices(category=[
        app_commands.Choice(name="💰 Монеты", value="coins"),
        app_commands.Choice(name="🎙 Голосовой онлайн", value="voice"),
        app_commands.Choice(name="🎯 Выполненные задания", value="quests"),
        app_commands.Choice(name="💬 Сообщения", value="messages"),
    ])
    async def top(self, interaction: discord.Interaction, category: app_commands.Choice[str] | None = None):
        db = self.bot.db
        kind = category.value if category else "coins"

        if kind == "coins":
            title = "🏆 Топ по монетам"
            rows = [(r["user_id"], await db.fmt(r["balance"])) for r in await db.top(TOP_SIZE)]
        elif kind == "voice":
            title = "🏆 Топ по голосовому онлайну"
            rows = [(r["user_id"], fmt_voice(r["value"])) for r in await db.top_stat("voice_seconds", TOP_SIZE)]
        elif kind == "quests":
            title = "🏆 Топ по выполненным заданиям"
            rows = [(r["user_id"], f"{fmt_num(r['value'])} зад.") for r in await db.top_stat("quests_done", TOP_SIZE)]
        else:
            title = "🏆 Топ по сообщениям"
            rows = [(r["user_id"], fmt_num(r["value"])) for r in await db.top_stat("messages", TOP_SIZE)]

        if not rows:
            return await send(interaction, "Пока здесь пусто.")

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, value) in enumerate(rows):
            place = medals[i] if i < 3 else f"**{i + 1}.**"
            lines.append(f"{place} <@{uid}> — {value}")
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
        embed.set_footer(text="Другие рейтинги — выбери категорию в /top")
        msg = await send(interaction, embed=embed, keep=True)
        delete_later(msg, 120)  # длинный топ держим 2 минуты


class GiveConfirm(TempView):
    def __init__(self, bot, sender, receiver, amount, fee, percent):
        super().__init__(owner_id=sender.id, timeout=60)
        self.bot, self.sender, self.receiver = bot, sender, receiver
        self.amount, self.fee, self.percent = amount, fee, percent
        self.done = False

    @discord.ui.button(label="Подтвердить перевод", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        self.stop()
        db = self.bot.db
        total = self.amount + self.fee
        if not await db.try_remove(self.sender.id, total):
            await interaction.response.edit_message(
                embed=discord.Embed(description=f"❌ Не хватает монет: нужно {await db.fmt(total)}.",
                                    color=discord.Color.red()), view=None)
            return delete_later(interaction.message)
        await db.add_balance(self.receiver.id, self.amount)
        await db.add_transaction(int(time.time()), self.sender.id, self.receiver.id, total, self.fee, "pay")
        embed = discord.Embed(title="💸 Перевод выполнен", color=discord.Color.green())
        embed.description = f"{self.sender.mention} ➜ {self.receiver.mention}"
        embed.add_field(name="Получено", value=await db.fmt(self.amount))
        embed.add_field(name=f"Комиссия {self.percent}%", value=await db.fmt(self.fee))
        embed.add_field(name="Списано всего", value=await db.fmt(total))
        await interaction.response.edit_message(
            content=f"{self.receiver.mention}, {self.sender.mention} перевёл(а) вам **{await db.fmt(self.amount)}**",
            embed=embed, view=None,
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.done = True
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="🚫 Перевод отменён.", color=discord.Color.greyple()), view=None)
        delete_later(interaction.message)

    async def on_timeout(self):
        if not self.done and self.message is not None:
            try:
                await self.message.edit(
                    embed=discord.Embed(description="⌛ Перевод не подтверждён.", color=discord.Color.greyple()),
                    view=None)
            except discord.HTTPException:
                pass
            delete_later(self.message)


async def setup(bot):
    await bot.add_cog(Economy(bot))
