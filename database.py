"""Работа с базой данных (SQLite). Все данные хранятся в файле economy.db."""
import aiosqlite

DEFAULT_SETTINGS = {
    "currency": "🪙",              # значок валюты
    "daily_amount": "100",         # награда за /daily
    "daily_streak_bonus": "10",    # бонус за каждый день подряд (макс. 6 дней)
    "daily_cooldown_hours": "24",  # раз в сколько часов можно брать /daily
    "max_bet": "10000",            # максимальная ставка в казино и дуэлях
    "duel_commission": "5",        # комиссия с дуэли, %
    "room_price": "5000",          # цена любовной комнаты
    "room_days": "30",             # на сколько дней покупается комната
    "room_category_id": "0",       # ID категории для комнат (0 = без категории)
    "room_role_id": "0",           # ID роли, которую получают участники комнаты (0 = не выдавать)
    "transfer_commission": "10",   # комиссия за переводы между игроками, %
    "role_commission": "5",        # комиссия с продажи роли игрока, %
    "user_role_days": "15",        # срок жизни роли игрока (дни)
    "user_role_limit": "3",        # сколько ролей может создать один игрок
    "voice_reward": "1",           # монет за каждую минуту в голосовом канале (0 = выключено)
    # --- кастомные голосовые комнаты ---
    "croom_price": "3000",         # цена кастомной комнаты
    "croom_days": "0",             # срок комнаты в днях (0 = навсегда)
    "croom_category_id": "0",      # категория для кастомных комнат (0 = категория канала «Войти»)
    "case_croom_days": "7",        # сколько дней живёт кастомная комната из кейса (0 = навсегда)
    # --- обмен предметов из кейсов на монеты (0 = этот предмет обменять нельзя) ---
    "sell_role": "1000",           # обычная роль
    "sell_permrole": "2000",       # роль с правами
    "sell_croom": "5000",          # кастомная комната
    "sell_loveroom": "3000",       # любовная комната
    # --- ежедневные задания (цель / награда; награда 0 = задание выключено) ---
    "q_msg1_goal": "50",           # сообщений
    "q_msg1_reward": "100",
    "q_msg2_goal": "200",          # сообщений
    "q_msg2_reward": "300",
    "q_voice1_goal": "30",         # минут в голосовых
    "q_voice1_reward": "100",
    "q_voice2_goal": "120",        # минут в голосовых
    "q_voice2_reward": "300",
    # --- кейсы: шанс получить кейс, % (за одно сообщение / за минуту в голосовом) ---
    "case_msg_common": "2",
    "case_msg_rare": "0.5",
    "case_msg_legendary": "0.05",
    "case_voice_common": "1",
    "case_voice_rare": "0.25",
    "case_voice_legendary": "0.02",
    "case_perm_role_days": "3",    # сколько дней действует «роль с правами» из кейса
    "case_dup_coins": "3000",      # замена, если выпала роль, которая уже есть у участника
    "case_loot_seeded": "0",       # служебное: стартовые призы уже добавлены
    # --- сапёр ---
    "miner_step": "10",            # +% к выигрышу за каждую открытую пустую клетку
    "miner_mines_min": "3",        # бомб на поле 5x4 (20 клеток)
    "miner_mines_max": "4",
    # --- топ дня по голосовому онлайну ---
    "voice_top_reward": "500",     # награда самому активному в голосовых за сутки
    "voice_top_msg_id": "0",       # служебное: id последнего сообщения (чтобы удалить его)
    "voice_top_last_day": "",      # служебное: за какой день топ уже подведён
}

