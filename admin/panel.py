import os
import logging
import aiosqlite
from datetime import datetime, timedelta
from urllib.parse import quote
from aiohttp import web

logger = logging.getLogger(__name__)
from database import (
    get_all_promos, create_promo, toggle_promo, delete_promo, get_buy_clicks_count,
    get_clicks_and_payments_by_month, get_unread_support_count, get_support_messages,
    mark_support_read, mark_all_support_read,
    log_action, get_admin_log,
)

ADMIN_LOGIN    = os.getenv("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DB_PATH        = os.getenv("DB_PATH", "subscriptions.db")
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
GROUP_ID       = os.getenv("GROUP_ID", "")

PLANS = {"basic":"Базовый пакет","standard":"Стандартный пакет","manual":"Вручную"}

import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
from config import encode_id, decode_id, PAYMENT_PROVIDER
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")
TMPL_DIR = os.path.join(os.path.dirname(__file__), "templates")


async def render(template, page="", **ctx):
    with open(os.path.join(TMPL_DIR, "base.html"), encoding="utf-8") as f:
        base = f.read()
    with open(os.path.join(TMPL_DIR, template), encoding="utf-8") as f:
        content = f.read()
    for k, v in ctx.items():
        content = content.replace(f"{{{{{k}}}}}", str(v) if v is not None else "")
    nav = {f"nav_{p}": ("active" if p == page else "") for p in ["stats","users","find","give","kick","broadcast","payments","promo","support","logs"]}
    html = base.replace("{{content}}", content)
    for k, v in nav.items():
        html = html.replace(f"{{{{{k}}}}}", v)

    unread = await get_unread_support_count()
    badge = f'<span class="support-badge">{unread}</span>' if unread else ""
    html = html.replace("{{support_badge}}", badge)

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


CODE_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# Невидимі символи, які тягнуться разом зі вставкою з Telegram
INVISIBLE = "​‌‍‎‏﻿⁦⁧⁨⁩"


def normalize_query(raw: str) -> str:
    """Приводить будь-яку вставку до чистого юзернейма: '@Name ', 't.me/Name', 'https://t.me/Name'."""
    q = (raw or "")
    for ch in INVISIBLE:
        q = q.replace(ch, "")
    q = q.strip()
    low = q.lower()
    for prefix in ("https://t.me/", "http://t.me/", "https://telegram.me/",
                   "http://telegram.me/", "t.me/", "telegram.me/"):
        if low.startswith(prefix):
            q = q[len(prefix):]
            break
    q = q.lstrip("@").strip()
    return q.rstrip("/")


def looks_like_code(q: str) -> bool:
    """Код учасника — короткий рядок з алфавіту encode_id. Юзернейми з '_' або довші сюди не потрапляють."""
    return bool(q) and len(q) <= 9 and all(c in CODE_CHARS for c in q.upper())


async def find_user(q: str):
    """Шукає юзера за юзернеймом (без урахування регістру), tg_id або кодом учасника."""
    q = normalize_query(q)
    if not q:
        return None
    u = await db_one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (q,))
    if u:
        return u
    if q.isdigit():
        u = await db_one("SELECT * FROM users WHERE tg_id=?", (int(q),))
        if u:
            return u
    if looks_like_code(q):
        decoded = decode_id(q)
        if decoded:
            return await db_one("SELECT * FROM users WHERE tg_id=?", (decoded,))
    return None

async def send_tg(tg_id, text):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                     json={"chat_id": tg_id, "text": text, "parse_mode": "HTML"})


async def create_invite_link():
    import aiohttp, time
    expire_date = int(time.time()) + 15 * 60
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/createChatInviteLink",
            json={"chat_id": GROUP_ID, "member_limit": 1, "expire_date": expire_date}
        )
        data = await resp.json()
        if data.get("ok"):
            return data["result"]["invite_link"]
    return None

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

def client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else (request.remote or "")) or "—"


