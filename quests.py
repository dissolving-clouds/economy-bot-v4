"""Ежедневные задания: список заданий и подсчёт прогресса.

Цели и награды хранятся в настройках (меняются в /panel). Награда 0 = задание выключено.
Задания обновляются каждый день в 00:00 по часовому поясу config.QUEST_TZ_OFFSET_HOURS.
"""
from datetime import datetime, timedelta, timezone

import config

# kind: "messages" — цель в сообщениях, "voice" — цель в минутах в голосовых каналах
QUESTS = [
    {"key": "msg1", "kind": "messages"},
    {"key": "msg2", "kind": "messages"},
    {"key": "voice1", "kind": "voice"},
    {"key": "voice2", "kind": "voice"},
]


def _tz():
    return timezone(timedelta(hours=config.QUEST_TZ_OFFSET_HOURS))


def today() -> str:
    """Текущий «день заданий», например 2026-10-09."""
    return datetime.now(_tz()).strftime("%Y-%m-%d")


def seconds_to_reset() -> int:
    now = datetime.now(_tz())
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def fmt_minutes(minutes: int) -> str:
    """30 -> «30 мин», 120 -> «2 ч», 90 -> «1 ч 30 мин»."""
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} ч {m} мин"
    return f"{h} ч" if h else f"{m} мин"


def _plural_messages(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "сообщение"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "сообщения"
    return "сообщений"


def quest_title(kind: str, goal: int) -> str:
    if kind == "messages":
        return f"Написать {goal} {_plural_messages(goal)}"
    return f"Провести {fmt_minutes(goal)} в голосовых"


async def user_quests(db, user_id: int) -> list[dict]:
    """Список включённых заданий с прогрессом участника за сегодня."""
    day = today()
    prog = await db.quest_get(user_id, day)
    result = []
    for q in QUESTS:
        goal = await db.get_int(f"q_{q['key']}_goal")
        reward = await db.get_int(f"q_{q['key']}_reward")
        if goal <= 0 or reward <= 0:
            continue  # задание выключено
        if q["kind"] == "messages":
            progress = prog["messages"]
        else:
            progress = prog["voice_seconds"] // 60
        result.append({
            "key": q["key"],
            "kind": q["kind"],
            "goal": goal,
            "reward": reward,
            "progress": min(progress, goal),
            "title": quest_title(q["kind"], goal),
            "done": f",{q['key']}," in prog["done"],
        })
    return result
