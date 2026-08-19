#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UZUNITED — Backend (bitta fayl, Flask)
=======================================

Bu fayl quyidagilarni o'z ichiga oladi:
  - Admin panel (login orqali)            ->  GET/POST  /            (yoki /admin)
  - Sozlamalar: AI API key, Telegram bot token, kamida 4 ta Telegram ID
  - Banerlar: rasm yuklash + link URL qo'shish/o'chirish
  - Buyurtmalar ro'yxati (admin panelda ko'rinadi)
  - Foydalanuvchilar ro'yxati (admin panelda ko'rinadi)
  - Ochiq (public) API:
        GET  /api/banners        -> saytdagi banerlar reklama uchun
        POST /api/order          -> "buyurtma berish" formasi -> Telegramga xabar
        POST /api/chat           -> UZUNITED AI chat (Groq API orqali)
        POST /api/register       -> ro'yxatdan o'tish
        POST /api/login          -> tizimga kirish
        GET  /api/my-orders      -> foydalanuvchining buyurtmalari (token bilan)

O'RNATISH:
    pip install -r requirements.txt
    python backend.py

ISHGA TUSHIRISH:
    Standart manzil: http://localhost:5000
    Admin panel:      http://localhost:5000/     (login: OA77 / UZUNITED)

MUHIM:
  - config.json, users.json, orders.json fayllari birinchi ishga tushirishda
    avtomatik yaratiladi (shu papkaning ichida).
  - AI API key (Groq) ADMIN PANEL orqali kiritiladi — bu faylga yozilmagan,
    chunki suhbatda yuborilgan kalit qisman berkitilgan (gsk*****Ju) edi va
    to'liq holda ishlatib bo'lmaydi. Admin panelga to'liq kalitni kiriting.
  - Telegram bot tokeni va birinchi ID (siz yuborgan qiymatlar) standart
    sifatida config.json ga yoziladi, keyinchalik admin paneldan o'zgartirishingiz mumkin.
  - Ishlab chiqarishga (production) chiqarishdan oldin: HTTPS ishlating,
    admin parolini o'zgartiring va debug=False bilan ishga tushiring (allaqachon shunday).