async def login_post(request):
    data = await request.post()
    if data.get("login") == ADMIN_LOGIN and data.get("password") == ADMIN_PASSWORD:
        await log_action("login", query=str(data.get("login","")), details=f"вход в панель, IP {client_ip(request)}")
        resp = web.HTTPFound("/admin/stats")
        resp.set_cookie("admin_auth", f"{ADMIN_LOGIN}:{ADMIN_PASSWORD}", max_age=86400*7, httponly=True)
        raise resp
    # Пароль у лог не пишемо — тільки факт невдалої спроби
    await log_action("login", query=str(data.get("login","")), details=f"неверный логин или пароль, IP {client_ip(request)}", ok=False)
    with open(os.path.join(TMPL_DIR, "login.html"), encoding="utf-8") as f:
        html = f.read().replace("{{error}}", '<div class="error">Неверный логин или пароль</div>')
    return web.Response(text=html, content_type="text/html")

async def logout(request):
    await log_action("logout", details="выход из панели")
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
    months = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек']
    colors = ['#534AB7','#1D9E75','#D85A30','#888']
    plans_html = "".join(f'<div style="display:flex;align-items:center;gap:8px;font-size:14px;"><span style="width:12px;height:12px;border-radius:3px;background:{colors[i%4]};flex-shrink:0;"></span><span>{PLANS.get(r["plan"],r["plan"])} — <strong>{r["c"]}</strong> чел.</span></div>' for i,r in enumerate(plans))

    clicks = await get_buy_clicks_count()
    paid_count = (await db_one("SELECT COUNT(*) as c FROM payments WHERE status='success'"))["c"]
    conversion = round(paid_count / clicks * 100, 1) if clicks else 0

    cp = await get_clicks_and_payments_by_month()
    funnel_months = sorted(set(cp["clicks"]) | set(cp["payments"]))
    funnel_labels = [months[int(m)-1] for m in funnel_months]
    funnel_clicks = [cp["clicks"].get(m, 0) for m in funnel_months]
    funnel_payments = [cp["payments"].get(m, 0) for m in funnel_months]

    inactive = max(total - active, 0)
    status_labels = ["Активные", "Неактивные"]
    status_vals = [active, inactive]
    status_colors = ['#1D9E75', '#888']
    status_html = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;font-size:14px;"><span style="width:12px;height:12px;border-radius:3px;background:{c};flex-shrink:0;"></span><span>{l} — <strong>{v}</strong> чел.</span></div>'
        for l, v, c in zip(status_labels, status_vals, status_colors)
    )

    return await render("stats.html", page="stats",
        active=active, total=total, monthly=int(monthly), expiring=expiring,
        sub_labels=[months[int(r['m'])-1] for r in subs],
        sub_vals=[r['c'] for r in subs],
        rev_labels=[months[int(r['m'])-1] for r in revs],
        rev_vals=[r['s'] for r in revs],
        plan_labels=[PLANS.get(r['plan'],r['plan']) for r in plans],
        plan_vals=[r['c'] for r in plans],
        plans_html=plans_html,
        status_labels=status_labels,
        status_vals=status_vals,
        status_html=status_html,
        clicks=clicks,
        conversion=conversion,
        funnel_labels=funnel_labels,
        funnel_clicks=funnel_clicks,
        funnel_payments=funnel_payments)


@require_login
async def users_page(request):
    q = normalize_query(request.rel_url.query.get("q",""))
    if q:
        # Пошук за кодом учасника — точний збіг по tg_id, інакше LIKE по нікнейму/імені/id
        code_id = decode_id(q) if looks_like_code(q) else None
        rows = await db_fetch(
            "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ? OR CAST(tg_id AS TEXT) LIKE ? OR tg_id=? ORDER BY expires_at DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%", code_id if code_id else -1)
        )
    else:
        rows = await db_fetch("SELECT * FROM users ORDER BY is_active DESC,expires_at ASC")
    rows_html = ""
    for u in rows:
        badge = '<span class="badge badge-active">Активный</span>' if u["is_active"] else '<span class="badge badge-expired">Неактивный</span>'
        exp = (u["expires_at"] or "")[:10] or "—"
        un = u["username"] or str(u["tg_id"])
        plan = PLANS.get(u["plan"], u["plan"] or "—")
        name = u["full_name"] or "—"
        rows_html += (
            "<tr>"
            f"<td><div style='font-weight:500'>{name}</div><div style='font-size:12px;color:#6b7280'>@{un}</div></td>"
            f"<td><span style='font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px'>{encode_id(u['tg_id'])}</span>"
            f"<div style='font-size:12px;color:#6b7280'>{u['tg_id']}</div></td>"
            f"<td>{plan}</td>"
            f"<td>{exp}</td>"
            f"<td>{badge}</td>"
            f"<td style='display:flex;gap:6px'>"
            f"<a href='/admin/give?u={un}' class='btn-sm'>Продлить</a> "
            f"<a href='/admin/kick?u={un}' class='btn-sm danger'>Кик</a>"
            "</td></tr>"
        )
    return await render("users.html", page="users", rows=rows_html, count=len(rows), search=q)


