# ═══════════════════════════════════════════════════════════════
# app.py — SHAPPNO Web Panel (Full & Fixed)
# ═══════════════════════════════════════════════════════════════

import os
import urllib.request
from functools import wraps
import sqlite3, hashlib, secrets, time, sys, asyncio, threading
from flask import Flask, request, jsonify, session, render_template, redirect, url_for

import bot_core

# ── Embedded database layer (database.py consolidated here) ──



DB_PATH = "shappno.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Global settings (website_on, admin_password)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Admin-created access passwords (used in Settings icon login)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            password TEXT NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1,
            duration_hours INTEGER NOT NULL DEFAULT 24,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )
    """)

    # Public users (auto-created on public login)
    c.execute("""
        CREATE TABLE IF NOT EXISTS public_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            login_time INTEGER,
            session_token TEXT
        )
    """)

    # Bots
    c.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_type TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            bot_name TEXT NOT NULL,
            bot_uid TEXT NOT NULL,
            bot_password TEXT NOT NULL,
            region TEXT NOT NULL,
            display_name TEXT NOT NULL,
            running INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    """)

    # Logs
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            log_text TEXT,
            timestamp INTEGER NOT NULL
        )
    """)

    # Default settings
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('website_on', '1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', '1444444655514444446555')")

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def create_admin_key(password, slots, duration_hours):
    now = int(time.time())
    expires = now + duration_hours * 3600
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO admin_keys (password, slots, duration_hours, created_at, expires_at, active) VALUES (?,?,?,?,?,1)",
        (password, slots, duration_hours, now, expires)
    )
    conn.commit()
    kid = cur.lastrowid
    conn.close()
    return kid


def verify_admin_key(password):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM admin_keys WHERE password=? AND active=1", (password,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if int(time.time()) > row["expires_at"]:
        return None
    return dict(row)


def list_admin_keys():
    conn = get_db()
    rows = conn.execute("SELECT * FROM admin_keys ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_admin_key(kid):
    conn = get_db()
    row = conn.execute("SELECT active FROM admin_keys WHERE id=?", (kid,)).fetchone()
    if row:
        conn.execute("UPDATE admin_keys SET active=? WHERE id=?", (0 if row["active"] else 1, kid))
        conn.commit()
    conn.close()


def delete_admin_key(kid):
    conn = get_db()
    conn.execute("DELETE FROM admin_keys WHERE id=?", (kid,))
    conn.commit()
    conn.close()


def public_login(identifier):
    ident = identifier.strip()
    if not ident:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()
    if not user:
        try:
            conn.execute("INSERT INTO public_users (username, login_time) VALUES (?, ?)", (ident, int(time.time())))
            conn.commit()
            user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()
        except sqlite3.IntegrityError:
            user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()
    token = secrets.token_hex(24)
    conn.execute("UPDATE public_users SET session_token=?, login_time=? WHERE id=?", (token, int(time.time()), user["id"]))
    conn.commit()
    conn.close()
    return {"token": token, "user_id": user["id"], "username": user["username"], "owner_type": "public", "slots": 1}


def admin_login_session(password):
    key = verify_admin_key(password)
    if not key:
        return None
    token = secrets.token_hex(24)
    return {
        "token": token,
        "user_id": key["id"],
        "username": f"Admin#{key['id']}",
        "owner_type": "admin",
        "slots": key["slots"],
        "expires_at": key["expires_at"],
    }


def verify_session(token):
    if not token:
        return None
    conn = get_db()
    u = conn.execute("SELECT * FROM public_users WHERE session_token=?", (token,)).fetchone()
    conn.close()
    if u:
        return {"user_id": u["id"], "username": u["username"], "owner_type": "public", "slots": 1}
    return None


def add_bot(owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, max_slots):
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM bots WHERE owner_type=? AND owner_id=?",
        (owner_type, owner_id)
    ).fetchone()["c"]
    if count >= max_slots:
        conn.close()
        return None, f"Slot limit reached ({max_slots})"
    cur = conn.execute(
        "INSERT INTO bots (owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, int(time.time()))
    )
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid, None


def get_bots(owner_type, owner_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bots WHERE owner_type=? AND owner_id=? ORDER BY id DESC",
        (owner_type, owner_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_by_id(bot_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def update_bot_running(bot_id, running):
    conn = get_db()
    conn.execute("UPDATE bots SET running=? WHERE id=?", (1 if running else 0, bot_id))
    conn.commit()
    conn.close()


def delete_bot(bot_id):
    conn = get_db()
    conn.execute("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,))
    conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()


def add_log(bot_id, text):
    conn = get_db()
    conn.execute("INSERT INTO bot_logs (bot_id, log_text, timestamp) VALUES (?,?,?)", (bot_id, text, int(time.time())))
    conn.execute("""DELETE FROM bot_logs WHERE bot_id=? AND id NOT IN (
        SELECT id FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT 400)""", (bot_id, bot_id))
    conn.commit()
    conn.close()


def get_logs(bot_id, limit=200):
    conn = get_db()
    rows = conn.execute("SELECT * FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def clear_logs(bot_id):
    conn = get_db()
    conn.execute("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,))
    conn.commit()
    conn.close()


def admin_stats():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) AS c FROM public_users").fetchone()["c"]
    bots = conn.execute("SELECT COUNT(*) AS c FROM bots").fetchone()["c"]
    running = conn.execute("SELECT COUNT(*) AS c FROM bots WHERE running=1").fetchone()["c"]
    keys = conn.execute("SELECT COUNT(*) AS c FROM admin_keys").fetchone()["c"]
    conn.close()
    return {"users": users, "bots": bots, "running": running, "keys": keys}


def admin_all_users():
    conn = get_db()
    rows = conn.execute("""
        SELECT u.*, 
               (SELECT COUNT(*) FROM bots b WHERE b.owner_type='public' AND b.owner_id=u.id) AS bot_count,
               (SELECT COUNT(*) FROM bots b WHERE b.owner_type='public' AND b.owner_id=u.id AND b.running=1) AS running_count
        FROM public_users u ORDER BY u.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_all_bots():
    conn = get_db()
    rows = conn.execute("SELECT * FROM bots ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot(bot_id):
    return get_bot_by_id(bot_id)


def update_bot_status(bot_id, status, running):
    """Old signature: (bot_id, status_string, running_bool)"""
    update_bot_running(bot_id, running)


# ── Embedded bot lifecycle manager (bot_manager.py consolidated here) ──
# ═══════════════════════════════════════════════════════════════
# bot_manager.py — Multi-bot lifecycle manager
# ═══════════════════════════════════════════════════════════════


# ─── Global registry ───
# bot_id -> {task, loop, running, name}
BOTS = {}
_BOTS_LOCK = threading.Lock()

# A dedicated event loop thread for ALL bots
_LOOP = asyncio.new_event_loop()
_LOOP_THREAD = threading.Thread(target=_LOOP.run_forever, daemon=True)
_LOOP_THREAD.start()


def _get_loop():
    return _LOOP


class BotRunner:
    def __init__(self, bot_id, bot_uid, bot_password, region, display_name):
        self.bot_id = bot_id
        self.bot_uid = bot_uid
        self.bot_password = bot_password
        self.region = region
        self.display_name = display_name
        self.task = None
        self.running = False
        self.connected = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.run_coroutine_threadsafe(self._run(), _get_loop())
        update_bot_status(self.bot_id, "on", 0)
        add_log(self.bot_id, f"[+] Bot starting... ({self.display_name})")

    def stop(self):
        self.running = False
        self.connected = False
        if self.task and not self.task.done():
            self.task.cancel()
        update_bot_status(self.bot_id, "off", 0)
        add_log(self.bot_id, "[-] Bot stopped")

    async def _watch_online(self, ready_event):
        await ready_event.wait()
        if self.running:
            self.connected = True
            update_bot_status(self.bot_id, "on", 1)
            add_log(self.bot_id, "[+] Chat + Online TCP connected; bot is ONLINE")

    async def _run(self):
        import login_engine
        while self.running:
            try:
                add_log(self.bot_id, "[*] Logging in...")
                result = await login_engine.do_login(self.bot_uid, self.bot_password, self.region)
                if not result:
                    add_log(self.bot_id, "[-] Login failed, retry in 15s")
                    await asyncio.sleep(15)
                    continue

                add_log(self.bot_id, f"[+] Logged in as {result['account_name']} (UID {result['bot_uid']})")
                bot_core.bot_uid = result["bot_uid"]
                bot_core.ToKen = result["token"]
                bot_core.region = result["region"]
                bot_core.DISPLAY_NAME = self.display_name  # override branding

                key = result["key"]; iv = result["iv"]
                ts = result["timestamp"]

                ChatAutH = bot_core.create_auth_token_chat(
                    int(result["bot_uid"]), result["token"], int(ts), key, iv
                )
                OnlineAutH, _ = bot_core.build_ob55_online_auth(
                    int(result["bot_uid"]), result["token"], int(ts), key, iv
                )

                ready = asyncio.Event()
                t1 = asyncio.create_task(
                    bot_core.TcPChaT(result["chat_ip"], result["chat_port"], ChatAutH, key, iv, ready, result["region"])
                )
                try:
                    await asyncio.wait_for(ready.wait(), timeout=25)
                except asyncio.TimeoutError:
                    t1.cancel()
                    add_log(self.bot_id, "[!] Chat TCP connection timed out; retrying")
                    await asyncio.sleep(5)
                    continue
                await asyncio.sleep(1)
                online_ready = asyncio.Event()
                t2 = asyncio.create_task(
                    bot_core.TcPOnLine(result["online_ip"], result["online_port"], key, iv, OnlineAutH, ready_event=online_ready)
                )
                online_watch = asyncio.create_task(self._watch_online(online_ready))
                try:
                    await asyncio.wait_for(asyncio.shield(online_ready.wait()), timeout=30)
                except asyncio.TimeoutError:
                    add_log(self.bot_id, "[!] Online TCP not connected yet; retrying in background")
                await asyncio.gather(t1, t2, online_watch)
            except asyncio.CancelledError:
                add_log(self.bot_id, "[-] Bot cancelled")
                break
            except Exception as e:
                add_log(self.bot_id, f"[!] Error: {e} -> restart in 10s")
                await asyncio.sleep(10)
        self.running = False
        self.connected = False
        update_bot_status(self.bot_id, "off", 0)


def start_bot(bot_id):
    with _BOTS_LOCK:
        if bot_id in BOTS and BOTS[bot_id].running:
            return True
        bot = get_bot(bot_id)
        if not bot:
            return False
        runner = BotRunner(
            bot_id, bot["bot_uid"], bot["bot_password"],
            bot["region"], bot["display_name"]
        )
        BOTS[bot_id] = runner
        runner.start()
        return True


def stop_bot(bot_id):
    with _BOTS_LOCK:
        if bot_id in BOTS:
            BOTS[bot_id].stop()
            del BOTS[bot_id]
            return True
    return False


def restart_bot(bot_id):
    stop_bot(bot_id)
    time.sleep(1)
    return start_bot(bot_id)


def is_running(bot_id):
    with _BOTS_LOCK:
        return bot_id in BOTS and BOTS[bot_id].running and BOTS[bot_id].connected
# Compatibility namespaces for existing route code.
db = sys.modules[__name__]
bot_manager = sys.modules[__name__]
init_db()

app = Flask(__name__, template_folder=".")
app.secret_key = os.environ.get("SECRET_KEY", "shappno_change_me_2024_secure")

REGIONS = ["BD", "IND", "PK", "SG", "ID", "ME", "VN", "TW", "CIS", "TH", "EU", "US"]

# ═══════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════
def current_user():
    tok = session.get("token")
    if tok:
        u = db.verify_session(tok)
        if u:
            return u
    if session.get("admin_key_id"):
        return {
            "user_id": session.get("admin_key_id"),
            "username": session.get("admin_username", "Admin"),
            "owner_type": "admin",
            "slots": session.get("admin_slots", 1),
            "expires_at": session.get("admin_expires", 0),
        }
    return None


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login_page"))
        return f(*a, user=u, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin_panel"):
            return redirect(url_for("admin_page"))
        return f(*a, **kw)
    return wrapper


# ═══════════════════════════════════════════════════════════════
# PUBLIC LOGIN
# ═══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    if db.get_setting("website_on", "1") != "1":
        return render_template("Index.html", page="login", off=True, regions=REGIONS)
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    off = db.get_setting("website_on", "1") != "1"
    return render_template("Index.html", page="login", off=off, regions=REGIONS)


@app.route("/api/login", methods=["POST"])
def api_login():
    if db.get_setting("website_on", "1") != "1":
        return jsonify({"ok": False, "msg": "Website is temporarily off"}), 403
    data = request.json or {}
    ident = (data.get("identifier") or "").strip()
    if not ident:
        return jsonify({"ok": False, "msg": "Enter username or password"}), 400
    res = db.public_login(ident)
    if not res:
        return jsonify({"ok": False, "msg": "Invalid"}), 400
    session["token"] = res["token"]
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ═══════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════
@app.route("/dashboard")
@login_required
def dashboard(user):
    return render_template("Index.html", page="dashboard", user=user, regions=REGIONS)


@app.route("/api/me")
@login_required
def api_me(user):
    bots = db.get_bots(user["owner_type"], user["user_id"])
    for b in bots:
        b["running"] = bot_manager.is_running(b["id"])
        db.update_bot_running(b["id"], b["running"])
    return jsonify({"ok": True, "user": user, "bots": bots})


@app.route("/api/bots/add", methods=["POST"])
@login_required
def api_bot_add(user):
    d = request.json or {}
    bot_name = (d.get("bot_name") or "").strip()
    bot_uid = (d.get("bot_uid") or "").strip()
    bot_password = (d.get("bot_password") or "").strip()
    region = (d.get("region") or "").strip()
    display_name = (d.get("display_name") or "").strip() or bot_name

    if not all([bot_name, bot_uid, bot_password, region]):
        return jsonify({"ok": False, "msg": "All fields required"}), 400

    bid, err = db.add_bot(
        user["owner_type"], user["user_id"],
        bot_name, bot_uid, bot_password, region, display_name,
        user["slots"]
    )
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    return jsonify({"ok": True, "bot_id": bid})


@app.route("/api/bots/<int:bid>/<action>", methods=["POST"])
@login_required
def api_bot_action(user, bid, action):
    bot = db.get_bot_by_id(bid)
    if not bot:
        return jsonify({"ok": False, "msg": "Bot not found"}), 404
    if bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False, "msg": "Not yours"}), 403

    if action in ("on", "start"):
        if not bot_manager.start_bot(bid):
            return jsonify({"ok": False, "msg": "Bot could not be started"}), 500
        db.update_bot_running(bid, True)
    elif action in ("off", "stop"):
        bot_manager.stop_bot(bid)
        db.update_bot_running(bid, False)
    elif action == "restart":
        bot_manager.restart_bot(bid)
    elif action == "delete":
        bot_manager.stop_bot(bid)
        db.delete_bot(bid)
    else:
        return jsonify({"ok": False, "msg": "Invalid action"}), 400

    return jsonify({"ok": True})


