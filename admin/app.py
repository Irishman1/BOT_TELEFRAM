import os
import sys
import asyncio
import aiosqlite
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SECRET", "change-this-secret-key")

ADMIN_LOGIN    = os.getenv("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password123")
DB_PATH        = os.getenv("DB_PATH", "subscriptions.db")
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")

PLANS = {
    "basic":    "Базовый пакет",
    "standard": "Стандартный пакет",
    "manual":   "Вручную",
}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def db_fetch(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def db_one(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def db_exec(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()


async def send_telegram(tg_id, text):
    import aiohttp
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        await s.post(url, json={"chat_id": tg_id, "text": text, "parse_mode": "HTML"})


# ─── Auth ────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("login") == ADMIN_LOGIN and
                request.form.get("password") == ADMIN_PASSWORD):
            session["logged_in"] = True
            return redirect(url_for("stats"))
        error = "Неверный логин или пароль"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Stats ───────────────────────────────────────────────────

@app.route("/")
@app.route("/stats")
@login_required
def stats():
    async def _():
        active = (await db_one("SELECT COUNT(*) as c FROM users WHERE is_active=1"))["c"]
        total  = (await db_one("SELECT COUNT(*) as c FROM users"))["c"]
        monthly = (await db_one(
            "SELECT COALESCE(SUM(amount),0) as s FROM payments WHERE status='success' AND strftime('%Y-%m',paid_at)=strftime('%Y-%m','now')"
        ))["s"]
        expiring = (await db_one(
            "SELECT COUNT(*) as c FROM users WHERE is_active=1 AND DATE(expires_at)<=DATE('now','+3 days')"
        ))["c"]

        monthly_subs = await db_fetch("""
            SELECT strftime('%m', subscribed_at) as month, COUNT(*) as cnt
            FROM users WHERE subscribed_at IS NOT NULL
            GROUP BY month ORDER BY month
        """)
        monthly_rev = await db_fetch("""
            SELECT strftime('%m', paid_at) as month, SUM(amount) as total
            FROM payments WHERE status='success'
            GROUP BY month ORDER BY month
        """)
        plans_dist = await db_fetch("""
            SELECT plan, COUNT(*) as cnt FROM users WHERE is_active=1
            GROUP BY plan
        """)
        return active, total, monthly, expiring, monthly_subs, monthly_rev, plans_dist

    active, total, monthly, expiring, monthly_subs, monthly_rev, plans_dist = run_async(_())
    return render_template("stats.html",
        active=active, total=total,
        monthly=round(monthly), expiring=expiring,
        monthly_subs=monthly_subs, monthly_rev=monthly_rev,
        plans_dist=plans_dist, plans=PLANS,
        page="stats"
    )


# ─── Users ───────────────────────────────────────────────────

@app.route("/users")
@login_required
def users():
    search = request.args.get("q", "")
    async def _():
        if search:
            return await db_fetch(
                "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ? ORDER BY expires_at DESC",
                (f"%{search}%", f"%{search}%")
            )
        return await db_fetch("SELECT * FROM users ORDER BY is_active DESC, expires_at ASC")
    rows = run_async(_())
    return render_template("users.html", users=rows, search=search, plans=PLANS, page="users")


# ─── Find ────────────────────────────────────────────────────

@app.route("/find", methods=["GET", "POST"])
@login_required
def find():
    result = None
    if request.method == "POST":
        q = request.form.get("q", "").lstrip("@")
        result = run_async(db_one(
            "SELECT * FROM users WHERE username=? OR tg_id=?", (q, q)
        ))
    return render_template("find.html", result=result, plans=PLANS, page="find")


# ─── Give access ─────────────────────────────────────────────

@app.route("/give", methods=["GET", "POST"])
@login_required
def give():
    msg = None
    if request.method == "POST":
        username = request.form.get("username", "").lstrip("@")
        days     = int(request.form.get("days", 30))

        async def _():
            user = await db_one("SELECT * FROM users WHERE username=?", (username,))
            if not user:
                return None, "Користувача не знайдено"
            from datetime import timedelta
            now = datetime.now()
            exp = user.get("expires_at")
            if exp and user["is_active"]:
                cur = datetime.fromisoformat(exp)
                new_exp = (cur if cur > now else now) + timedelta(days=days)
            else:
                new_exp = now + timedelta(days=days)
            await db_exec(
                "UPDATE users SET expires_at=?, plan='manual', is_active=1, subscribed_at=COALESCE(subscribed_at,?) WHERE username=?",
                (new_exp.isoformat(), now.isoformat(), username)
            )
            await send_telegram(user["tg_id"],
                f"🎁 Администратор предоставил тебе доступ на <b>{days} дней</b>!\n"
                f"📅 Подписка до: <b>{new_exp.strftime('%d.%m.%Y')}</b>\n\n"
                f"Отримай посилання: /getlink"
            )
            return user, f"✅ @{username} получил доступ на {days} дней до {new_exp.strftime('%d.%m.%Y')}"

        user, msg = run_async(_())

    return render_template("give.html", msg=msg, page="give")


# ─── Kick ────────────────────────────────────────────────────

@app.route("/kick", methods=["GET", "POST"])
@login_required
def kick():
    msg = None
    if request.method == "POST":
        username = request.form.get("username", "").lstrip("@")
        group_id = os.getenv("GROUP_ID", "")

        async def _():
            user = await db_one("SELECT * FROM users WHERE username=?", (username,))
            if not user:
                return "Користувача не знайдено"
            await db_exec("UPDATE users SET is_active=0 WHERE username=?", (username,))
            import aiohttp
            async with aiohttp.ClientSession() as s:
                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember",
                             json={"chat_id": group_id, "user_id": user["tg_id"]})
                await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/unbanChatMember",
                             json={"chat_id": group_id, "user_id": user["tg_id"]})
            await send_telegram(user["tg_id"], "❌ Твой доступ отменён администратором.")
            return f"✅ @{username} видалено з групи"

        msg = run_async(_())

    return render_template("kick.html", msg=msg, page="kick")


# ─── Broadcast ───────────────────────────────────────────────

@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    result = None
    if request.method == "POST":
        text = request.form.get("text", "")
        if text:
            async def _():
                users = await db_fetch("SELECT tg_id FROM users WHERE is_active=1")
                sent = failed = 0
                for u in users:
                    try:
                        await send_telegram(u["tg_id"], text)
                        sent += 1
                    except Exception:
                        failed += 1
                return sent, failed
            sent, failed = run_async(_())
            result = {"sent": sent, "failed": failed}

    return render_template("broadcast.html", result=result, page="broadcast")


if __name__ == "__main__":
    port = int(os.getenv("ADMIN_PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=False)