@require_login
async def find_get(request):
    return await render("find.html", page="find", result="")

@require_login
async def find_post(request):
    data = await request.post()
    q = normalize_query(data.get("q",""))
    u = await find_user(q)
    if not u:
        await log_action("find", query=q, details="не найден", ok=False)
        return await render("find.html", page="find", result='<div class="alert alert-danger">Пользователь не найден</div>')
    s = '<span class="badge badge-active">Активный</span>' if u["is_active"] else '<span class="badge badge-expired">Неактивный</span>'
    exp = (u["expires_at"] or "")[:10] or "—"
    result = f'<div class="user-card" style="margin-top:20px;border-top:1px solid #e5e7ef;padding-top:20px;"><div class="user-card-name">{u["full_name"] or u["username"]} {s}</div><div class="user-meta"><div class="user-meta-item"><label>Username</label><p>@{u["username"] or "—"}</p></div><div class="user-meta-item"><label>Telegram ID</label><p>{u["tg_id"]}</p></div><div class="user-meta-item"><label>Тариф</label><p>{PLANS.get(u["plan"],u["plan"] or "—")}</p></div><div class="user-meta-item"><label>До</label><p>{exp}</p></div></div><div style="display:flex;gap:8px;margin-top:16px;"><a href="/admin/give?u={u["username"]}" class="btn btn-primary" style="font-size:13px;padding:7px 14px;">Продлить</a><a href="/admin/kick?u={u["username"]}" class="btn btn-danger" style="font-size:13px;padding:7px 14px;">Удалить</a></div></div>'
    return await render("find.html", page="find", result=result)


@require_login
async def give_get(request):
    return await render("give.html", page="give", msg="", prefill=request.rel_url.query.get("u",""))