@app.route("/api/bots/<int:bid>/logs")
@login_required
def api_bot_logs(user, bid):
    bot = db.get_bot_by_id(bid)
    if not bot:
        return jsonify({"ok": False}), 404
    if bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True, "logs": db.get_logs(bid, 200)})


@app.route("/api/bots/<int:bid>/logs/clear", methods=["POST"])
@login_required
def api_bot_logs_clear(user, bid):
    bot = db.get_bot_by_id(bid)
    if not bot:
        return jsonify({"ok": False}), 404
    if bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False}), 403
    db.clear_logs(bid)
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════
# SETTINGS (admin key login)
# ═══════════════════════════════════════════════════════════════
@app.route("/settings")
def settings_page():
    return render_template("Index.html", page="settings")


@app.route("/api/settings-login", methods=["POST"])
def api_settings_login():
    d = request.json or {}
    pw = (d.get("password") or "").strip()
    if not pw:
        return jsonify({"ok": False, "msg": "Enter password"}), 400

    res = db.admin_login_session(pw)
    if not res:
        return jsonify({"ok": False, "msg": "Invalid or expired password"}), 401

    session["token"] = res["token"]
    session["admin_key_id"] = res["user_id"]
    session["admin_username"] = res["username"]
    session["admin_slots"] = res["slots"]
    session["admin_expires"] = res["expires_at"]
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.route("/settings/logout")
def settings_logout():
    for k in ("token", "admin_key_id", "admin_username", "admin_slots", "admin_expires"):
        session.pop(k, None)
    return redirect(url_for("login_page"))


