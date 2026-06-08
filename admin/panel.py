import os
import aiosqlite
from datetime import datetime, timedelta
from aiohttp import web

ADMIN_LOGIN    = os.getenv("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DB_PATH        = "subscriptions.db"
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
GROUP_ID       = os.getenv("GROUP_ID", "")

PLANS = {"1m":"1 місяць","3m":"3 місяці","1y":"1 рік","manual":"Вручну"}
TMPL_DIR = os.path.join(os.path.dirname(__file__), "templates")


def render(name, page="", **ctx):
    with open(os.path.join(TMPL_DIR, "base.html"), encoding="utf-8") as f:
        base = f.read()
    with open(os.path.join(TMPL_DIR, name), encoding="utf-8") as f:
        content = f.read()
    for k, v in ctx.items():
        content = content.replace(f"{{{{{k}}}}}", str(v) if v is not None else "")
    nav = {f"nav_{p}": ("active" if p == page else "") for p in ["stats","users","find","give","kick","broadcast"]}
    html = base.replace("{{content}}", content)
    for k, v in nav.items():
        html = html.replace(f"{{{{{k}}}}}", v)
    return web.Response(text=html, content_type="text/html")


async def db_fetch(q, p=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, p) as c:
            return [dict(r) for r in await c.fetchall()]

async def db_one(q, p=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, p) as c:
            row = await c.fetchone()
            return dict(row) if row else None

async def db_exec(q, p=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(q, p)
        await db.commit()

async def send_tg(tg_id, text):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                     json={"chat_id": tg_id, "text": text, "parse_mode": "HTML"})

def is_logged(request):
    return request.cookies.get("admin_auth") == f"{ADMIN_LOGIN}:{ADMIN_PASSWORD}"

def require_login(h):
    async def w(request):
        if not is_logged(request):
            raise web.HTTPFound("/admin/login")
        return await h(request)
    return w


async def login_get(request):
    with open(os.path.join(TMPL_DIR, "login.html"), encoding="utf-8") as f:
        html = f.read().replace("{{error}}", "")
    return web.Response(text=html, content_type="text/html")

async def login_post(request):
    data = await request.post()
    if data.get("login") == ADMIN_LOGIN and data.get("password") == ADMIN_PASSWORD:
        resp = web.HTTPFound("/admin/stats")
        resp.set_cookie("admin_auth", f"{ADMIN_LOGIN}:{ADMIN_PASSWORD}", max_age=86400*7, httponly=True)
        raise resp
    with open(os.path.join(TMPL_DIR, "login.html"), encoding="utf-8") as f:
        html = f.read().replace("{{error}}", '<div class="error">Невірний логін або пароль</div>')
    return web.Response(text=html, content_type="text/html")

async def logout(request):
    resp = web.HTTPFound("/admin/login")
    resp.del_cookie("admin_auth")
    raise resp


@require_login
async def stats(request):
    active   = (await db_one("SELECT COUNT(*) as c FROM users WHERE is_active=1"))["c"]
    total    = (await db_one("SELECT COUNT(*) as c FROM users"))["c"]
    monthly  = (await db_one("SELECT COALESCE(SUM(amount),0) as s FROM payments WHERE status='success' AND strftime('%Y-%m',paid_at)=strftime('%Y-%m','now')"))["s"]
    expiring = (await db_one("SELECT COUNT(*) as c FROM users WHERE is_active=1 AND DATE(expires_at)<=DATE('now','+3 days')"))["c"]
    subs  = await db_fetch("SELECT strftime('%m',subscribed_at) as m,COUNT(*) as c FROM users WHERE subscribed_at IS NOT NULL GROUP BY m ORDER BY m")
    revs  = await db_fetch("SELECT strftime('%m',paid_at) as m,ROUND(SUM(amount)) as s FROM payments WHERE status='success' GROUP BY m ORDER BY m")
    plans = await db_fetch("SELECT plan,COUNT(*) as c FROM users WHERE is_active=1 GROUP BY plan")
    months = ['Січ','Лют','Бер','Кві','Тра','Чер','Лип','Сер','Вер','Жов','Лис','Гру']
    colors = ['#534AB7','#1D9E75','#D85A30','#888']
    plans_html = "".join(f'<div style="display:flex;align-items:center;gap:8px;font-size:14px;"><span style="width:12px;height:12px;border-radius:3px;background:{colors[i%4]};flex-shrink:0;"></span><span>{PLANS.get(r["plan"],r["plan"])} — <strong>{r["c"]}</strong> чол.</span></div>' for i,r in enumerate(plans))
    return render("stats.html", page="stats",
        active=active, total=total, monthly=int(monthly), expiring=expiring,
        sub_labels=[months[int(r['m'])-1] for r in subs],
        sub_vals=[r['c'] for r in subs],
        rev_labels=[months[int(r['m'])-1] for r in revs],
        rev_vals=[r['s'] for r in revs],
        plan_labels=[PLANS.get(r['plan'],r['plan']) for r in plans],
        plan_vals=[r['c'] for r in plans],
        plans_html=plans_html)


@require_login
@require_login
async def users_page(request):
    q = request.rel_url.query.get("q","")
    if q:
        rows = await db_fetch(
            "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ? ORDER BY expires_at DESC",
            (f"%{q}%", f"%{q}%")
        )
    else:
        rows = await db_fetch("SELECT * FROM users ORDER BY is_active DESC,expires_at ASC")
    rows_html = ""
    for u in rows:
        badge = '<span class="badge badge-active">Активний</span>' if u["is_active"] else '<span class="badge badge-expired">Неактивний</span>'
        exp = (u["expires_at"] or "")[:10] or "—"
        un = u["username"] or str(u["tg_id"])
        plan = PLANS.get(u["plan"], u["plan"] or "—")
        name = u["full_name"] or "—"
        rows_html += (
            "<tr>"
            f"<td><div style='font-weight:500'>{name}</div><div style='font-size:12px;color:#6b7280'>@{un}</div></td>"
            f"<td>{plan}</td>"
            f"<td>{exp}</td>"
            f"<td>{badge}</td>"
            f"<td style='display:flex;gap:6px'>"
            f"<a href='/admin/give?u={un}' class='btn-sm'>Продовжити</a> "
            f"<a href='/admin/kick?u={un}' class='btn-sm danger'>Кік</a>"
            "</td></tr>"
        )
    return render("users.html", page="users", rows=rows_html, count=len(rows), search=q)


@require_login
async def find_get(request):
    return render("find.html", page="find", result="")

@require_login
async def find_post(request):
    data = await request.post()
    q = data.get("q","").lstrip("@")
    u = await db_one("SELECT * FROM users WHERE username=? OR tg_id=?",(q,q))
    if not u:
        return render("find.html", page="find", result='<div class="alert alert-danger">Користувача не знайдено</div>')
    s = '<span class="badge badge-active">Активний</span>' if u["is_active"] else '<span class="badge badge-expired">Неактивний</span>'
    exp = (u["expires_at"] or "")[:10] or "—"
    result = f'<div class="user-card" style="margin-top:20px;border-top:1px solid #e5e7ef;padding-top:20px;"><div class="user-card-name">{u["full_name"] or u["username"]} {s}</div><div class="user-meta"><div class="user-meta-item"><label>Username</label><p>@{u["username"] or "—"}</p></div><div class="user-meta-item"><label>Telegram ID</label><p>{u["tg_id"]}</p></div><div class="user-meta-item"><label>Тариф</label><p>{PLANS.get(u["plan"],u["plan"] or "—")}</p></div><div class="user-meta-item"><label>До</label><p>{exp}</p></div></div><div style="display:flex;gap:8px;margin-top:16px;"><a href="/admin/give?u={u["username"]}" class="btn btn-primary" style="font-size:13px;padding:7px 14px;">Продовжити</a><a href="/admin/kick?u={u["username"]}" class="btn btn-danger" style="font-size:13px;padding:7px 14px;">Видалити</a></div></div>'
    return render("find.html", page="find", result=result)


@require_login
async def give_get(request):
    return render("give.html", page="give", msg="", prefill=request.rel_url.query.get("u",""))

@require_login
async def give_post(request):
    data = await request.post()
    username = data.get("username","").lstrip("@")
    days = int(data.get("days",30))
    u = await db_one("SELECT * FROM users WHERE username=?",(username,))
    if not u:
        return render("give.html", page="give", msg='<div class="alert alert-danger">Користувача не знайдено</div>', prefill=username)
    now = datetime.now()
    exp = u.get("expires_at")
    cur = datetime.fromisoformat(exp) if exp and u["is_active"] else now
    new_exp = (cur if cur > now else now) + timedelta(days=days)
    await db_exec("UPDATE users SET expires_at=?,plan='manual',is_active=1,subscribed_at=COALESCE(subscribed_at,?) WHERE username=?",(new_exp.isoformat(),now.isoformat(),username))
    try: await send_tg(u["tg_id"],f"🎁 Адміністратор надав доступ на <b>{days} днів</b>!\n📅 До: <b>{new_exp.strftime('%d.%m.%Y')}</b>\n\n/getlink")
    except: pass
    return render("give.html", page="give", msg=f'<div class="alert alert-success">✅ @{username} отримав доступ на {days} днів до {new_exp.strftime("%d.%m.%Y")}</div>', prefill="")


@require_login
async def kick_get(request):
    return render("kick.html", page="kick", msg="", prefill=request.rel_url.query.get("u",""))

@require_login
async def kick_post(request):
    data = await request.post()
    username = data.get("username","").lstrip("@")
    u = await db_one("SELECT * FROM users WHERE username=?",(username,))
    if not u:
        return render("kick.html", page="kick", msg='<div class="alert alert-danger">Користувача не знайдено</div>', prefill=username)
    await db_exec("UPDATE users SET is_active=0 WHERE username=?",(username,))
    import aiohttp as _h
    async with _h.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember",  json={"chat_id":GROUP_ID,"user_id":u["tg_id"]})
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/unbanChatMember",json={"chat_id":GROUP_ID,"user_id":u["tg_id"]})
    try: await send_tg(u["tg_id"],"❌ Твій доступ скасовано адміністратором.")
    except: pass
    return render("kick.html", page="kick", msg=f'<div class="alert alert-success">✅ @{username} видалено з групи</div>', prefill="")


@require_login
async def broadcast_get(request):
    return render("broadcast.html", page="broadcast", result="")

@require_login
async def broadcast_post(request):
    data = await request.post()
    text = data.get("text","")
    if not text:
        return render("broadcast.html", page="broadcast", result="")
    users = await db_fetch("SELECT tg_id FROM users WHERE is_active=1")
    sent = failed = 0
    for u in users:
        try: await send_tg(u["tg_id"],text); sent+=1
        except: failed+=1
    return render("broadcast.html", page="broadcast", result=f'<div class="alert alert-success">✅ Надіслано: <strong>{sent}</strong>, помилок: <strong>{failed}</strong></div>')


def setup_admin(app: web.Application):
    app.router.add_get ("/admin",            lambda r: web.HTTPFound("/admin/stats"))
    app.router.add_get ("/admin/",           lambda r: web.HTTPFound("/admin/stats"))
    app.router.add_get ("/admin/login",      login_get)
    app.router.add_post("/admin/login",      login_post)
    app.router.add_get ("/admin/logout",     logout)
    app.router.add_get ("/admin/stats",      stats)
    app.router.add_get ("/admin/users",      users_page)
    app.router.add_get ("/admin/find",       find_get)
    app.router.add_post("/admin/find",       find_post)
    app.router.add_get ("/admin/give",       give_get)
    app.router.add_post("/admin/give",       give_post)
    app.router.add_get ("/admin/kick",       kick_get)
    app.router.add_post("/admin/kick",       kick_post)
    app.router.add_get ("/admin/broadcast",  broadcast_get)
    app.router.add_post("/admin/broadcast",  broadcast_post)