@require_login
async def give_post(request):
    data = await request.post()
    username = normalize_query(data.get("username",""))
    try:
        days = int(str(data.get("days","30")).strip() or 30)
    except ValueError:
        days = 0
    if days < 1:
        await log_action("give", query=username, details=f"некорректное число дней: {data.get('days')!r}", ok=False)
        return await render("give.html", page="give", msg='<div class="alert alert-danger">Укажи количество дней числом (больше 0)</div>', prefill=username)
    reason = data.get("reason","gift")
    plan_key = (data.get("plan_key","manual:0") or "manual:0").split(":")[0]
    u = await find_user(username)
    if not u:
        await log_action("give", query=username, details=f"не найден (пакет {plan_key}, {days} дн.)", ok=False)
        return await render("give.html", page="give", msg=f'<div class="alert alert-danger">Пользователь <b>{username}</b> не найден в базе. Он ещё ни разу не писал боту — попроси его отправить боту /start, после этого доступ можно выдать.</div>', prefill=username)
    now = datetime.now()
    exp = u.get("expires_at")
    cur = now
    if exp and u["is_active"]:
        try:
            cur = datetime.fromisoformat(exp)
        except (ValueError, TypeError):
            cur = now  # зіпсована дата в базі не має блокувати видачу
    new_exp = (cur if cur > now else now) + timedelta(days=days)
    display_name = f'@{u["username"]}' if u.get("username") else (u.get("full_name") or str(u["tg_id"]))
    await db_exec("UPDATE users SET expires_at=?,plan=?,is_active=1,subscribed_at=COALESCE(subscribed_at,?),notified_3d=0,notified_1d=0 WHERE tg_id=?",(new_exp.isoformat(),plan_key,now.isoformat(),u["tg_id"]))
    await log_action(
        "give", query=username, target_tg_id=u["tg_id"], target_name=display_name,
        details=f"{PLANS.get(plan_key, plan_key)}, +{days} дн. → {new_exp.strftime('%d.%m.%Y')}, "
                f"причина: {'оплата на карту' if reason == 'card' else 'подарок'}, "
                f"было: {(exp or '—')[:10]}"
    )
    try:
        link = await create_invite_link()
    except Exception as e:
        link = None  # підписка вже активована — збій Telegram не має видавати 500 і лякати адміна
        logger.warning(f"create_invite_link failed for {u['tg_id']}: {e}")
        await log_action("give", query=username, target_tg_id=u["tg_id"], target_name=display_name,
                         details=f"доступ выдан, но ссылку-приглашение создать не удалось: {e}", ok=False)
    try:
        if reason == "card":
            tg_msg = f"✅ <b>Оплата подтверждена!</b>\n📅 Доступ до: <b>{new_exp.strftime('%d.%m.%Y')}</b>"
        else:
            tg_msg = f"🎁 Вам предоставлен бесплатный доступ на <b>{days} дней</b>!\n📅 До: <b>{new_exp.strftime('%d.%m.%Y')}</b>"
        if link:
            tg_msg += f"\n\n🔗 <b>Ссылка на группу:</b>\n{link}\n\n⏱ Действует 15 минут, одноразовая."
        await send_tg(u["tg_id"], tg_msg)
    except: pass
    if link:
        msg = (
            f'<div class="alert alert-success">✅ {display_name} получил доступ на {days} дней до {new_exp.strftime("%d.%m.%Y")}</div>'
            f'<div style="margin-top:16px;padding:16px;background:#f0fdf4;border:1px solid #86efac;border-radius:10px;">'
            f'<div style="font-size:13px;color:#166534;font-weight:600;margin-bottom:8px;">🔗 Одноразовая ссылка на группу (15 мин):</div>'
            f'<div style="display:flex;gap:8px;align-items:center;">'
            f'<input id="inv_link" value="{link}" readonly style="flex:1;padding:8px 10px;border:1px solid #ccc;border-radius:7px;font-size:13px;background:#fff;">'
            f'<button onclick="navigator.clipboard.writeText(document.getElementById(\'inv_link\').value);this.textContent=\'✅\'" '
            f'style="padding:8px 14px;background:#1D9E75;color:#fff;border:none;border-radius:7px;font-size:13px;cursor:pointer;">Копировать</button>'
            f'</div>'
            f'<div style="font-size:12px;color:#6b7280;margin-top:6px;">Ссылка уже отправлена пользователю в Telegram. Можно также передать вручную.</div>'
            f'</div>'
        )
    else:
        msg = f'<div class="alert alert-success">✅ {display_name} получил доступ на {days} дней до {new_exp.strftime("%d.%m.%Y")}</div>'
    return await render("give.html", page="give", msg=msg, prefill="")


@require_login
async def kick_get(request):
    return await render("kick.html", page="kick", msg="", prefill=request.rel_url.query.get("u",""))

@require_login
async def kick_post(request):
    data = await request.post()
    username = normalize_query(data.get("username",""))
    u = await find_user(username)
    if not u:
        await log_action("kick", query=username, details="не найден", ok=False)
        return await render("kick.html", page="kick", msg='<div class="alert alert-danger">Пользователь не найден</div>', prefill=username)
    await db_exec("UPDATE users SET is_active=0 WHERE tg_id=?",(u["tg_id"],))
    import aiohttp as _h
    async with _h.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember",  json={"chat_id":GROUP_ID,"user_id":u["tg_id"]})
        await s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/unbanChatMember",json={"chat_id":GROUP_ID,"user_id":u["tg_id"]})
    try: await send_tg(u["tg_id"],"❌ Твой доступ отменён администратором.")
    except: pass
    kicked = f'@{u["username"]}' if u.get("username") else (u.get("full_name") or str(u["tg_id"]))
    await log_action("kick", query=username, target_tg_id=u["tg_id"], target_name=kicked,
                     details=f"доступ был до {(u.get('expires_at') or '—')[:10]}, забанен и разбанен в группе")
    return await render("kick.html", page="kick", msg=f'<div class="alert alert-success">✅ {kicked} удалён из группы</div>', prefill="")


@require_login
async def broadcast_get(request):
    return await render("broadcast.html", page="broadcast", result="")

