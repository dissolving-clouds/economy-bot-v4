"""Картинки для /case и /inventory в стиле /profile."""
import asyncio
import io
import os
import time

import discord
from PIL import Image, ImageDraw, ImageFont

from profile_card import (BASE, GOLD, _avatar_image, _circle, _coin, _fit, _font, _panel, _paste_avatar,
                          _stat_tile)

W, H = 1400, 780
BG = (18, 18, 21, 255)
GRAY = (170, 170, 178)
DIM = (130, 130, 140)
WHITE = (255, 255, 255)

RARITY_NAMES = {"common": "Обычный", "rare": "Редкий", "legendary": "Легендарный"}
RARITY_COLORS = {
    "common": (170, 175, 185),
    "rare": (80, 150, 255),
    "legendary": (245, 190, 40),
}


def fmt_n(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _to_png(img) -> io.BytesIO:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf


def _left_card(img, d, avatar, name, username, balance):
    """Левая колонка: аватар, имя и баланс (как в профиле)."""
    _panel(img, (30, 30, 400, H - 30), radius=40)
    _paste_avatar(img, avatar, 215, 210, 200)
    d.text((215, 400), name, font=_fit(d, name, 40, 330, True), fill=WHITE, anchor="mm")
    d.text((215, 445), f"@{username}", font=_fit(d, f"@{username}", 28, 330), fill=(150, 150, 160), anchor="mm")
    _panel(img, (55, 495, 375, 605), radius=26, fill=(36, 36, 41), outline=(58, 58, 65))
    d.text((355, 512), "Баланс", font=_font(22), fill=GRAY, anchor="ra")
    d.text((355, 590), balance, font=_fit(d, balance, 44, 200, True), fill=WHITE, anchor="rd")
    cx0, cy0 = 105, 550
    d.ellipse((cx0 - 30, cy0 - 30, cx0 + 30, cy0 + 30), fill=(245, 190, 40), outline=(255, 225, 110), width=4)
    d.ellipse((cx0 - 20, cy0 - 20, cx0 + 20, cy0 + 20), outline=(200, 140, 20), width=3)
    d.rounded_rectangle((cx0 - 4, cy0 - 11, cx0 + 4, cy0 + 11), radius=3, fill=(200, 140, 20))


def draw_chest(d, cx, cy, size, color):
    """Сундук фигурами: корпус, крышка, оковка и замок."""
    s = size
    dark = tuple(max(0, int(c * 0.55)) for c in color)
    light = tuple(min(255, int(c * 1.15) + 10) for c in color)
    # крышка
    d.rounded_rectangle((cx - s, cy - s * 0.75, cx + s, cy - s * 0.05), radius=int(s * 0.3), fill=light, outline=dark,
                        width=4)
    # корпус
    d.rounded_rectangle((cx - s, cy - s * 0.15, cx + s, cy + s * 0.75), radius=int(s * 0.12), fill=color,
                        outline=dark, width=4)
    # оковка
    for x in (cx - s * 0.62, cx + s * 0.62):
        d.rectangle((x - s * 0.09, cy - s * 0.72, x + s * 0.09, cy + s * 0.72), fill=dark)
    # замок
    d.rounded_rectangle((cx - s * 0.2, cy - s * 0.3, cx + s * 0.2, cy + s * 0.15), radius=6, fill=(250, 235, 180),
                        outline=dark, width=3)
    d.ellipse((cx - s * 0.06, cy - s * 0.17, cx + s * 0.06, cy - s * 0.05), fill=dark)


# ================================================================ /case

def render_case_card(*, avatar, name, username, balance, cases: dict, total_opened: int, best: str) -> io.BytesIO:
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    _left_card(img, d, avatar, name, username, balance)
    # плитка «всего открыто» под балансом
    _panel(img, (55, 625, 375, 725), radius=26, fill=(36, 36, 41), outline=(58, 58, 65))
    d.text((355, 642), "Открыто кейсов", font=_font(22), fill=GRAY, anchor="ra")
    d.text((355, 712), fmt_n(total_opened), font=_fit(d, fmt_n(total_opened), 40, 290, True), fill=WHITE,
           anchor="rd")

    # три кейса
    for i, rarity in enumerate(("common", "rare", "legendary")):
        x1 = 430 + i * 320
        box = (x1, 30, x1 + 300, 560)
        color = RARITY_COLORS[rarity]
        _panel(img, box, radius=36, outline=tuple(int(c * 0.6) for c in color))
        title = RARITY_NAMES[rarity]
        d.text((x1 + 150, 72), title, font=_fit(d, title, 30, 260, True), fill=color, anchor="mm")
        d.line((x1 + 30, 105, x1 + 270, 105), fill=(58, 58, 65), width=2)
        # мягкое свечение под сундуком
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((x1 + 50, 150, x1 + 250, 330), fill=(*color, 28))
        img.alpha_composite(glow)
        d = ImageDraw.Draw(img)
        draw_chest(d, x1 + 150, 240, 78, color)
        count = cases[rarity]["count"]
        d.text((x1 + 150, 395), f"x{fmt_n(count)}", font=_fit(d, f"x{fmt_n(count)}", 72, 250, True),
               fill=WHITE if count else DIM, anchor="mm")
        d.text((x1 + 150, 450), "в наличии", font=_font(22), fill=DIM, anchor="mm")
        _panel(img, (x1 + 25, 480, x1 + 275, 535), radius=18, fill=(36, 36, 41), outline=(58, 58, 65))
        opened = f"Открыто: {fmt_n(cases[rarity]['opened'])}"
        d.text((x1 + 150, 508), opened, font=_fit(d, opened, 22, 230), fill=GRAY, anchor="mm")

    # лучший приз
    _panel(img, (430, 590, W - 30, H - 30), radius=36)
    d.text((465, 625), "Лучший приз из кейсов", font=_font(24), fill=GRAY)
    d.text((465, 700), best, font=_fit(d, best, 40, W - 30 - 465 - 40, True), fill=GOLD if best != "—" else DIM,
           anchor="ls")
    return _to_png(img)


async def build_case_card(bot, member: discord.Member) -> discord.File:
    db = bot.db
    cases = await db.get_cases(member.id)
    best_row = await db.best_drop(member.id)
    best = "—"
    if best_row:
        best = f"{best_row['label']} ({RARITY_NAMES.get(best_row['rarity'], '')})"
    kwargs = dict(
        avatar=await _avatar_image(member), name=member.display_name, username=member.name,
        balance=fmt_n(await db.get_balance(member.id)), cases=cases,
        total_opened=await db.total_opened(member.id), best=best,
    )
    buf = await asyncio.to_thread(lambda: render_case_card(**kwargs))
    return discord.File(buf, filename="case.png")


# ================================================================ /inventory

def render_inventory_card(*, avatar, name, username, balance, wallet, items_count,
                          partner_avatar, partner_name, room_info,
                          roles: list[dict], total_opened: int, best: str) -> io.BytesIO:
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    _left_card(img, d, avatar, name, username, balance)
    _panel(img, (55, 625, 375, 725), radius=26, fill=(36, 36, 41), outline=(58, 58, 65))
    d.text((355, 642), "Монеты в инвентаре", font=_fit(d, "Монеты в инвентаре", 22, 280), fill=GRAY, anchor="ra")
    d.text((355, 712), wallet, font=_fit(d, wallet, 40, 220, True), fill=WHITE, anchor="rd")
    _coin(d, 95, 690, 14)

    # --- любовная комната ---
    _panel(img, (430, 30, 900, 330), radius=36)
    d.text((465, 70), "Любовная комната", font=_font(26), fill=GRAY)
    if partner_name:
        d.text((465, 140), partner_name, font=_fit(d, partner_name, 40, 260, True), fill=WHITE)
        d.text((465, 210), room_info, font=_fit(d, room_info, 24, 260), fill=GRAY)
        _paste_avatar(img, partner_avatar, 800, 180, 120, ring=6)
    else:
        d.text((465, 140), "Нет пары", font=_font(40, True), fill=WHITE)
        d.text((465, 210), "Команда /marry", font=_font(24), fill=GRAY)
        cx, cy, hc = 800, 180, (235, 235, 240)
        d.polygon([(cx, cy - 70), (cx + 70, cy), (cx, cy + 70), (cx - 70, cy)], outline=(200, 200, 205), width=3)
        d.ellipse((cx - 28, cy - 24, cx + 1, cy + 5), fill=hc)
        d.ellipse((cx - 1, cy - 24, cx + 28, cy + 5), fill=hc)
        d.polygon([(cx - 27, cy - 5), (cx + 27, cy - 5), (cx, cy + 26)], fill=hc)

    # --- кейсы ---
    _panel(img, (930, 30, W - 30, 330), radius=36)
    d.text((965, 70), "Кейсы", font=_font(26), fill=GRAY)
    draw_chest(d, 1290, 105, 34, RARITY_COLORS["legendary"])
    d.text((965, 150), f"Открыто: {fmt_n(total_opened)}", font=_fit(d, f"Открыто: {fmt_n(total_opened)}", 36, 380,
                                                                       True), fill=WHITE)
    d.text((965, 222), "Лучший приз", font=_font(22), fill=DIM)
    d.text((965, 255), best, font=_fit(d, best, 28, 380, True), fill=GOLD if best != "—" else DIM)
    d.text((965, 300), f"Предметов ждут в инвентаре: {items_count}",
           font=_fit(d, f"Предметов ждут в инвентаре: {items_count}", 20, 380), fill=DIM)

    # --- созданные роли ---
    _panel(img, (430, 360, W - 30, H - 30), radius=40)
    d.text((465, 398), "Мои роли на продаже", font=_font(26), fill=GRAY)
    d.line((465, 442, W - 65, 442), fill=(58, 58, 65), width=2)
    if not roles:
        d.text((465, 540), "Пока нет — создай роль в /shop", font=_font(30, True), fill=DIM, anchor="lm")
    for i, r in enumerate(roles[:3]):
        y1 = 462 + i * 92
        _panel(img, (460, y1, W - 60, y1 + 78), radius=20, fill=(36, 36, 41), outline=(58, 58, 65))
        color = r["color"] or (200, 200, 205)
        d.ellipse((484, y1 + 27, 508, y1 + 51), fill=color)
        d.text((528, y1 + 39), r["name"], font=_fit(d, r["name"], 30, 360, True), fill=color, anchor="lm")
        info = f"{fmt_n(r['price'])} · купили {r['buyers']} · заработано {fmt_n(r['earned'])}"
        if not r["listed"]:
            info += " · снята"
        d.text((W - 84, y1 + 39), info, font=_fit(d, info, 24, 470), fill=GRAY, anchor="rm")
    if len(roles) > 3:
        d.text((W - 65, H - 50), f"и ещё {len(roles) - 3}", font=_font(20), fill=DIM, anchor="rd")
    return _to_png(img)


async def build_inventory_card(bot, member: discord.Member) -> discord.File:
    db = bot.db
    now = int(time.time())
    guild = member.guild
    avatar = await _avatar_image(member)

    partner_avatar = partner_name = None
    room_info = ""
    room = await db.get_room_of(member.id)
    if room:
        pid = room["partner_id"] if room["owner_id"] == member.id else room["owner_id"]
        partner = guild.get_member(pid)
        if partner:
            partner_avatar, partner_name = await _avatar_image(partner), partner.display_name
        else:
            partner_avatar, partner_name = avatar, f"ID {pid}"
        left = max(0, room["expires_at"] - now)
        room_info = f"Осталось {left // 86400} дн. {left % 86400 // 3600} ч."

    roles = []
    for cr in await db.get_custom_roles_of(member.id):
        role = guild.get_role(cr["role_id"])
        if role is None:
            continue
        st = await db.role_sale_stats(cr["role_id"], member.id)
        roles.append({
            "name": role.name, "color": role.colour.to_rgb() if role.colour.value else None,
            "price": cr["price"], "buyers": st["buyers"], "earned": st["earned"], "listed": cr["listed"],
        })

    best_row = await db.best_drop(member.id)
    best = f"{best_row['label']} ({RARITY_NAMES.get(best_row['rarity'], '')})" if best_row else "—"
    kwargs = dict(
        avatar=avatar, name=member.display_name, username=member.name,
        balance=fmt_n(await db.get_balance(member.id)), wallet=fmt_n(await db.get_wallet(member.id)),
        items_count=len(await db.get_case_items(member.id)),
        partner_avatar=partner_avatar, partner_name=partner_name, room_info=room_info,
        roles=roles, total_opened=await db.total_opened(member.id), best=best,
    )
    buf = await asyncio.to_thread(lambda: render_inventory_card(**kwargs))
    return discord.File(buf, filename="inventory.png")


# ================================================================ любовный профиль

def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


LOVE_BG = os.path.join(BASE, "pics", "love_bg.png")      # фон любовного профиля (1920x1080)
LOVE_FONT = os.path.join(BASE, "fonts", "love_bold.otf")  # жирный шрифт для любовного профиля


def _lfont(size: int):
    try:
        return ImageFont.truetype(LOVE_FONT, size)
    except OSError:
        return _font(size, True)


def _lfit(d, text, size, max_w):
    while size > 14:
        f = _lfont(size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _lfont(14)


def render_love_card(*, avatar1, avatar2, names, together, created, fund, left, days) -> io.BytesIO:
    """Рисует любовный профиль поверх фона pics/love_bg.png (координаты под картинку 1920x1080)."""
    img = Image.open(LOVE_BG).convert("RGBA")
    if img.size != (1920, 1080):
        img = img.resize((1920, 1080))
    d = ImageDraw.Draw(img)

    # аватарки в кольцах сердца
    for cx, av in ((742, avatar1), (1146, avatar2)):
        size = 280
        circ = _circle(av, size)
        img.paste(circ, (cx - size // 2, 390 - size // 2), circ)
    d = ImageDraw.Draw(img)

    white = (255, 255, 255)
    # левые плитки: время вместе (по левому краю), дата создания (по правому краю)
    d.text((250, 463), together, font=_lfit(d, together, 36, 155), fill=white, anchor="lm")
    d.text((479, 623), created, font=_lfit(d, created, 36, 250), fill=white, anchor="rm")
    # правые плитки: баланс комнаты и сколько осталось (по правому краю)
    d.text((1727, 463), fund, font=_lfit(d, fund, 36, 160), fill=white, anchor="rm")
    d.text((1727, 623), left, font=_lfit(d, left, 36, 250), fill=white, anchor="rm")
    # по центру: сколько дней вместе (подпись «Дней» уже есть на фоне)
    d.text((945, 632), days, font=_lfit(d, days, 120, 260), fill=white, anchor="mm")
    # внизу: ники пары
    d.text((960, 928), names, font=_lfit(d, names, 36, 1040), fill=white, anchor="mm")
    return _to_png(img)


async def build_love_card(bot, member: discord.Member, room) -> discord.File:
    guild = member.guild
    now = int(time.time())
    owner = guild.get_member(room["owner_id"])
    partner = guild.get_member(room["partner_id"])
    av1 = await _avatar_image(owner) if owner else await _avatar_image(member)
    av2 = await _avatar_image(partner) if partner else av1
    keys = room.keys()
    created_at = room["created_at"] if "created_at" in keys else 0
    together = room["together_seconds"] if "together_seconds" in keys else 0
    fund = room["fund"] if "fund" in keys else 0
    left = max(0, room["expires_at"] - now)
    days_together = (now - created_at) // 86400 if created_at else None
    name1 = owner.display_name if owner else f"ID {room['owner_id']}"
    name2 = partner.display_name if partner else f"ID {room['partner_id']}"
    left_days = -(-left // 86400) if left else 0  # округляем вверх: 29.5 дня -> «30 дней»
    kwargs = dict(
        avatar1=av1, avatar2=av2, names=f"{name1} & {name2}",
        together=f"{together // 3600}ч {together % 3600 // 60}мин",
        created=time.strftime("%d.%m.%Y", time.gmtime(created_at + 7 * 3600)) if created_at else "—",
        fund=fmt_n(fund),
        left=f"{left_days} {_days_word(left_days)}",
        days=str(days_together if days_together is not None else 0),
    )
    buf = await asyncio.to_thread(lambda: render_love_card(**kwargs))
    return discord.File(buf, filename="love.png")