"""

import os
import json
import secrets
import datetime
import threading
from functools import wraps

from flask import (
    Flask, request, jsonify, session, redirect,
    render_template_string, send_from_directory, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import requests

# ----------------------------------------------------------------------------
# Sozlamalar / yo'llar
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
USERS_PATH = os.path.join(BASE_DIR, "users.json")
ORDERS_PATH = os.path.join(BASE_DIR, "orders.json")

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB

os.makedirs(UPLOAD_DIR, exist_ok=True)
_lock = threading.Lock()

DEFAULT_SYSTEM_PROMPT = (
    "Sen UZUNITED kompaniyasining rasmiy AI-yordamchisisan (nomi: 'UZUNITED AI'). "
    "UZUNITED — mobil ilova, veb-sayt, backend tizimlar va Telegram bot ishlab "
    "chiqish bilan shug'ullanuvchi IT-kompaniya. Mijozlarga xizmatlar, narxlar "
    "taxminiy tartibi va buyurtma jarayoni haqida qisqa, aniq va do'stona javob ber. "
    "O'zbek tilida, samimiy va professional uslubda yoz. Agar savol UZUNITED "
    "xizmatlariga aloqador bo'lmasa, muloyimlik bilan mavzuga qaytar. Aniq narxni "
    "bilmasang, mijozni 'Buyurtma berish' formasini to'ldirishga yoki Telegram "
    "orqali bog'lanishga taklif qil."
)

DEFAULT_CONFIG = {
    "secret_key": secrets.token_hex(32),
    "admin": {
        "username": "OA77",
        "password_hash": generate_password_hash("UZUNITED"),
    },
    "ai": {
        "api_key": "",  # Admin panel orqali to'ldiring (Groq API key, gsk_... bilan boshlanadi)
        "model": "llama-3.3-70b-versatile",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
    },
    "telegram": {
        "bot_token": "8679546571:AAH5liF0WCOgNhBheKMIt3Nqgqe67ax-YL0",
        "chat_ids": ["7786233288", "", "", ""],
    },
    "banners": [],
}


# ----------------------------------------------------------------------------
# Oddiy JSON saqlash (fayl asosida, bazasiz)
# ----------------------------------------------------------------------------
def _load(path, default):
    if not os.path.exists(path):
        _save(path, default)
        return json.loads(json.dumps(default))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def load_config():
    cfg = _load(CONFIG_PATH, DEFAULT_CONFIG)
    # eski config fayllarga yangi kalitlar qo'shilishi uchun (versiya yangilansa)
    changed = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        _save(CONFIG_PATH, cfg)
    return cfg


def save_config(cfg):
    _save(CONFIG_PATH, cfg)


def load_users():
    return _load(USERS_PATH, {"users": [], "next_id": 1})


def save_users(data):
    _save(USERS_PATH, data)


def load_orders():
    return _load(ORDERS_PATH, {"orders": [], "next_id": 1})


def save_orders(data):
    _save(ORDERS_PATH, data)


# ----------------------------------------------------------------------------
# Flask ilova
# ----------------------------------------------------------------------------
app = Flask(__name__)
_cfg_bootstrap = load_config()
app.secret_key = _cfg_bootstrap["secret_key"]
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


@app.after_request
def add_cors_headers(resp):
    # Frontend boshqa domenda joylashgani uchun CORS ochiq qilingan.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def cors_preflight(_any):
    return ("", 204)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# Telegram va AI yordamchi funksiyalar
# ----------------------------------------------------------------------------
def send_telegram_message(text):
    cfg = load_config()
    token = cfg["telegram"].get("bot_token", "").strip()
    chat_ids = [c.strip() for c in cfg["telegram"].get("chat_ids", []) if c and c.strip()]
    if not token or not chat_ids:
        return False
    ok_any = False
    for chat_id in chat_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=8,
            )
            if r.ok:
                ok_any = True
        except requests.RequestException:
            continue
    return ok_any


def ask_ai(user_message, history=None):
    cfg = load_config()
    api_key = cfg["ai"].get("api_key", "").strip()
    if not api_key:
        return (
            False,
            "AI hozircha sozlanmagan. Admin panel orqali AI API kalitini kiriting. "
            "Shu orada savolingizni Telegram orqali hamkorlik bo'limidagi tugma bilan yuboring.",
        )

    model = cfg["ai"].get("model", "llama-3.3-70b-versatile").strip()
    system_prompt = cfg["ai"].get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for h in history[-8:]:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)[:2000]})
    messages.append({"role": "user", "content": str(user_message)[:2000]})

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 600,
            },
            timeout=25,
        )
        if r.status_code == 200:
            data = r.json()
            reply = data["choices"][0]["message"]["content"].strip()
            return True, reply
        else:
            return (
                False,
                "AI xizmatidan javob olishda xatolik yuz berdi (API kaliti yoki "
                "model nomini admin panelda tekshiring).",
            )
    except requests.RequestException:
        return False, "AI xizmatiga ulanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring."
    except (KeyError, IndexError, ValueError):
        return False, "AI javobini o'qishda xatolik yuz berdi."


def find_user_by_token(token):
    if not token:
        return None
    data = load_users()
    for u in data["users"]:
        if u.get("token") == token:
            return u
    return None


def get_bearer_token():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


# ----------------------------------------------------------------------------
# Admin panel — login talab qiluvchi dekorator
# ----------------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect("/")
        return f(*args, **kwargs)
    return wrapper


# ----------------------------------------------------------------------------
# HTML shablonlar (admin panel) — bitta faylda saqlash uchun satr sifatida
# ----------------------------------------------------------------------------
BASE_STYLE = """
<style>
  :root{
    --violet:#6d28d9; --blue:#2563eb; --magenta:#db2777; --pink:#ec4899;
    --ink:#12111a; --paper:#f7f5f2; --line:#e7e3db; --muted:#6b6b76;
    --grad: linear-gradient(120deg, var(--blue), var(--violet), var(--magenta), var(--pink));
  }
  *{box-sizing:border-box;}
  body{margin:0;font-family:'Segoe UI',Inter,Arial,sans-serif;background:var(--paper);color:var(--ink);}
  a{color:inherit;}
  .topbar{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;
          background:var(--ink);color:#fff;}
  .brand{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.02em;}
  .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--grad);}
  .topbar a.logout{font-size:13px;color:#fff;opacity:.8;text-decoration:none;border:1px solid rgba(255,255,255,.25);
          padding:6px 12px;border-radius:999px;}
  .wrap{max-width:980px;margin:0 auto;padding:28px 20px 60px;}
  .card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px;margin-bottom:22px;}
  .card h2{margin:0 0 4px;font-size:17px;}
  .card p.hint{margin:0 0 16px;color:var(--muted);font-size:13px;}
  label{display:block;font-size:13px;font-weight:600;margin:12px 0 6px;}
  input[type=text],input[type=password],input[type=url],textarea,select{
    width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font-size:14px;font-family:inherit;
  }
  textarea{min-height:70px;resize:vertical;}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
  @media(max-width:640px){.grid2{grid-template-columns:1fr;}}
  .btn{display:inline-flex;align-items:center;gap:8px;border:none;border-radius:999px;padding:10px 18px;
       font-weight:700;font-size:14px;cursor:pointer;margin-top:14px;}
  .btn.primary{background:var(--ink);color:#fff;}
  .btn.gradient{background:var(--grad);color:#fff;}
  .btn.danger{background:#fff;color:#c81e4a;border:1px solid #f0c6d3;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top;}
  th{color:var(--muted);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
  .empty{color:var(--muted);font-size:13px;padding:10px 0;}
  .banner-item{display:flex;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;
               padding:10px;margin-bottom:10px;}
  .banner-item img{width:74px;height:44px;object-fit:cover;border-radius:8px;background:#eee;}
  .banner-item .meta{flex:1;font-size:13px;}
  .banner-item .meta .u{color:var(--muted);font-size:12px;word-break:break-all;}
  .msg{padding:10px 14px;border-radius:10px;font-size:13px;margin-bottom:16px;}
  .msg.ok{background:#e7f7ee;color:#127a3d;border:1px solid #bfe9d0;}
  .msg.err{background:#fdecec;color:#b3261e;border:1px solid #f6c6c3;}
  .tabs{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap;}
  .tabs a{padding:8px 14px;border-radius:999px;background:#fff;border:1px solid var(--line);
          font-size:13px;font-weight:600;text-decoration:none;color:var(--ink);}
  .tabs a.active{background:var(--ink);color:#fff;border-color:var(--ink);}
  .login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--ink);}
  .login-card{background:#fff;border-radius:18px;padding:34px;width:100%;max-width:360px;}
  .login-card .mark{width:44px;height:44px;border-radius:12px;background:var(--grad);margin-bottom:14px;}
  .login-card h1{font-size:20px;margin:0 0 4px;}
  .login-card p{color:var(--muted);font-size:13px;margin:0 0 18px;}
  small.foot{display:block;margin-top:26px;color:var(--muted);font-size:12px;text-align:center;}
</style>
"""

LOGIN_HTML = """
<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UZUNITED — Admin panel</title>""" + BASE_STYLE + """
</head><body>
<div class="login-wrap">
  <div class="login-card">
    <div class="mark"></div>
    <h1>UZUNITED admin panel</h1>
    <p>Davom etish uchun tizimga kiring</p>
    {% if error %}<div class="msg err">{{ error }}</div>{% endif %}
    <form method="post" action="/login">
      <label>Login</label>
      <input type="text" name="username" required autofocus>
      <label>Parol</label>
      <input type="password" name="password" required>
      <button class="btn gradient" type="submit" style="width:100%;justify-content:center;">Kirish</button>
    </form>
  </div>
</div>
</body></html>
"""

DASHBOARD_HTML = """
<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UZUNITED — Admin panel</title>""" + BASE_STYLE + """
</head><body>
<div class="topbar">
  <div class="brand"><span class="mark"></span> UZUNITED admin</div>
  <a class="logout" href="/logout">Chiqish</a>
</div>
<div class="wrap">
  {% if msg %}<div class="msg ok">{{ msg }}</div>{% endif %}
  {% if err %}<div class="msg err">{{ err }}</div>{% endif %}

  <div class="tabs">
    <a href="/#sozlamalar" class="active">Sozlamalar</a>
    <a href="/#banerlar" class="active">Banerlar</a>
    <a href="/#buyurtmalar" class="active">Buyurtmalar ({{ orders|length }})</a>
    <a href="/#foydalanuvchilar" class="active">Foydalanuvchilar ({{ users|length }})</a>
  </div>

  <div class="card" id="sozlamalar">
    <h2>AI sozlamalari</h2>
    <p class="hint">UZUNITED AI chat shu API kalit orqali ishlaydi (Groq, OpenAI-mos API). Kalitni to'liq holda kiriting.</p>
    <form method="post" action="/dashboard/settings">
      <label>AI API kaliti (masalan gsk_...)</label>
      <input type="text" name="ai_api_key" value="{{ ai_api_key }}" placeholder="gsk_...">
      <div class="grid2">
        <div>
          <label>AI model nomi</label>
          <input type="text" name="ai_model" value="{{ ai_model }}">
        </div>
      </div>

      <h2 style="margin-top:26px;">Telegram sozlamalari</h2>
      <p class="hint">Yangi buyurtma tushganda shu bot orqali quyidagi ID'larga xabar yuboriladi. Kamida 4 ta ID uchun joy bor.</p>
      <label>Bot tokeni</label>
      <input type="text" name="telegram_bot_token" value="{{ telegram_bot_token }}">
      <div class="grid2">
        {% for cid in chat_ids %}
        <div>
          <label>Telegram ID {{ loop.index }}</label>
          <input type="text" name="chat_id_{{ loop.index0 }}" value="{{ cid }}" placeholder="masalan 123456789">
        </div>
        {% endfor %}
      </div>
      <button class="btn primary" type="submit">Sozlamalarni saqlash</button>
    </form>
  </div>

  <div class="card" id="banerlar">
    <h2>Banerlar (reklama)</h2>
    <p class="hint">Sayt pastidagi reklama bo'limida ko'rinadigan rasm va havola (masalan UzMyShop).</p>
    {% if banners|length == 0 %}<p class="empty">Hozircha baner qo'shilmagan.</p>{% endif %}
    {% for b in banners %}
    <div class="banner-item">
      {% if b.image_url %}<img src="{{ b.image_url }}">{% else %}<div style="width:74px;height:44px;background:#eee;border-radius:8px;"></div>{% endif %}
      <div class="meta">
        <div><strong>{{ b.title or "Baner" }}</strong></div>
        <div class="u">{{ b.link_url }}</div>
      </div>
      <form method="post" action="/dashboard/banners/delete/{{ b.id }}" onsubmit="return confirm('Ushbu baner o\\'chirilsinmi?');">
        <button class="btn danger" type="submit">O'chirish</button>
      </form>
    </div>
    {% endfor %}

    <form method="post" action="/dashboard/banners/add" enctype="multipart/form-data" style="margin-top:16px;">
      <label>Sarlavha</label>
      <input type="text" name="title" placeholder="UzMyShop">
      <label>Havola (URL) — tugma bosilganda shu manzilga o'tadi</label>
      <input type="url" name="link_url" placeholder="https://uzmyshop.uz/app" required>
      <label>Qisqa tavsif</label>
      <textarea name="description" placeholder="Masalan: Onlayn xaridlar uchun UzMyShop ilovasi"></textarea>
      <label>Rasm</label>
      <input type="file" name="image" accept="image/*" required>
      <button class="btn gradient" type="submit">Baner qo'shish</button>
    </form>
  </div>

  <div class="card" id="buyurtmalar">
    <h2>Buyurtmalar</h2>
    {% if orders|length == 0 %}
      <p class="empty">Hozircha buyurtma yo'q.</p>
    {% else %}
    <table>
      <tr><th>№</th><th>Sana</th><th>Xizmat</th><th>Ism</th><th>Aloqa</th><th>Xabar</th></tr>
      {% for o in orders|reverse %}
      <tr>
        <td>{{ o.id }}</td><td>{{ o.created_at }}</td><td>{{ o.service }}</td>
        <td>{{ o.name }}</td><td>{{ o.contact }}</td><td>{{ o.message }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}
  </div>

  <div class="card" id="foydalanuvchilar">
    <h2>Ro'yxatdan o'tgan foydalanuvchilar</h2>
    {% if users|length == 0 %}
      <p class="empty">Hozircha foydalanuvchi yo'q.</p>
    {% else %}
    <table>
      <tr><th>№</th><th>Ism</th><th>Telefon</th><th>Ro'yxatdan o'tgan sana</th></tr>
      {% for u in users|reverse %}
      <tr><td>{{ u.id }}</td><td>{{ u.name }}</td><td>{{ u.phone }}</td><td>{{ u.created_at }}</td></tr>
      {% endfor %}
    </table>
    {% endif %}
  </div>

  <small class="foot">UZUNITED &copy; {{ year }} — admin panel</small>
</div>
</body></html>
"""


# ----------------------------------------------------------------------------
# Admin panel yo'llari
# ----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
@app.route("/admin", methods=["GET"])
def admin_root():
    if session.get("is_admin"):
        return dashboard()
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/login", methods=["POST"])
def login():
    cfg = load_config()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    admin_cfg = cfg["admin"]
    if username == admin_cfg["username"] and check_password_hash(admin_cfg["password_hash"], password):
        session["is_admin"] = True
        return redirect("/")
    return render_template_string(LOGIN_HTML, error="Login yoki parol noto'g'ri.")


@app.route("/logout", methods=["GET"])
def logout():
    session.clear()
    return redirect("/")


@app.route("/dashboard", methods=["GET"])
@admin_required
def dashboard():
    cfg = load_config()
    orders = load_orders()["orders"]
    users = load_users()["users"]
    chat_ids = list(cfg["telegram"].get("chat_ids", []))
    while len(chat_ids) < 4:
        chat_ids.append("")
    return render_template_string(
        DASHBOARD_HTML,
        msg=request.args.get("msg"),
        err=request.args.get("err"),
        ai_api_key=cfg["ai"].get("api_key", ""),
        ai_model=cfg["ai"].get("model", ""),
        telegram_bot_token=cfg["telegram"].get("bot_token", ""),
        chat_ids=chat_ids,
        banners=cfg.get("banners", []),
        orders=orders,
        users=users,
        year=datetime.datetime.now().year,
    )


@app.route("/dashboard/settings", methods=["POST"])
@admin_required
def update_settings():
    cfg = load_config()
    cfg["ai"]["api_key"] = (request.form.get("ai_api_key") or "").strip()
    cfg["ai"]["model"] = (request.form.get("ai_model") or cfg["ai"]["model"]).strip()
    cfg["telegram"]["bot_token"] = (request.form.get("telegram_bot_token") or "").strip()

    chat_ids = []
    i = 0
    while f"chat_id_{i}" in request.form:
        val = (request.form.get(f"chat_id_{i}") or "").strip()
        chat_ids.append(val)
        i += 1
    while len(chat_ids) < 4:
        chat_ids.append("")
    cfg["telegram"]["chat_ids"] = chat_ids

    save_config(cfg)
    return redirect("/dashboard?msg=Sozlamalar+saqlandi")


@app.route("/dashboard/banners/add", methods=["POST"])
@admin_required
def add_banner():
    cfg = load_config()
    title = (request.form.get("title") or "").strip()
    link_url = (request.form.get("link_url") or "").strip()
    description = (request.form.get("description") or "").strip()
    file = request.files.get("image")

    if not link_url or not file or file.filename == "":
        return redirect("/dashboard?err=Rasm+va+havola+majburiy")
    if not allowed_file(file.filename):
        return redirect("/dashboard?err=Ruxsat+etilmagan+rasm+formati")

    ext = file.filename.rsplit(".", 1)[1].lower()
    fname = f"banner_{secrets.token_hex(8)}.{ext}"
    fname = secure_filename(fname)
    file.save(os.path.join(UPLOAD_DIR, fname))

    banners = cfg.get("banners", [])
    next_id = (max([b["id"] for b in banners], default=0)) + 1
    banners.append({
        "id": next_id,
        "title": title,
        "description": description,
        "link_url": link_url,
        "image_url": f"/uploads/{fname}",
    })
    cfg["banners"] = banners
    save_config(cfg)
    return redirect("/dashboard?msg=Baner+qo'shildi")


@app.route("/dashboard/banners/delete/<int:banner_id>", methods=["POST"])
@admin_required
def delete_banner(banner_id):
    cfg = load_config()
    banners = cfg.get("banners", [])
    target = next((b for b in banners if b["id"] == banner_id), None)
    if target:
        img_path = os.path.join(BASE_DIR, target["image_url"].lstrip("/"))
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError:
                pass
    cfg["banners"] = [b for b in banners if b["id"] != banner_id]
    save_config(cfg)
    return redirect("/dashboard?msg=Baner+o'chirildi")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ----------------------------------------------------------------------------
# Ochiq (public) API — frontend shu yerlarga so'rov yuboradi
# ----------------------------------------------------------------------------
@app.route("/api/banners", methods=["GET"])
def api_banners():
    cfg = load_config()
    return jsonify({"ok": True, "banners": cfg.get("banners", [])})


@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    service = (data.get("service") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not contact or not service:
        return jsonify({"ok": False, "error": "Ism, aloqa va xizmat turi majburiy."}), 400

    user = find_user_by_token(get_bearer_token())

    orders_data = load_orders()
    order_id = orders_data["next_id"]
    order = {
        "id": order_id,
        "user_id": user["id"] if user else None,
        "name": name,
        "contact": contact,
        "service": service,
        "message": message,
        "status": "yangi",
        "created_at": now_iso(),
    }
    orders_data["orders"].append(order)
    orders_data["next_id"] = order_id + 1
    save_orders(orders_data)

    text = (
        f"🆕 <b>Yangi buyurtma — UZUNITED</b>\n"
        f"Xizmat: {service}\n"
        f"Ism: {name}\n"
        f"Aloqa: {contact}\n"
        f"Xabar: {message or '—'}\n"
        f"Vaqt: {order['created_at']}"
    )
    send_telegram_message(text)

    return jsonify({"ok": True, "order_id": order_id})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"ok": False, "reply": "Savolingizni yozing."}), 400
    ok, reply = ask_ai(message, history)
    return jsonify({"ok": ok, "reply": reply})


@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""

    if not name or not phone or len(password) < 4:
        return jsonify({"ok": False, "error": "Ism, telefon va kamida 4 belgili parol kerak."}), 400

    users_data = load_users()
    if any(u["phone"] == phone for u in users_data["users"]):
        return jsonify({"ok": False, "error": "Bu telefon raqami bilan foydalanuvchi allaqachon mavjud."}), 409

    uid = users_data["next_id"]
    token = secrets.token_hex(24)
    users_data["users"].append({
        "id": uid,
        "name": name,
        "phone": phone,
        "password_hash": generate_password_hash(password),
        "token": token,
        "created_at": now_iso(),
    })
    users_data["next_id"] = uid + 1
    save_users(users_data)

    return jsonify({"ok": True, "token": token, "name": name})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""

    users_data = load_users()
    user = next((u for u in users_data["users"] if u["phone"] == phone), None)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "error": "Telefon yoki parol noto'g'ri."}), 401

    new_token = secrets.token_hex(24)
    user["token"] = new_token
    save_users(users_data)

    return jsonify({"ok": True, "token": new_token, "name": user["name"]})


@app.route("/api/my-orders", methods=["GET"])
def api_my_orders():
    user = find_user_by_token(get_bearer_token())
    if not user:
        return jsonify({"ok": False, "error": "Avtorizatsiyadan o'tilmagan."}), 401
    orders = load_orders()["orders"]
    mine = [o for o in orders if o.get("user_id") == user["id"]]
    return jsonify({"ok": True, "orders": mine})


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Topilmadi"}), 404
    return redirect("/")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"UZUNITED backend ishga tushdi: http://localhost:{port}  (admin: /  yoki /admin)")
    app.run(host="0.0.0.0", port=port, debug=False)