@require_login
async def broadcast_post(request):
    data = await request.post()
    text = data.get("text","")
    target = data.get("target", "all")
    if not text:
        return await render("broadcast.html", page="broadcast", result="")
    if target == "expiring":
        days = data.get("days","3")
        try:
            days = int(days)
        except ValueError:
            days = 3
        users = await db_fetch(
            f"SELECT tg_id FROM users WHERE is_active=1 AND expires_at IS NOT NULL AND DATE(expires_at) <= DATE('now', '+{days} days')"
        )
    else:
        users = await db_fetch("SELECT tg_id FROM users WHERE is_active=1")
    sent = failed = 0
    for u in users:
        try: await send_tg(u["tg_id"],text); sent+=1
        except: failed+=1
    await log_action("broadcast", details=f"аудитория: {target}, отправлено {sent}, ошибок {failed}, текст: {text[:120]}")
    return await render("broadcast.html", page="broadcast", result=f'<div class="alert alert-success">✅ Отправлено: <strong>{sent}</strong>, ошибок: <strong>{failed}</strong></div>')


# ─── Support messages ────────────────────────────────────────

@require_login
async def support_page(request):
    messages = await get_support_messages()
    rows_html = ""
    for m in messages:
        un = f"@{m['username']}" if m.get("username") else (m.get("full_name") or "")
        date = (m["created_at"] or "")[:16].replace("T", " ")
        read_cls = "" if m["is_read"] else 'style="font-weight:600;background:#f0effe;"'
        status = '<span class="badge badge-active">Прочитано</span>' if m["is_read"] else '<span class="badge badge-expired">Новое</span>'
        mark_read = "" if m["is_read"] else f'<a href="/admin/support/read?id={m["id"]}" class="btn-sm">Отметить прочитанным</a>'
        reply_link = f'<a href="/admin/support/reply?tg_id={m["tg_id"]}&msg_id={m["id"]}&name={quote(un.lstrip("@"))}" class="btn-sm" style="margin-left:6px;">✉️ Ответить</a>'
        action = mark_read + reply_link
        rows_html += f"""<tr {read_cls}>
            <td>{date}</td>
            <td>{un}<br><span style="color:var(--muted);font-size:12px;">{m['tg_id']} · код {encode_id(m['tg_id'])}</span></td>
            <td>{m['text']}</td>
            <td>{status}</td>
            <td>{action}</td>
        </tr>"""
    if not rows_html:
        rows_html = '<tr><td colspan="5" style="text-align:center;color:var(--muted);">Сообщений нет</td></tr>'
    return await render("support.html", page="support", rows=rows_html)


@require_login
async def support_mark_read(request):
    msg_id = request.rel_url.query.get("id")
    if msg_id:
        await mark_support_read(int(msg_id))
    raise web.HTTPFound("/admin/support")


@require_login
async def support_mark_all_read(request):
    await mark_all_support_read()
    await log_action("support_read_all", details="все обращения отмечены прочитанными")
    raise web.HTTPFound("/admin/support")


@require_login
async def support_reply_get(request):
    tg_id = request.rel_url.query.get("tg_id", "")
    msg_id = request.rel_url.query.get("msg_id", "")
    name = request.rel_url.query.get("name", "")
    user_label = f"@{name}" if name else f"ID {tg_id}"
    return await render("reply.html", page="support", msg="", tg_id=tg_id, msg_id=msg_id, user_label=user_label)


@require_login
async def support_reply_post(request):
    data = await request.post()
    tg_id = data.get("tg_id", "")
    msg_id = data.get("msg_id", "")
    text = data.get("text", "").strip()
    if not tg_id or not text:
        return await render("reply.html", page="support", msg='<div class="alert alert-danger">Заполни сообщение</div>', tg_id=tg_id, msg_id=msg_id, user_label=f"ID {tg_id}")

    target = await db_one("SELECT username, full_name FROM users WHERE tg_id=?", (int(tg_id),))
    label = f'@{target["username"]}' if target and target.get("username") else (target or {}).get("full_name", "") or f"ID {tg_id}"
    try:
        await send_tg(int(tg_id), f"💬 <b>Ответ от поддержки:</b>\n\n{text}")
        await log_action("support_reply", target_tg_id=int(tg_id), target_name=label,
                         details=f"ответ отправлен: {text[:150]}")
    except Exception as e:
        await log_action("support_reply", target_tg_id=int(tg_id), target_name=label,
                         details=f"не удалось отправить ответ: {e}", ok=False)
    if msg_id:
        await mark_support_read(int(msg_id))
    raise web.HTTPFound("/admin/support")


