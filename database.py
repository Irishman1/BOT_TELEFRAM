import os
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "subscriptions.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id         INTEGER PRIMARY KEY,
                username      TEXT,
                full_name     TEXT,
                subscribed_at DATETIME,
                expires_at    DATETIME,
                plan          TEXT,
                is_active     INTEGER DEFAULT 0,
                notified_3d   INTEGER DEFAULT 0,
                notified_1d   INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        TEXT UNIQUE,
                tg_id           INTEGER,
                amount          REAL,
                plan            TEXT,
                paid_at         DATETIME,
                status          TEXT,
                paypal_order_id TEXT,
                promo_code      TEXT
            )
        """)
        # Міграція для старих БД без нових колонок
        cur = await db.execute("PRAGMA table_info(payments)")
        cols = [r[1] for r in await cur.fetchall()]
        if "paypal_order_id" not in cols:
            await db.execute("ALTER TABLE payments ADD COLUMN paypal_order_id TEXT")
        if "promo_code" not in cols:
            await db.execute("ALTER TABLE payments ADD COLUMN promo_code TEXT")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS invite_links (
                tg_id      INTEGER PRIMARY KEY,
                link       TEXT,
                created_at DATETIME,
                expires_at DATETIME
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                order_id        TEXT PRIMARY KEY,
                tg_id           INTEGER,
                plan            TEXT,
                promo_code      TEXT,
                paypal_order_id TEXT,
                created_at      DATETIME
            )
        """)
        cur = await db.execute("PRAGMA table_info(pending_orders)")
        cols = [r[1] for r in await cur.fetchall()]
        if "promo_code" not in cols:
            await db.execute("ALTER TABLE pending_orders ADD COLUMN promo_code TEXT")
        if "paypal_order_id" not in cols:
            await db.execute("ALTER TABLE pending_orders ADD COLUMN paypal_order_id TEXT")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code        TEXT PRIMARY KEY,
                discount    INTEGER NOT NULL,
                max_uses    INTEGER DEFAULT 0,
                used_count  INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                created_at  DATETIME
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS buy_clicks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      INTEGER,
                plan       TEXT,
                created_at DATETIME
            )
        """)
        await db.commit()


# ─── Users ──────────────────────────────────────────────────

async def get_user(tg_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_user(tg_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (tg_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
        """, (tg_id, username, full_name))
        await db.commit()


async def activate_subscription(tg_id: int, plan: str, days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT expires_at, is_active FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()

        now = datetime.now()
        if row and row["is_active"] and row["expires_at"]:
            current = datetime.fromisoformat(row["expires_at"])
            new_expiry = (current if current > now else now) + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)

        await db.execute("""
            UPDATE users SET
                subscribed_at = COALESCE(subscribed_at, ?),
                expires_at    = ?,
                plan          = ?,
                is_active     = 1,
                notified_3d   = 0,
                notified_1d   = 0
            WHERE tg_id = ?
        """, (now.isoformat(), new_expiry.isoformat(), plan, tg_id))
        await db.commit()
        return new_expiry


async def deactivate_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_active = 0 WHERE tg_id = ?", (tg_id,))
        await db.commit()


async def get_expiring_users(days: int) -> list:
    col = f"notified_{days}d"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"""
            SELECT * FROM users
            WHERE is_active = 1 AND {col} = 0
              AND DATE(expires_at) = DATE('now', '+{days} days')
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_notified(tg_id: int, days: int):
    col = f"notified_{days}d"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {col} = 1 WHERE tg_id = ?", (tg_id,))
        await db.commit()


async def get_expired_users() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM users
            WHERE is_active = 1 AND expires_at < datetime('now')
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_active_users() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE is_active = 1") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1") as cur:
            active = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute("""
            SELECT COALESCE(SUM(amount),0) FROM payments
            WHERE status='success' AND strftime('%Y-%m',paid_at)=strftime('%Y-%m','now')
        """) as cur:
            monthly = (await cur.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE is_active=1 AND DATE(expires_at)<=DATE('now','+3 days')
        """) as cur:
            expiring = (await cur.fetchone())[0]
    return {"active": active, "total": total,
            "monthly_revenue": monthly, "expiring_soon": expiring}


async def find_user_by_username(username: str) -> Optional[dict]:
    username = username.lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─── Payments ────────────────────────────────────────────────

async def save_payment(order_id: str, tg_id: int, amount: float, plan: str, status: str,
                        paypal_order_id: str = None, promo_code: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO payments (order_id, tg_id, amount, plan, paid_at, status, paypal_order_id, promo_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (order_id, tg_id, amount, plan, datetime.now().isoformat(), status, paypal_order_id, promo_code))
        await db.commit()


async def payment_exists(order_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM payments WHERE order_id=? AND status='success'", (order_id,)
        ) as cur:
            return await cur.fetchone() is not None


# ─── Invite links ────────────────────────────────────────────

async def save_invite_link(tg_id: int, link: str, expires_at: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO invite_links (tg_id, link, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (tg_id, link, datetime.now().isoformat(), expires_at.isoformat()))
        await db.commit()


async def get_invite_link(tg_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT link, expires_at FROM invite_links WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and datetime.fromisoformat(row[1]) > datetime.now():
                return row[0]
            return None


# ─── Pending orders ──────────────────────────────────────────

async def save_pending_order(order_id: str, tg_id: int, plan: str, promo_code: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO pending_orders (order_id, tg_id, plan, promo_code, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (order_id, tg_id, plan, promo_code, datetime.now().isoformat()))
        await db.commit()


async def set_pending_order_paypal_id(order_id: str, paypal_order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_orders SET paypal_order_id = ? WHERE order_id = ?",
            (paypal_order_id, order_id)
        )
        await db.commit()


async def get_pending_order_by_paypal_id(paypal_order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_orders WHERE paypal_order_id = ?", (paypal_order_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_pending_order(order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_orders WHERE order_id = ?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_pending_order(order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_orders WHERE order_id = ?", (order_id,))
        await db.commit()


# ─── Promo codes ─────────────────────────────────────────────

async def get_promo(code: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code.upper(),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            promo = dict(row)
            if promo["max_uses"] and promo["used_count"] >= promo["max_uses"]:
                return None
            return promo


async def use_promo(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", (code.upper(),)
        )
        await db.commit()


async def create_promo(code: str, discount: int, max_uses: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO promo_codes (code, discount, max_uses, used_count, is_active, created_at)
            VALUES (?, ?, ?, COALESCE((SELECT used_count FROM promo_codes WHERE code=?),0), 1, ?)
        """, (code.upper(), discount, max_uses, code.upper(), datetime.now().isoformat()))
        await db.commit()


async def toggle_promo(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE promo_codes SET is_active = 1 - is_active WHERE code = ?", (code.upper(),)
        )
        await db.commit()


async def delete_promo(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
        await db.commit()


async def get_all_promos() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Buy clicks (conversion tracking) ────────────────────────

async def log_buy_click(tg_id: int, plan: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO buy_clicks (tg_id, plan, created_at) VALUES (?, ?, ?)",
            (tg_id, plan, datetime.now().isoformat())
        )
        await db.commit()


async def get_buy_clicks_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM buy_clicks") as cur:
            return (await cur.fetchone())[0]