RARITIES = ("common", "rare", "legendary")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shop_roles (
    role_id INTEGER PRIMARY KEY,
    price INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rooms (
    owner_id INTEGER PRIMARY KEY,
    partner_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    role_id INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER NOT NULL DEFAULT 0   -- 0 = голосового канала сейчас нет
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,   -- сколько списано с отправителя
    fee INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'pay',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions (from_id);
CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions (to_id);
CREATE TABLE IF NOT EXISTS roulette_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_roles (
    role_id INTEGER PRIMARY KEY,
    creator_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    listed INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS inventory (
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL DEFAULT 0,
    equipped INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, role_id)
);
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    messages INTEGER NOT NULL DEFAULT 0,
    voice_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS custom_rooms (
    owner_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL DEFAULT 0,
    role_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL DEFAULT 0   -- 0 = навсегда
);
CREATE TABLE IF NOT EXISTS quest_progress (
    user_id INTEGER PRIMARY KEY,
    day TEXT NOT NULL DEFAULT '',
    messages INTEGER NOT NULL DEFAULT 0,
    voice_seconds INTEGER NOT NULL DEFAULT 0,
    done TEXT NOT NULL DEFAULT ','          -- выполненные сегодня задания: ",msg1,voice1,"
);
CREATE TABLE IF NOT EXISTS user_cases (
    user_id INTEGER NOT NULL,
    rarity TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,       -- сколько кейсов есть сейчас
    opened INTEGER NOT NULL DEFAULT 0,      -- сколько открыто за всё время
    PRIMARY KEY (user_id, rarity)
);
CREATE TABLE IF NOT EXISTS case_loot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rarity TEXT NOT NULL,
    kind TEXT NOT NULL,                     -- coins / role / permrole / croom / loveroom
    role_id INTEGER NOT NULL DEFAULT 0,
    amount_min INTEGER NOT NULL DEFAULT 0,
    amount_max INTEGER NOT NULL DEFAULT 0,
    days INTEGER NOT NULL DEFAULT 0,        -- срок роли (0 = навсегда)
    weight REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS case_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                     -- role / permrole / croom / loveroom
    role_id INTEGER NOT NULL DEFAULT 0,
    days INTEGER NOT NULL DEFAULT 0,
    rarity TEXT NOT NULL DEFAULT 'common',
    created_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_items_user ON case_items (user_id);
CREATE TABLE IF NOT EXISTS coin_wallet (
    user_id INTEGER PRIMARY KEY,
    amount INTEGER NOT NULL DEFAULT 0       -- монеты из кейсов, которые ещё не забраны на баланс
);
CREATE TABLE IF NOT EXISTS case_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    rarity TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    chance REAL NOT NULL DEFAULT 1          -- вероятность этого приза внутри кейса (0..1)
);
CREATE INDEX IF NOT EXISTS idx_case_log_user ON case_log (user_id);
CREATE TABLE IF NOT EXISTS case_drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    rarity TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'msg',     -- msg (сообщение) / voice (голосовой) / admin (выдала администрация)
    count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_case_drops_user ON case_drops (user_id);
CREATE TABLE IF NOT EXISTS daily_voice (
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);
CREATE TABLE IF NOT EXISTS role_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    buyer_id INTEGER NOT NULL,
    earned INTEGER NOT NULL DEFAULT 0,      -- сколько получил автор (после комиссии)
    kind TEXT NOT NULL DEFAULT 'buy'        -- buy / renew
);
CREATE INDEX IF NOT EXISTS idx_role_sales_role ON role_sales (role_id);
"""

STAT_COLUMNS = {"messages", "voice_seconds", "quests_done"}
RARITY_RANK = {"common": 1, "rare": 2, "legendary": 3}

# Стартовые призы кейсов (добавляются один раз; потом всё меняется в /panel_case)
DEFAULT_LOOT = [
    # rarity, kind, amount_min, amount_max, weight
    ("common", "coins", 50, 150, 85),
    ("common", "coins", 200, 400, 15),
    ("rare", "coins", 300, 700, 80),
    ("rare", "croom", 0, 0, 10),
    ("rare", "loveroom", 0, 0, 10),
    ("legendary", "coins", 1500, 3000, 70),
    ("legendary", "croom", 0, 0, 20),
    ("legendary", "loveroom", 0, 0, 10),
]


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()
        await self._seed_loot()

    async def _seed_loot(self):
        if await self.get("case_loot_seeded") == "1":
            return
        async with self.conn.execute("SELECT COUNT(*) AS c FROM case_loot") as cur:
            empty = (await cur.fetchone())["c"] == 0
        if empty:
            await self.conn.executemany(
                "INSERT INTO case_loot (rarity, kind, amount_min, amount_max, weight) VALUES (?, ?, ?, ?, ?)",
                DEFAULT_LOOT,
            )
        await self.set("case_loot_seeded", "1")

    async def _migrate(self):
        """Дополняет старую базу новыми колонками (данные сохраняются)."""
        async with self.conn.execute("PRAGMA table_info(stats)") as cur:
            stat_cols = {r["name"] for r in await cur.fetchall()}
        if "quests_done" not in stat_cols:
            await self.conn.execute("ALTER TABLE stats ADD COLUMN quests_done INTEGER NOT NULL DEFAULT 0")
        # откуда роль в инвентаре: shop (куплена/создана), case (из кейса), perm (роль с правами из кейса),
        # own (была у участника раньше — спрятана через /inventory)
        async with self.conn.execute("PRAGMA table_info(inventory)") as cur:
            inv_cols = {r["name"] for r in await cur.fetchall()}
        if "source" not in inv_cols:
            await self.conn.execute("ALTER TABLE inventory ADD COLUMN source TEXT NOT NULL DEFAULT 'shop'")
        # Таблица transactions могла быть создана раньше с другой схемой (нет колонки ts и т.п.)
        async with self.conn.execute("PRAGMA table_info(transactions)") as cur:
            tx_cols = {r["name"] for r in await cur.fetchall()}
        need = {"ts", "from_id", "to_id", "amount", "fee", "kind", "note"}
        if not need <= tx_cols:
            await self.conn.executescript("""
                DROP INDEX IF EXISTS idx_tx_from;
                DROP INDEX IF EXISTS idx_tx_to;
                DROP TABLE IF EXISTS transactions_old;
                ALTER TABLE transactions RENAME TO transactions_old;
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    from_id INTEGER NOT NULL,
                    to_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    fee INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'pay',
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX idx_tx_from ON transactions (from_id);
                CREATE INDEX idx_tx_to ON transactions (to_id);
            """)
        # Старая таблица roulette_log могла быть без нужных колонок (ts и т.п.) — пересобираем
        async with self.conn.execute("PRAGMA table_info(roulette_log)") as cur:
            rl_cols = {r["name"] for r in await cur.fetchall()}
        if not {"ts", "user_id", "number", "color"} <= rl_cols:
            await self.conn.executescript("""
                DROP TABLE roulette_log;
                CREATE TABLE roulette_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    color TEXT NOT NULL
                );
            """)
            await self.conn.commit()

        async with self.conn.execute("PRAGMA table_info(rooms)") as cur:
            info = {r["name"]: r["pk"] for r in await cur.fetchall()}
        # Старая таблица комнат: ключом был channel_id. Пересобираем с ключом owner_id.
        if info.get("owner_id", 0) == 0:
            role_expr = "role_id" if "role_id" in info else "0"
            await self.conn.executescript(f"""
                CREATE TABLE rooms_new (
                    owner_id INTEGER PRIMARY KEY,
                    partner_id INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    role_id INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO rooms_new (owner_id, partner_id, expires_at, role_id, channel_id)
                    SELECT owner_id, partner_id, expires_at, {role_expr}, channel_id FROM rooms;
                DROP TABLE rooms;
                ALTER TABLE rooms_new RENAME TO rooms;
            """)
        # любовные комнаты: дата создания, баланс комнаты, время вместе в комнате
        async with self.conn.execute("PRAGMA table_info(rooms)") as cur:
            room_cols = {r["name"] for r in await cur.fetchall()}
        if "created_at" not in room_cols:
            await self.conn.execute("ALTER TABLE rooms ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
        if "together_seconds" not in room_cols:
            await self.conn.execute("ALTER TABLE rooms ADD COLUMN together_seconds INTEGER NOT NULL DEFAULT 0")
        if "fund" not in room_cols:
            await self.conn.execute("ALTER TABLE rooms ADD COLUMN fund INTEGER NOT NULL DEFAULT 0")
            if {"fund_owner", "fund_partner"} <= room_cols:  # копилка из старой версии бота
                await self.conn.execute("UPDATE rooms SET fund = fund_owner + fund_partner")

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ---------- Настройки ----------
    async def get(self, key: str) -> str:
        async with self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else DEFAULT_SETTINGS[key]

    async def get_int(self, key: str) -> int:
        try:
            return int(await self.get(key))
        except ValueError:
            return int(DEFAULT_SETTINGS[key])

    async def get_float(self, key: str) -> float:
        try:
            return float(str(await self.get(key)).replace(",", "."))
        except ValueError:
            return float(DEFAULT_SETTINGS[key])

    async def set(self, key: str, value: str):
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await self.conn.commit()

    async def fmt(self, amount: int) -> str:
        currency = await self.get("currency")
        return f"{amount:,}".replace(",", " ") + f" {currency}"

    # ---------- Пользователи и баланс ----------
    async def ensure_user(self, user_id: int):
        await self.conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await self.conn.commit()

    async def get_user(self, user_id: int):
        await self.ensure_user(user_id)
        async with self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()

    async def get_balance(self, user_id: int) -> int:
        return (await self.get_user(user_id))["balance"]

    async def add_balance(self, user_id: int, amount: int) -> int:
        """Добавляет (или отнимает, если amount < 0) монеты. Баланс не уходит ниже 0."""
        await self.ensure_user(user_id)
        await self.conn.execute(
            "UPDATE users SET balance = MAX(0, balance + ?) WHERE user_id=?", (amount, user_id)
        )
        await self.conn.commit()
        return await self.get_balance(user_id)

    async def try_remove(self, user_id: int, amount: int) -> bool:
        """Списывает монеты, только если их хватает."""
        await self.ensure_user(user_id)
        cur = await self.conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
            (amount, user_id, amount),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def claim_daily(self, user_id: int, now: int, streak: int, reward: int):
        await self.ensure_user(user_id)
        await self.conn.execute(
            "UPDATE users SET balance = balance + ?, last_daily=?, streak=? WHERE user_id=?",
            (reward, now, streak, user_id),
        )
        await self.conn.commit()

    async def top(self, limit: int = 10):
        async with self.conn.execute(
            "SELECT user_id, balance FROM users WHERE balance > 0 ORDER BY balance DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()

    async def rank_balance(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        async with self.conn.execute(
            "SELECT COUNT(*) + 1 AS r FROM users "
            "WHERE balance > (SELECT balance FROM users WHERE user_id=?)",
            (user_id,),
        ) as cur:
            return (await cur.fetchone())["r"]

    async def stats(self):
        async with self.conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(balance), 0) AS s FROM users"
        ) as cur:
            users = await cur.fetchone()
        async with self.conn.execute("SELECT COUNT(*) AS c FROM shop_roles") as cur:
            shop = await cur.fetchone()
        async with self.conn.execute("SELECT COUNT(*) AS c FROM custom_roles WHERE listed=1") as cur:
            custom = await cur.fetchone()
        async with self.conn.execute("SELECT COUNT(*) AS c FROM rooms") as cur:
            rooms = await cur.fetchone()
        return users["c"], users["s"], shop["c"], custom["c"], rooms["c"]

    # ---------- Сообщения и голосовые ----------
    async def add_message(self, user_id: int):
        await self.conn.execute(
            "INSERT INTO stats (user_id, messages) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET messages = messages + 1",
            (user_id,),
        )
        await self.conn.commit()

    async def add_voice(self, user_ids: list[int], seconds: int, reward: int = 0):
        """Добавляет время в голосовых и (если reward > 0) монеты каждому из списка."""
        await self.conn.executemany(
            "INSERT INTO stats (user_id, voice_seconds) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET voice_seconds = voice_seconds + excluded.voice_seconds",
            [(uid, seconds) for uid in user_ids],
        )
        if reward > 0:
            await self.conn.executemany(
                "INSERT INTO users (user_id, balance) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance",
                [(uid, reward) for uid in user_ids],
            )
        await self.conn.commit()

    async def get_stats(self, user_id: int):
        await self.conn.execute("INSERT OR IGNORE INTO stats (user_id) VALUES (?)", (user_id,))
        await self.conn.commit()
        async with self.conn.execute("SELECT * FROM stats WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()

    async def rank_stat(self, user_id: int, column: str) -> int:
        if column not in STAT_COLUMNS:
            raise ValueError(column)
        await self.get_stats(user_id)
        async with self.conn.execute(
            f"SELECT COUNT(*) + 1 AS r FROM stats "
            f"WHERE {column} > (SELECT {column} FROM stats WHERE user_id=?)",
            (user_id,),
        ) as cur:
            return (await cur.fetchone())["r"]

    async def top_stat(self, column: str, limit: int = 10):
        if column not in STAT_COLUMNS:
            raise ValueError(column)
        async with self.conn.execute(
            f"SELECT user_id, {column} AS value FROM stats WHERE {column} > 0 "
            f"ORDER BY {column} DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()

    # ---------- Роли сервера (магазин администратора) ----------
    async def add_shop_role(self, role_id: int, price: int, description: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO shop_roles (role_id, price, description) VALUES (?, ?, ?)",
            (role_id, price, description),
        )
        await self.conn.commit()

    async def remove_shop_role(self, role_id: int):
        await self.conn.execute("DELETE FROM shop_roles WHERE role_id=?", (role_id,))
        await self.conn.commit()

    async def get_shop_roles(self):
        async with self.conn.execute("SELECT * FROM shop_roles ORDER BY price") as cur:
            return await cur.fetchall()

    async def get_shop_role(self, role_id: int):
        async with self.conn.execute("SELECT * FROM shop_roles WHERE role_id=?", (role_id,)) as cur:
            return await cur.fetchone()

    # ---------- Роли игроков ----------
    async def add_custom_role(self, role_id: int, creator_id: int, price: int, expires_at: int):
        await self.conn.execute(
            "INSERT INTO custom_roles (role_id, creator_id, price, expires_at, listed) VALUES (?, ?, ?, ?, 1)",
            (role_id, creator_id, price, expires_at),
        )
        await self.conn.commit()

    async def get_custom_role(self, role_id: int):
        async with self.conn.execute("SELECT * FROM custom_roles WHERE role_id=?", (role_id,)) as cur:
            return await cur.fetchone()

    async def get_listed_custom_roles(self, now: int):
        async with self.conn.execute(
            "SELECT * FROM custom_roles WHERE listed=1 AND expires_at>? ORDER BY price", (now,)
        ) as cur:
            return await cur.fetchall()

    async def all_custom_roles(self):
        async with self.conn.execute("SELECT * FROM custom_roles") as cur:
            return await cur.fetchall()

    async def count_custom_roles_of(self, creator_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM custom_roles WHERE creator_id=?", (creator_id,)
        ) as cur:
            return (await cur.fetchone())["c"]

    async def set_custom_listed(self, role_id: int, listed: int):
        await self.conn.execute("UPDATE custom_roles SET listed=? WHERE role_id=?", (listed, role_id))
        await self.conn.commit()

    async def update_custom_expiry(self, role_id: int, expires_at: int):
        await self.conn.execute("UPDATE custom_roles SET expires_at=? WHERE role_id=?", (expires_at, role_id))
        await self.conn.commit()

    async def get_expired_custom_roles(self, now: int):
        async with self.conn.execute("SELECT * FROM custom_roles WHERE expires_at<=?", (now,)) as cur:
            return await cur.fetchall()

    async def delete_custom_role(self, role_id: int):
        """Удаляет роль игрока и все её записи в инвентарях."""
        await self.conn.execute("DELETE FROM custom_roles WHERE role_id=?", (role_id,))
        await self.conn.execute("DELETE FROM inventory WHERE role_id=?", (role_id,))
        await self.conn.commit()

    # ---------- Инвентарь ----------
    async def add_item(self, user_id: int, role_id: int, expires_at: int, equipped: int = 1, source: str = "shop"):
        await self.conn.execute(
            "INSERT OR REPLACE INTO inventory (user_id, role_id, expires_at, equipped, source) VALUES (?, ?, ?, ?, ?)",
            (user_id, role_id, expires_at, equipped, source),
        )
        await self.conn.commit()

    async def get_item(self, user_id: int, role_id: int):
        async with self.conn.execute(
            "SELECT * FROM inventory WHERE user_id=? AND role_id=?", (user_id, role_id)
        ) as cur:
            return await cur.fetchone()

    async def get_inventory(self, user_id: int, now: int):
        async with self.conn.execute(
            "SELECT * FROM inventory WHERE user_id=? AND (expires_at=0 OR expires_at>?) ORDER BY role_id",
            (user_id, now),
        ) as cur:
            return await cur.fetchall()

    async def set_equipped(self, user_id: int, role_id: int, equipped: int):
        await self.conn.execute(
            "UPDATE inventory SET equipped=? WHERE user_id=? AND role_id=?", (equipped, user_id, role_id)
        )
        await self.conn.commit()

    async def set_item_expiry(self, user_id: int, role_id: int, expires_at: int):
        await self.conn.execute(
            "UPDATE inventory SET expires_at=? WHERE user_id=? AND role_id=?", (expires_at, user_id, role_id)
        )
        await self.conn.commit()

    async def remove_item(self, user_id: int, role_id: int):
        await self.conn.execute("DELETE FROM inventory WHERE user_id=? AND role_id=?", (user_id, role_id))
        await self.conn.commit()

    async def get_expired_items(self, now: int):
        async with self.conn.execute(
            "SELECT * FROM inventory WHERE expires_at>0 AND expires_at<=?", (now,)
        ) as cur:
            return await cur.fetchall()

    # ---------- Любовные комнаты ----------
    async def add_room(self, owner_id: int, partner_id: int, expires_at: int, role_id: int, created_at: int = 0):
        await self.conn.execute(
            "INSERT INTO rooms (owner_id, partner_id, expires_at, role_id, channel_id, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (owner_id, partner_id, expires_at, role_id, created_at),
        )
        await self.conn.commit()

    async def get_room_of(self, user_id: int):
        async with self.conn.execute(
            "SELECT * FROM rooms WHERE owner_id=? OR partner_id=?", (user_id, user_id)
        ) as cur:
            return await cur.fetchone()

    async def get_room_by_channel(self, channel_id: int):
        async with self.conn.execute("SELECT * FROM rooms WHERE channel_id=?", (channel_id,)) as cur:
            return await cur.fetchone()

    async def all_rooms(self):
        async with self.conn.execute("SELECT * FROM rooms") as cur:
            return await cur.fetchall()

    async def set_room_channel(self, owner_id: int, channel_id: int):
        await self.conn.execute("UPDATE rooms SET channel_id=? WHERE owner_id=?", (channel_id, owner_id))
        await self.conn.commit()

    async def set_room_expiry(self, owner_id: int, expires_at: int):
        await self.conn.execute("UPDATE rooms SET expires_at=? WHERE owner_id=?", (expires_at, owner_id))
        await self.conn.commit()

    async def add_room_fund(self, owner_id: int, amount: int):
        await self.conn.execute("UPDATE rooms SET fund = fund + ? WHERE owner_id=?", (amount, owner_id))
        await self.conn.commit()

    async def take_room_fund(self, owner_id: int, amount: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE rooms SET fund = fund - ? WHERE owner_id=? AND fund >= ?", (amount, owner_id, amount)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def add_room_together(self, owner_id: int, seconds: int):
        await self.conn.execute(
            "UPDATE rooms SET together_seconds = together_seconds + ? WHERE owner_id=?", (seconds, owner_id)
        )
        await self.conn.commit()

    async def clear_room_role(self, role_id: int):
        await self.conn.execute("UPDATE rooms SET role_id=0 WHERE role_id=?", (role_id,))
        await self.conn.commit()

    # ---------- уборка: роль удалена с сервера ----------
    async def purge_role(self, role_id: int):
        """Удаляет роль отовсюду: магазин, роли игроков, инвентари, призы и предметы кейсов."""
        for sql in (
            "DELETE FROM shop_roles WHERE role_id=?",
            "DELETE FROM custom_roles WHERE role_id=?",
            "DELETE FROM inventory WHERE role_id=?",
            "DELETE FROM case_loot WHERE role_id=? AND kind IN ('role', 'permrole')",
            "DELETE FROM case_items WHERE role_id=? AND kind IN ('role', 'permrole')",
        ):
            await self.conn.execute(sql, (role_id,))
        await self.conn.execute("UPDATE rooms SET role_id=0 WHERE role_id=?", (role_id,))
        await self.conn.commit()

    async def known_role_ids(self) -> "set[int]":
        ids = set()
        for sql in ("SELECT role_id FROM shop_roles", "SELECT role_id FROM custom_roles",
                    "SELECT DISTINCT role_id FROM inventory",
                    "SELECT DISTINCT role_id FROM case_loot WHERE role_id != 0",
                    "SELECT DISTINCT role_id FROM case_items WHERE role_id != 0"):
            async with self.conn.execute(sql) as cur:
                ids |= {r[0] for r in await cur.fetchall()}
        return ids

    async def get_croom_by(self, channel_id: int = 0, role_id: int = 0):
        async with self.conn.execute(
            "SELECT * FROM custom_rooms WHERE (channel_id=? AND channel_id != 0) OR (role_id=? AND role_id != 0)",
            (channel_id, role_id),
        ) as cur:
            return await cur.fetchone()

    async def delete_room(self, owner_id: int):
        await self.conn.execute("DELETE FROM rooms WHERE owner_id=?", (owner_id,))
        await self.conn.commit()

    async def add_quest_done(self, user_id: int):
        await self.conn.execute(
            "INSERT INTO stats (user_id, quests_done) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET quests_done = quests_done + 1",
            (user_id,),
        )
        await self.conn.commit()

    # ---------- Голосовой онлайн по дням (для топа дня) ----------
    async def add_daily_voice(self, user_ids: list[int], day: str, seconds: int):
        await self.conn.executemany(
            "INSERT INTO daily_voice (day, user_id, seconds) VALUES (?, ?, ?) "
            "ON CONFLICT(day, user_id) DO UPDATE SET seconds = seconds + excluded.seconds",
            [(day, uid, seconds) for uid in user_ids],
        )
        await self.conn.commit()

    async def top_daily_voice(self, day: str, limit: int = 1):
        async with self.conn.execute(
            "SELECT user_id, seconds FROM daily_voice WHERE day=? ORDER BY seconds DESC LIMIT ?", (day, limit)
        ) as cur:
            return await cur.fetchall()

    async def cleanup_daily_voice(self, before_day: str):
        await self.conn.execute("DELETE FROM daily_voice WHERE day < ?", (before_day,))
        await self.conn.commit()

    # ---------- Кейсы ----------
    async def add_cases(self, user_id: int, rarity: str, n: int = 1):
        await self.conn.execute(
            "INSERT INTO user_cases (user_id, rarity, count) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, rarity) DO UPDATE SET count = MAX(0, count + excluded.count)",
            (user_id, rarity, n),
        )
        await self.conn.commit()

    async def get_cases(self, user_id: int) -> dict:
        """{rarity: {"count": n, "opened": m}} для всех редкостей."""
        result = {r: {"count": 0, "opened": 0} for r in RARITIES}
        async with self.conn.execute("SELECT * FROM user_cases WHERE user_id=?", (user_id,)) as cur:
            for row in await cur.fetchall():
                if row["rarity"] in result:
                    result[row["rarity"]] = {"count": row["count"], "opened": row["opened"]}
        return result

    async def take_case(self, user_id: int, rarity: str) -> bool:
        """Забирает один кейс на открытие. False — если кейсов нет."""
        cur = await self.conn.execute(
            "UPDATE user_cases SET count = count - 1, opened = opened + 1 "
            "WHERE user_id=? AND rarity=? AND count > 0",
            (user_id, rarity),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_loot(self, rarity: str | None = None):
        if rarity is None:
            sql, args = "SELECT * FROM case_loot ORDER BY rarity, weight DESC", ()
        else:
            sql, args = "SELECT * FROM case_loot WHERE rarity=? ORDER BY weight DESC, id", (rarity,)
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def get_loot_entry(self, loot_id: int):
        async with self.conn.execute("SELECT * FROM case_loot WHERE id=?", (loot_id,)) as cur:
            return await cur.fetchone()

    async def add_loot(self, rarity: str, kind: str, weight: float, role_id: int = 0,
                       amount_min: int = 0, amount_max: int = 0, days: int = 0):
        await self.conn.execute(
            "INSERT INTO case_loot (rarity, kind, role_id, amount_min, amount_max, days, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rarity, kind, role_id, amount_min, amount_max, days, weight),
        )
        await self.conn.commit()

    async def update_loot(self, loot_id: int, **fields):
        allowed = {"weight", "amount_min", "amount_max", "days"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.conn.execute(f"UPDATE case_loot SET {sets} WHERE id=?", (*fields.values(), loot_id))
        await self.conn.commit()

    async def delete_loot(self, loot_id: int):
        await self.conn.execute("DELETE FROM case_loot WHERE id=?", (loot_id,))
        await self.conn.commit()

    async def add_case_item(self, user_id: int, kind: str, role_id: int, days: int, rarity: str, now: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO case_items (user_id, kind, role_id, days, rarity, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, kind, role_id, days, rarity, now),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_case_items(self, user_id: int):
        async with self.conn.execute(
            "SELECT * FROM case_items WHERE user_id=? ORDER BY id", (user_id,)
        ) as cur:
            return await cur.fetchall()

    async def get_case_item(self, item_id: int):
        async with self.conn.execute("SELECT * FROM case_items WHERE id=?", (item_id,)) as cur:
            return await cur.fetchone()

    async def take_case_item(self, item_id: int, user_id: int) -> bool:
        """Удаляет предмет из инвентаря. True — если он был и принадлежал этому участнику."""
        cur = await self.conn.execute("DELETE FROM case_items WHERE id=? AND user_id=?", (item_id, user_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def restore_case_item(self, item):
        """Возвращает предмет (если использовать его не получилось)."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO case_items (id, user_id, kind, role_id, days, rarity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["user_id"], item["kind"], item["role_id"], item["days"], item["rarity"],
             item["created_at"]),
        )
        await self.conn.commit()

    async def has_case_role_item(self, user_id: int, role_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM case_items WHERE user_id=? AND role_id=? AND kind IN ('role', 'permrole') LIMIT 1",
            (user_id, role_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def get_wallet(self, user_id: int) -> int:
        async with self.conn.execute("SELECT amount FROM coin_wallet WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row["amount"] if row else 0

    async def add_wallet(self, user_id: int, amount: int):
        await self.conn.execute(
            "INSERT INTO coin_wallet (user_id, amount) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
            (user_id, amount),
        )
        await self.conn.commit()

    async def wallet_to_balance(self, user_id: int, amount: int) -> bool:
        """Переносит монеты из инвентаря на баланс. False — если в инвентаре столько нет."""
        cur = await self.conn.execute(
            "UPDATE coin_wallet SET amount = amount - ? WHERE user_id=? AND amount >= ?",
            (amount, user_id, amount),
        )
        if cur.rowcount == 0:
            return False
        await self.conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await self.conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        await self.conn.commit()
        return True

    async def add_case_log(self, ts: int, user_id: int, rarity: str, kind: str, label: str, chance: float):
        await self.conn.execute(
            "INSERT INTO case_log (ts, user_id, rarity, kind, label, chance) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, user_id, rarity, kind, label, chance),
        )
        await self.conn.commit()

    async def get_case_log(self, user_id: int, limit: int = 10, offset: int = 0):
        """Что выпало из открытых кейсов (новые сверху)."""
        async with self.conn.execute(
            "SELECT * FROM case_log WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset)
        ) as cur:
            return await cur.fetchall()

    async def count_case_log(self, user_id: int) -> int:
        async with self.conn.execute("SELECT COUNT(*) AS c FROM case_log WHERE user_id=?", (user_id,)) as cur:
            return (await cur.fetchone())["c"]

    # ---------- История получения кейсов ----------
    async def add_case_drop(self, ts: int, user_id: int, rarity: str, source: str, count: int = 1):
        await self.conn.execute(
            "INSERT INTO case_drops (ts, user_id, rarity, source, count) VALUES (?, ?, ?, ?, ?)",
            (ts, user_id, rarity, source, count),
        )
        await self.conn.commit()

    async def get_case_drops(self, user_id: int, limit: int = 10, offset: int = 0):
        async with self.conn.execute(
            "SELECT * FROM case_drops WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset)
        ) as cur:
            return await cur.fetchall()

    async def count_case_drops(self, user_id: int) -> int:
        async with self.conn.execute("SELECT COUNT(*) AS c FROM case_drops WHERE user_id=?", (user_id,)) as cur:
            return (await cur.fetchone())["c"]

    async def case_drop_totals(self, user_id: int) -> dict:
        """{rarity: сколько кейсов получено всего} (по истории)."""
        async with self.conn.execute(
            "SELECT rarity, COALESCE(SUM(count), 0) AS s FROM case_drops WHERE user_id=? GROUP BY rarity", (user_id,)
        ) as cur:
            return {r["rarity"]: r["s"] for r in await cur.fetchall()}

    async def best_drop(self, user_id: int):
        """Самый крутой приз: сначала по редкости кейса, потом по наименьшему шансу."""
        async with self.conn.execute(
            "SELECT * FROM case_log WHERE user_id=? ORDER BY "
            "CASE rarity WHEN 'legendary' THEN 3 WHEN 'rare' THEN 2 ELSE 1 END DESC, chance ASC, id DESC LIMIT 1",
            (user_id,),
        ) as cur:
            return await cur.fetchone()

    async def total_opened(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(opened), 0) AS s FROM user_cases WHERE user_id=?", (user_id,)
        ) as cur:
            return (await cur.fetchone())["s"]

    # ---------- Продажи ролей игроков ----------
    async def add_role_sale(self, ts: int, role_id: int, buyer_id: int, earned: int, kind: str):
        await self.conn.execute(
            "INSERT INTO role_sales (ts, role_id, buyer_id, earned, kind) VALUES (?, ?, ?, ?, ?)",
            (ts, role_id, buyer_id, earned, kind),
        )
        await self.conn.commit()

    async def role_sale_stats(self, role_id: int, creator_id: int) -> dict:
        """buyers — сколько разных людей купило, sales — продаж (с продлениями), earned — заработано."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS sales, COALESCE(SUM(earned), 0) AS earned FROM role_sales WHERE role_id=?",
            (role_id,),
        ) as cur:
            row = await cur.fetchone()
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM ("
            "  SELECT buyer_id AS uid FROM role_sales WHERE role_id=? "
            "  UNION SELECT user_id FROM inventory WHERE role_id=?"
            ") WHERE uid != ?",
            (role_id, role_id, creator_id),
        ) as cur:
            buyers = (await cur.fetchone())["c"]
        return {"sales": row["sales"], "earned": row["earned"], "buyers": buyers}

    async def get_custom_roles_of(self, creator_id: int):
        async with self.conn.execute(
            "SELECT * FROM custom_roles WHERE creator_id=? ORDER BY expires_at", (creator_id,)
        ) as cur:
            return await cur.fetchall()

    # ---------- Кастомные голосовые комнаты ----------
    async def add_croom(self, owner_id: int, channel_id: int, role_id: int, name: str,
                        created_at: int, expires_at: int):
        await self.conn.execute(
            "INSERT INTO custom_rooms (owner_id, channel_id, role_id, name, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, channel_id, role_id, name, created_at, expires_at),
        )
        await self.conn.commit()

    async def get_croom(self, owner_id: int):
        async with self.conn.execute("SELECT * FROM custom_rooms WHERE owner_id=?", (owner_id,)) as cur:
            return await cur.fetchone()

    async def all_crooms(self):
        async with self.conn.execute("SELECT * FROM custom_rooms ORDER BY created_at") as cur:
            return await cur.fetchall()

    async def count_crooms(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) AS c FROM custom_rooms") as cur:
            return (await cur.fetchone())["c"]

    async def update_croom(self, owner_id: int, **fields):
        allowed = {"channel_id", "role_id", "name", "expires_at"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.conn.execute(
            f"UPDATE custom_rooms SET {sets} WHERE owner_id=?", (*fields.values(), owner_id)
        )
        await self.conn.commit()

    async def delete_croom(self, owner_id: int):
        await self.conn.execute("DELETE FROM custom_rooms WHERE owner_id=?", (owner_id,))
        await self.conn.commit()

    # ---------- Ежедневные задания ----------
    async def quest_add(self, user_ids: list[int], day: str, messages: int = 0, voice_seconds: int = 0):
        """Добавляет прогресс заданий. Если наступил новый день — прогресс начинается заново."""
        await self.conn.executemany(
            "INSERT INTO quest_progress (user_id, day, messages, voice_seconds, done) VALUES (?, ?, ?, ?, ',') "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  messages = CASE WHEN day = excluded.day THEN messages + excluded.messages ELSE excluded.messages END, "
            "  voice_seconds = CASE WHEN day = excluded.day "
            "                  THEN voice_seconds + excluded.voice_seconds ELSE excluded.voice_seconds END, "
            "  done = CASE WHEN day = excluded.day THEN done ELSE ',' END, "
            "  day = excluded.day",
            [(uid, day, messages, voice_seconds) for uid in user_ids],
        )
        await self.conn.commit()

    async def quest_get(self, user_id: int, day: str) -> dict:
        """Прогресс за сегодня (если записи за сегодня нет — нули)."""
        async with self.conn.execute("SELECT * FROM quest_progress WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row is None or row["day"] != day:
            return {"messages": 0, "voice_seconds": 0, "done": ","}
        return {"messages": row["messages"], "voice_seconds": row["voice_seconds"], "done": row["done"]}

    async def quest_mark_done(self, user_id: int, day: str, key: str) -> bool:
        """Отмечает задание выполненным. True — только в первый раз (защита от двойной награды)."""
        cur = await self.conn.execute(
            "UPDATE quest_progress SET done = done || ? WHERE user_id=? AND day=? AND instr(done, ?) = 0",
            (f"{key},", user_id, day, f",{key},"),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- История переводов ----------
    async def add_transaction(self, ts: int, from_id: int, to_id: int, amount: int, fee: int,
                              kind: str = "pay", note: str = ""):
        await self.conn.execute(
            "INSERT INTO transactions (ts, from_id, to_id, amount, fee, kind, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, from_id, to_id, amount, fee, kind, note),
        )
        await self.conn.commit()

    async def get_transactions(self, user_id: int, limit: int = 15):
        async with self.conn.execute(
            "SELECT * FROM transactions WHERE from_id=? OR to_id=? ORDER BY id DESC LIMIT ?",
            (user_id, user_id, limit),
        ) as cur:
            return await cur.fetchall()

    # ---------- Журнал рулетки ----------
    async def add_roulette(self, ts: int, user_id: int, number: int, color: str):
        await self.conn.execute(
            "INSERT INTO roulette_log (ts, user_id, number, color) VALUES (?, ?, ?, ?)",
            (ts, user_id, number, color),
        )
        await self.conn.commit()

    async def get_roulette(self, limit: int = 20):
        async with self.conn.execute("SELECT * FROM roulette_log ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return await cur.fetchall()

    async def roulette_color_counts(self):
        async with self.conn.execute("SELECT color, COUNT(*) AS c FROM roulette_log GROUP BY color") as cur:
            return {r["color"]: r["c"] for r in await cur.fetchall()}
