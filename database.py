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
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                tg_id    INTEGER,
                amount   REAL,
                plan     TEXT,
                paid_at  DATETIME,
                status   TEXT
            )
        """)
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
                order_id   TEXT PRIMARY KEY,
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

async def save_payment(order_id: str, tg_id: int, amount: float, plan: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO payments (order_id, tg_id, amount, plan, paid_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, tg_id, amount, plan, datetime.now().isoformat(), status))
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

async def save_pending_order(order_id: str, tg_id: int, plan: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO pending_orders (order_id, tg_id, plan, created_at)
            VALUES (?, ?, ?, ?)
        """, (order_id, tg_id, plan, datetime.now().isoformat()))
        await db.commit()


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