# ═══════════════════════════════════════════════════════════════
# ADMIN PANEL (master password)
# ═══════════════════════════════════════════════════════════════
@app.route("/admin-shappno")
def admin_page():
    return render_template("Index.html", page="admin")


@app.route("/admin-shappno/login", methods=["POST"])
def admin_login():
    d = request.json or {}
    pw = d.get("password", "")
    if pw == db.get_setting("admin_password", "1444444655514444446555"):
        session["admin_panel"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "Wrong password"}), 401


@app.route("/admin-shappno/logout")
def admin_logout():
    session.pop("admin_panel", None)
    return redirect(url_for("admin_page"))


@app.route("/admin-shappno/api/stats")
@admin_required
def admin_api_stats():
    return jsonify({
        "ok": True,
        "stats": db.admin_stats(),
        "website_on": db.get_setting("website_on", "1") == "1"
    })


@app.route("/admin-shappno/api/website-toggle", methods=["POST"])
@admin_required
def admin_toggle_website():
    cur = db.get_setting("website_on", "1")
    new = "0" if cur == "1" else "1"
    db.set_setting("website_on", new)
    return jsonify({"ok": True, "website_on": new == "1"})


@app.route("/admin-shappno/api/keys", methods=["GET"])
@admin_required
def admin_api_keys():
    return jsonify({"ok": True, "keys": db.list_admin_keys()})