# ─── DB backup ───────────────────────────────────────────────

@require_login
async def backup_db(request):
    await log_action("backup", details="скачана полная копия базы")
    return web.FileResponse(
        path=DB_PATH,
        headers={"Content-Disposition": "attachment; filename=backup.db"},
    )


@require_login
async def export_users_csv(request):
    import csv, io
    rows = await db_fetch("SELECT * FROM users ORDER BY tg_id")
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    await log_action("export_users", details=f"выгружен CSV со всеми пользователями ({len(rows)} шт.)")
    return web.Response(text=buf.getvalue(), content_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"})


@require_login
async def export_payments_csv(request):
    import csv, io
    rows = await db_fetch("""
        SELECT p.*, u.username, u.full_name
        FROM payments p LEFT JOIN users u ON p.tg_id = u.tg_id
        ORDER BY p.paid_at DESC
    """)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    await log_action("export_payments", details=f"выгружен CSV с платежами ({len(rows)} шт.)")
    return web.Response(text=buf.getvalue(), content_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments.csv"})


# ─── Promo codes ─────────────────────────────────────────────

@require_login
async def promo_page(request):
    promos = await get_all_promos()
    rows_html = ""
    for p in promos:
        status = '<span class="badge badge-active">Активный</span>' if p["is_active"] else '<span class="badge badge-expired">Отключен</span>'
        limit = p["max_uses"] if p["max_uses"] else "∞"
        toggle_text = "Отключить" if p["is_active"] else "Включить"

        users = await db_fetch("""
            SELECT pay.paid_at, pay.amount, u.username, u.full_name, u.tg_id
            FROM payments pay LEFT JOIN users u ON pay.tg_id = u.tg_id
            WHERE pay.promo_code = ? AND pay.status='success'
            ORDER BY pay.paid_at DESC
        """, (p["code"],))

        if users:
            users_html = "".join(
                f"<div style='display:flex;justify-content:space-between;font-size:13px;padding:4px 0;border-bottom:1px solid #f0f0f5;'>"
                f"<span>{u['full_name'] or u['tg_id']} {('@'+u['username']) if u['username'] else ''}</span>"
                f"<span style='color:#6b7280'>{(u['paid_at'] or '')[:16].replace('T',' ')} · {u['amount']:.2f} €</span>"
                "</div>"
                for u in users
            )
        else:
            users_html = "<div style='font-size:13px;color:#6b7280;'>Ещё никто не использовал</div>"

        rows_html += (
            "<tr>"
            f"<td><strong>{p['code']}</strong></td>"
            f"<td>{p['discount']}%</td>"
            f"<td>{p['used_count']} / {limit}</td>"
            f"<td>{status}</td>"
            f"<td style='display:flex;gap:6px'>"
            f"<a href='/admin/promo/toggle?code={p['code']}' class='btn-sm'>{toggle_text}</a>"
            f"<a href='/admin/promo/delete?code={p['code']}' class='btn-sm danger'>Удалить</a>"
            "</td></tr>"
            f"<tr><td colspan='5' style='background:#fafafe;padding:8px 12px;'>{users_html}</td></tr>"
        )
    return await render("promo.html", page="promo", rows=rows_html, msg="")


@require_login
async def promo_create(request):
    data = await request.post()
    code = data.get("code","").strip().upper()
    try:
        discount = int(data.get("discount", 0) or 0)
        max_uses = int(data.get("max_uses", 0) or 0)
    except ValueError:
        discount = max_uses = 0
    if code and 0 < discount <= 100:
        await create_promo(code, discount, max_uses)
        await log_action("promo_create", query=code,
                         details=f"скидка {discount}%, лимит: {max_uses or '∞'}")
    else:
        await log_action("promo_create", query=code,
                         details=f"отклонено: скидка {discount}%, лимит {max_uses}", ok=False)
    raise web.HTTPFound("/admin/promo")


@require_login
async def promo_toggle(request):
    code = request.rel_url.query.get("code","")
    await toggle_promo(code)
    await log_action("promo_toggle", query=code, details="переключен вкл/выкл")
    raise web.HTTPFound("/admin/promo")


@require_login
async def promo_delete(request):
    code = request.rel_url.query.get("code","")
    await delete_promo(code)
    await log_action("promo_delete", query=code, details="промокод удалён безвозвратно")
    raise web.HTTPFound("/admin/promo")


ACTION_LABELS = {
    "give":             ("🎁", "Выдача доступа"),
    "kick":             ("🚫", "Удаление"),
    "find":             ("🔍", "Поиск"),
    "broadcast":        ("📣", "Рассылка"),
    "promo_create":     ("🎟", "Промокод создан"),
    "promo_toggle":     ("🎟", "Промокод вкл/выкл"),
    "promo_delete":     ("🎟", "Промокод удалён"),
    "support_reply":    ("💬", "Ответ в поддержку"),
    "support_read_all": ("💬", "Поддержка прочитана"),
    "backup":           ("🗄", "Скачана база"),
    "export_users":     ("📤", "Экспорт пользователей"),
    "export_payments":  ("📤", "Экспорт платежей"),
    "login":            ("🔑", "Вход в панель"),
    "logout":           ("🚪", "Выход"),
    "auto_kick":        ("⏳", "Авто-снятие доступа"),
}

SOURCE_LABELS = {"panel": "панель", "bot": "команда в боте", "auto": "планировщик"}


@require_login
async def logs_page(request):
    from html import escape
    rows = await get_admin_log(300)
    rows_html = ""
    for r in rows:
        emoji, label = ACTION_LABELS.get(r["action"], ("•", r["action"]))
        when = (r["created_at"] or "").replace("T", " ")[:16]
        src = SOURCE_LABELS.get(r["source"], r["source"] or "")
        act = f'{emoji} {label}'
        if not r["ok"]:
            act += ' <span class="badge badge-expired">ошибка</span>'
        act += f'<div style="font-size:12px;color:#6b7280">{src}</div>'
        if r["target_tg_id"]:
            who = (f'<div>{escape(r["target_name"] or "")}</div>'
                   f'<div style="font-size:12px;color:#6b7280">{r["target_tg_id"]} · код {encode_id(r["target_tg_id"])}</div>')
        else:
            who = '<span style="color:#6b7280">—</span>'
        rows_html += (
            "<tr>"
            f"<td style='white-space:nowrap'>{when}</td>"
            f"<td style='white-space:nowrap'>{act}</td>"
            f"<td>{who}</td>"
            f"<td style='font-size:13px;color:#6b7280'>{escape(r['query'] or '—')}</td>"
            f"<td style='font-size:13px'>{escape(r['details'] or '')}</td>"
            "</tr>"
        )
    if not rows_html:
        rows_html = "<tr><td colspan='5' style='color:#6b7280;padding:16px'>Пока пусто — журнал заполняется с момента обновления панели</td></tr>"
    return await render("logs.html", page="logs", rows=rows_html, count=len(rows))


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
    app.router.add_get ("/admin/payments",   payments_page)
    app.router.add_get ("/admin/receipt/{order_id}", receipt_page)
    app.router.add_get ("/admin/export/users.csv",    export_users_csv)
    app.router.add_get ("/admin/export/payments.csv", export_payments_csv)
    app.router.add_get ("/admin/promo",        promo_page)
    app.router.add_post("/admin/promo/create", promo_create)
    app.router.add_get ("/admin/promo/toggle", promo_toggle)
    app.router.add_get ("/admin/promo/delete", promo_delete)
    app.router.add_get ("/admin/support",       support_page)
    app.router.add_get ("/admin/support/read",  support_mark_read)
    app.router.add_get ("/admin/support/read_all", support_mark_all_read)
    app.router.add_get ("/admin/support/reply", support_reply_get)
    app.router.add_post("/admin/support/reply", support_reply_post)
    app.router.add_get ("/admin/logs",          logs_page)
    app.router.add_get ("/admin/backup",        backup_db)


@require_login
async def payments_page(request):
    rows = await db_fetch("""
        SELECT p.*, u.username, u.full_name
        FROM payments p
        LEFT JOIN users u ON p.tg_id = u.tg_id
        ORDER BY p.paid_at DESC
        LIMIT 100
    """)

    total_all = (await db_one("SELECT COALESCE(SUM(amount),0) as s FROM payments WHERE status='success'"))["s"]
    total_month = (await db_one("SELECT COALESCE(SUM(amount),0) as s FROM payments WHERE status='success' AND strftime('%Y-%m',paid_at)=strftime('%Y-%m','now')"))["s"]
    count_all = (await db_one("SELECT COUNT(*) as c FROM payments WHERE status='success'"))["c"]

    rows_html = ""
    for p in rows:
        status_badge = {
            "success": '<span class="badge badge-active">Успешно</span>',
            "sandbox": '<span class="badge" style="background:#e0f2fe;color:#0369a1;">Тест</span>',
            "failure": '<span class="badge badge-expired">Ошибка</span>',
        }.get(p["status"], f'<span class="badge">{p["status"]}</span>')

        paid_at = (p["paid_at"] or "")[:16].replace("T", " ")
        name = p["full_name"] or "—"
        un = f'@{p["username"]}' if p["username"] else str(p["tg_id"])
        plan = PLANS.get(p["plan"], p["plan"] or "—")
        amount = p["amount"]
        amount_str = f"{amount:.2f}".rstrip("0").rstrip(".") if amount is not None else "0"
        promo = f' <span style="font-size:11px;color:#6b7280;">({p["promo_code"]})</span>' if p.get("promo_code") else ""
        receipt = f'<a href="/admin/receipt/{p["order_id"]}" class="btn-sm">Чек</a>' if p["status"] == "success" else "—"

        rows_html += (
            "<tr>"
            f"<td>{paid_at}</td>"
            f"<td><div style='font-weight:500'>{name}</div><div style='font-size:12px;color:#6b7280'>{un}</div></td>"
            f"<td>{plan}{promo}</td>"
            f"<td style='font-weight:600;color:#16a34a'>{amount_str} €</td>"
            f"<td>{status_badge}</td>"
            f"<td>{receipt}</td>"
            "</tr>"
        )

    return await render("payments.html", page="payments",
        rows=rows_html,
        total_all=f"{total_all:.2f}".rstrip("0").rstrip("."),
        total_month=f"{total_month:.2f}".rstrip("0").rstrip("."),
        count_all=count_all,
    )


@require_login
async def receipt_page(request):
    try:
        return await _receipt_page_inner(request)
    except Exception as e:
        logger.exception(f"receipt_page error: {e}")
        return web.Response(text=f"Ошибка: {e}", status=500)


async def _receipt_page_inner(request):
    order_id = request.match_info["order_id"]
    p = await db_one("""
        SELECT p.*, u.username, u.full_name, u.tg_id as utg
        FROM payments p LEFT JOIN users u ON p.tg_id = u.tg_id
        WHERE p.order_id = ?
    """, (order_id,))
    if not p:
        return web.Response(text="Платёж не найден", status=404)

    paid_at = (p["paid_at"] or "")[:16].replace("T", " ")
    full_name = p["full_name"] or "—"
    un = f'@{p["username"]}' if p["username"] else "—"
    plan = PLANS.get(p["plan"], p["plan"] or "—")
    amount = p["amount"]
    amount_str = f"{amount:.2f}".rstrip("0").rstrip(".") if amount is not None else "0"
    paypal_oid = p.get("paypal_order_id") or "—"
    promo = p.get("promo_code") or "—"
    provider = p.get("provider") or PAYMENT_PROVIDER
    provider_names = {"paypal": "PayPal", "lemonsqueezy": "Lemon Squeezy", "whop": "Whop"}
    provider_name = provider_names.get(provider, provider)
    paypal_link = ""
    if provider == "paypal" and p.get("paypal_order_id"):
        mode = "sandbox." if PAYPAL_MODE == "sandbox" else ""
        paypal_link = f'<a href="https://www.{mode}paypal.com/activity/payment/{p["paypal_order_id"]}" target="_blank" class="btn-sm">Открыть в PayPal →</a>'

    return await render("receipt.html", page="payments",
        order_id=p["order_id"],
        paid_at=paid_at,
        provider_name=provider_name,
        name=full_name,
        username=un,
        tg_id=p["tg_id"],
        plan=plan,
        amount=amount_str,
        status=p["status"],
        paypal_order_id=paypal_oid,
        promo_code=promo,
        paypal_link=paypal_link,
    )
