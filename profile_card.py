"""Генерация картинки-профиля (Pillow). Файл кладётся в корень бота рядом с database.py."""
import asyncio
import io
import os
import time

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from quests import seconds_to_reset, user_quests

W, H = 1400, 780
BASE = os.path.dirname(os.path.abspath(__file__))

# Шрифт: положите fonts/font.ttf (и по желанию fonts/font_bold.ttf) — например Montserrat или Inter.
_FONT_CANDIDATES = [
    ("fonts/font.ttf", "fonts/font_bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


def _font(size: int, bold: bool = False):
    for regular, heavy in _FONT_CANDIDATES:
        path = heavy if bold else regular
        if not os.path.isabs(path):
            path = os.path.join(BASE, path)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit(draw, text, size, max_w, bold=False):
    """Уменьшает шрифт, пока текст не влезет по ширине."""
    while size > 12:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(12, bold)


def _panel(img, box, radius=30, fill=(28, 28, 32), outline=(52, 52, 58)):
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)


def _circle(avatar: Image.Image, size: int) -> Image.Image:
    avatar = avatar.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4, size * 4), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


def _paste_avatar(img, avatar, cx, cy, size, ring=8):
    d = ImageDraw.Draw(img)
    r = size // 2 + ring
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(40, 40, 46), outline=(80, 80, 90), width=3)
    img.paste(_circle(avatar, size), (cx - size // 2, cy - size // 2), _circle(avatar, size))


def _stat_tile(img, box, label, value):
    _panel(img, box, radius=24, fill=(36, 36, 41), outline=(58, 58, 65))
    d = ImageDraw.Draw(img)
    x1, y1, x2, y2 = box
    d.text((x2 - 24, y1 + 20), label, font=_fit(d, label, 24, x2 - x1 - 48), fill=(170, 170, 178), anchor="ra")
    f = _fit(d, value, 52, x2 - x1 - 48, bold=True)
    d.text((x2 - 24, y2 - 18), value, font=f, fill=(255, 255, 255), anchor="rd")


def _coin(d, cx, cy, r):
    """Маленькая монетка фигурами."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(245, 190, 40), outline=(255, 225, 110), width=2)
    d.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62), outline=(200, 140, 20), width=2)


def _check(d, cx, cy, size, fill):
    """Галочка линиями (в шрифте может не быть символа ✓)."""
    s = size
    d.line([(cx - s, cy), (cx - s * 0.3, cy + s * 0.7), (cx + s, cy - s * 0.7)], fill=fill, width=4, joint="curve")


GREEN = (110, 210, 140)
GOLD = (245, 200, 70)


def _quest_tile(img, box, q):
    _panel(img, box, radius=24, fill=(36, 36, 41), outline=(70, 110, 85) if q["done"] else (58, 58, 65))
    d = ImageDraw.Draw(img)
    x1, y1, x2, y2 = box
    pad = 22
    inner = x2 - x1 - pad * 2

    # верхняя строка: тип задания слева, награда справа
    kind = "Сообщения" if q["kind"] == "messages" else "Голосовые"
    reward = f"+{q['reward_text']}"
    rf = _font(24, True)
    rw = d.textlength(reward, font=rf)
    d.text((x2 - pad, y1 + 18), reward, font=rf, fill=GREEN if q["done"] else GOLD, anchor="ra")
    _coin(d, int(x2 - pad - rw - 16), y1 + 31, 10)
    d.text((x1 + pad, y1 + 20), kind, font=_fit(d, kind, 21, inner - rw - 50), fill=(170, 170, 178))

    # прогресс крупно
    d.text((x1 + pad, y1 + 92), q["progress_text"], font=_fit(d, q["progress_text"], 36, inner, True),
           fill=(255, 255, 255), anchor="ls")

    # полоска прогресса
    by1, by2 = y1 + 108, y1 + 120
    d.rounded_rectangle((x1 + pad, by1, x2 - pad, by2), radius=6, fill=(54, 54, 61))
    ratio = 1.0 if q["done"] else (q["progress"] / q["goal"] if q["goal"] else 0)
    if ratio > 0:
        fx = x1 + pad + max(12, int(inner * min(1.0, ratio)))
        d.rounded_rectangle((x1 + pad, by1, fx, by2), radius=6, fill=GREEN if q["done"] else (235, 235, 240))

    # подпись
    caption = "Выполнено" if q["done"] else q["title"]
    cf = _fit(d, caption, 19, inner - (30 if q["done"] else 0))
    d.text((x1 + pad, y2 - 20), caption, font=cf, fill=GREEN if q["done"] else (150, 150, 160), anchor="ld")
    if q["done"]:
        cw = d.textlength(caption, font=cf)
        _check(d, int(x1 + pad + cw + 18), y2 - 29, 8, GREEN)


QUEST_PANEL_H = 330  # высота блока заданий


def render_card(*, avatar, name, username, balance, top_voice, top_coins,
                partner_avatar=None, partner_name=None, room_info="",
                online="0ч 0м", messages="0", streak="0 дн.", roles="0", currency="",
                quests=None, quests_reset="") -> io.BytesIO:
    quests = quests or []
    height = H + (QUEST_PANEL_H if quests else 0)
    img = Image.new("RGBA", (W, height), (18, 18, 21, 255))
    d = ImageDraw.Draw(img)

    # --- левая карточка ---
    _panel(img, (30, 30, 400, H - 30), radius=40)
    _paste_avatar(img, avatar, 215, 210, 200)
    d.text((215, 400), name, font=_fit(d, name, 40, 330, True), fill=(255, 255, 255), anchor="mm")
    d.text((215, 445), f"@{username}", font=_fit(d, f"@{username}", 28, 330), fill=(150, 150, 160), anchor="mm")
    _panel(img, (55, 495, 375, 605), radius=26, fill=(36, 36, 41), outline=(58, 58, 65))
    d.text((355, 512), "Баланс", font=_font(22), fill=(170, 170, 178), anchor="ra")
    d.text((355, 590), balance, font=_fit(d, balance, 44, 200, True), fill=(255, 255, 255), anchor="rd")
    # жёлтая монетка (рисуется фигурами, без шрифта)
    cx0, cy0 = 105, 550
    d.ellipse((cx0 - 30, cy0 - 30, cx0 + 30, cy0 + 30), fill=(245, 190, 40), outline=(255, 225, 110), width=4)
    d.ellipse((cx0 - 20, cy0 - 20, cx0 + 20, cy0 + 20), outline=(200, 140, 20), width=3)
    d.rounded_rectangle((cx0 - 4, cy0 - 11, cx0 + 4, cy0 + 11), radius=3, fill=(200, 140, 20))
    _panel(img, (55, 625, 375, 725), radius=26, fill=(36, 36, 41), outline=(58, 58, 65))
    d.text((355, 642), "Серия timely", font=_font(22), fill=(170, 170, 178), anchor="ra")
    d.text((355, 712), streak, font=_fit(d, streak, 40, 290, True), fill=(255, 255, 255), anchor="rd")

    # --- два верхних блока: топ ---
    for i, (label, val) in enumerate((("Топ по онлайну", top_voice), ("Топ по монетам", top_coins))):
        x1 = 430 + i * 250
        box = (x1, 30, x1 + 230, 330)
        _panel(img, box, radius=30)
        d.text((x1 + 115, 70), label, font=_fit(d, label, 24, 200), fill=(170, 170, 178), anchor="mm")
        d.line((x1 + 20, 105, x1 + 210, 105), fill=(58, 58, 65), width=2)
        d.text((x1 + 115, 200), val, font=_fit(d, val, 80, 200, True), fill=(255, 255, 255), anchor="mm")
        d.text((x1 + 115, 285), "место", font=_font(24), fill=(150, 150, 160), anchor="mm")

    # --- любовная комната ---
    _panel(img, (940, 30, W - 30, 330), radius=40)
    d.text((975, 70), "Любовная комната", font=_font(26), fill=(170, 170, 178))
    text_w = 205  # текст не заходит на значок справа
    if partner_name:
        d.text((975, 140), partner_name, font=_fit(d, partner_name, 42, text_w, True), fill=(255, 255, 255))
        d.text((975, 215), room_info, font=_fit(d, room_info, 24, text_w), fill=(170, 170, 178))
        _paste_avatar(img, partner_avatar, 1275, 180, 110, ring=6)
    else:
        d.text((975, 140), "Нет партнёра", font=_fit(d, "Нет партнёра", 42, text_w, True), fill=(255, 255, 255))
        d.text((975, 215), "Команда /marry", font=_fit(d, "Команда /marry", 24, text_w), fill=(170, 170, 178))
        cx, cy = 1275, 180
        d.polygon([(cx, cy - 70), (cx + 70, cy), (cx, cy + 70), (cx - 70, cy)], outline=(200, 200, 205), width=3)
        # сердечко фигурами (шрифт символ ♥ не умеет)
        hc = (235, 235, 240)
        d.ellipse((cx - 28, cy - 24, cx + 1, cy + 5), fill=hc)
        d.ellipse((cx - 1, cy - 24, cx + 28, cy + 5), fill=hc)
        d.polygon([(cx - 27, cy - 5), (cx + 27, cy - 5), (cx, cy + 26)], fill=hc)

    # --- нижний блок: статистика ---
    _panel(img, (430, 360, W - 30, H - 30), radius=40)
    tiles = (("Онлайн", online), ("Сообщения", messages), ("Роли в инвентаре", roles), ("Монеты", balance))
    tw, th = 427, 150
    for i, (label, val) in enumerate(tiles):
        x1 = 460 + (i % 2) * (tw + 26)
        y1 = 390 + (i // 2) * (th + 30)
        _stat_tile(img, (x1, y1, x1 + tw, y1 + th), label, val)

    # --- ежедневные задания (на всю ширину снизу) ---
    if quests:
        top = H
        _panel(img, (30, top, W - 30, top + QUEST_PANEL_H - 30), radius=40)
        done = sum(1 for q in quests if q["done"])
        d.text((65, top + 30), "Ежедневные задания", font=_font(26), fill=(170, 170, 178))
        d.text((W - 65, top + 32), f"{done}/{len(quests)} · обновятся через {quests_reset}",
               font=_font(22), fill=(130, 130, 140), anchor="ra")
        d.line((65, top + 78, W - 65, top + 78), fill=(58, 58, 65), width=2)
        n = len(quests)
        gap = 22
        left, right = 60, W - 60
        tile_w = (right - left - gap * (n - 1)) / n
        for i, quest in enumerate(quests):
            x1 = int(left + i * (tile_w + gap))
            _quest_tile(img, (x1, top + 104, int(x1 + tile_w), top + 274), quest)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf


def _mins(minutes: int) -> str:
    """Короткая запись минут для плитки: 45м, 2ч, 1ч30м."""
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}ч{m:02d}м"
    return f"{h}ч" if h else f"{m}м"


def _voice(seconds: int) -> str:
    return f"{seconds // 3600}ч {seconds % 3600 // 60}м"


async def _avatar_image(member) -> Image.Image:
    data = await member.display_avatar.replace(size=256, format="png").read()
    return Image.open(io.BytesIO(data))


async def build_profile_card(bot, member: discord.Member) -> discord.File:
    """Собирает реальные данные из БД и возвращает discord.File с картинкой."""
    db = bot.db
    now = int(time.time())
    user = await db.get_user(member.id)
    stats = await db.get_stats(member.id)
    rank_coins = await db.rank_balance(member.id)
    rank_voice = await db.rank_stat(member.id, "voice_seconds")
    roles = len(await db.get_inventory(member.id, now))
    currency = await db.get("currency")

    avatar = await _avatar_image(member)
    partner_avatar = partner_name = None
    room_info = ""
    room = await db.get_room_of(member.id)
    if room:
        pid = room["partner_id"] if room["owner_id"] == member.id else room["owner_id"]
        partner = member.guild.get_member(pid)
        if partner:
            partner_avatar = await _avatar_image(partner)
            partner_name = partner.display_name
        else:
            partner_avatar = avatar
            partner_name = f"ID {pid}"
        left = max(0, (room["expires_at"] - now) // 86400)
        room_info = f"Осталось {left} дн."

    quests = []
    for it in await user_quests(db, member.id):
        if it["kind"] == "voice":
            progress_text = f"{_mins(it['progress'])} / {_mins(it['goal'])}"
        else:
            progress_text = f"{it['progress']} / {it['goal']}"
        quests.append({**it, "progress_text": progress_text, "reward_text": f"{it['reward']:,}".replace(",", " ")})
    left = seconds_to_reset()
    quests_reset = f"{left // 3600}ч {left % 3600 // 60}м"

    kwargs = dict(
        avatar=avatar, name=member.display_name, username=member.name,
        balance=f"{user['balance']:,}".replace(",", " "), top_voice=f"#{rank_voice}", top_coins=f"#{rank_coins}",
        partner_avatar=partner_avatar, partner_name=partner_name, room_info=room_info,
        online=_voice(stats["voice_seconds"]), messages=f"{stats['messages']:,}".replace(",", " "),
        streak=f"{user['streak']} дн.", roles=str(roles), currency=currency,
        quests=quests, quests_reset=quests_reset,
    )
    buf = await asyncio.to_thread(lambda: render_card(**kwargs))
    return discord.File(buf, filename="profile.png")