@app.route("/admin-shappno/api/keys/create", methods=["POST"])
@admin_required
def admin_api_keys_create():
    d = request.json or {}
    try:
        pw = (d.get("password") or "").strip()
        slots = int(d.get("slots", 1))
        hours = int(d.get("duration_hours", 24))
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid numbers"}), 400
    if not pw or slots < 1 or hours < 1:
        return jsonify({"ok": False, "msg": "All fields > 0"}), 400
    kid = db.create_admin_key(pw, slots, hours)
    return jsonify({"ok": True, "id": kid})


@app.route("/admin-shappno/api/keys/<int:kid>/toggle", methods=["POST"])
@admin_required
def admin_api_key_toggle(kid):
    db.toggle_admin_key(kid)
    return jsonify({"ok": True})


@app.route("/admin-shappno/api/keys/<int:kid>/delete", methods=["POST"])
@admin_required
def admin_api_key_delete(kid):
    db.delete_admin_key(kid)
    return jsonify({"ok": True})


@app.route("/admin-shappno/api/users")
@admin_required
def admin_api_users():
    return jsonify({"ok": True, "users": db.admin_all_users()})


@app.route("/admin-shappno/api/bots")
@admin_required
def admin_api_bots():
    return jsonify({"ok": True, "bots": db.admin_all_bots()})



@app.route("/admin-shappno/api/free-users/purge", methods=["POST"])
@admin_required
def admin_purge_free_users():
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"ok": False, "msg": "Confirmation required"}), 400
    conn = get_db()
    rows = conn.execute("SELECT id FROM public_users").fetchall()
    user_ids = [int(r["id"]) for r in rows]
    bot_rows = conn.execute("SELECT id FROM bots WHERE owner_type='public'").fetchall()
    bot_ids = [int(r["id"]) for r in bot_rows]
    for bid in bot_ids:
        stop_bot(bid)
    conn.execute("DELETE FROM bot_logs WHERE bot_id IN (SELECT id FROM bots WHERE owner_type='public')")
    conn.execute("DELETE FROM bots WHERE owner_type='public'")
    conn.execute("DELETE FROM public_users")
    conn.commit(); conn.close()
    return jsonify({"ok": True, "deleted_users": len(user_ids), "deleted_bots": len(bot_ids)})

# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════
@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200


@app.route("/api/ping")
def api_ping():
    return {"ok": True, "service": "shappno"}, 200


# Optional lightweight self-ping. This is a best-effort health pulse only;
# it does NOT override Render's free-instance sleep or usage policies.
def keep_alive_forever():
    while True:
        time.sleep(300)
        try:
            render_url = os.environ.get("RENDER_EXTERNAL_URL")
            if render_url:
                ping_url = f"{render_url.rstrip('/')}/api/ping"
            else:
                port = os.environ.get("PORT", "8080")
                ping_url = f"http://127.0.0.1:{port}/api/ping"
            req = urllib.request.Request(ping_url, headers={"User-Agent": "SHAPPNO-Health/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read(64)
            print(f"[KeepAlive] Ping sent at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as exc:
            print(f"[KeepAlive] Ping failed: {exc}")


# Run once in the single-worker Render configuration.
if os.environ.get("DISABLE_SELF_PING", "0") != "1":
    threading.Thread(target=keep_alive_forever, daemon=True, name="keep-alive").start()


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[*] SHAPPNO Web Panel starting on port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)