# -*- coding: utf-8 -*-
"""
UZUNITED — Vercel uchun Flask backend
======================================

MUHIM ARXITEKTURA IZOHI:
- Bu fayl Vercel tomonidan avtomatik aniqlanadigan Python "entrypoint".
  Vercel shunchaki quyidagi `app` obyektini (Flask WSGI ilovasi) qidiradi
  va uni serverless funksiya sifatida ishga tushiradi. Alohida
  "handler" klassi yozish SHART EMAS — `app = Flask(__name__)` ning o'zi
  WSGI handler vazifasini bajaradi va Vercel buni avtomatik his qiladi.
- Vercel fayl tizimi VAQTINCHALIK: har bir so'rov potentsial ravishda
  yangi konteynerda ishlashi mumkin va diskka yozilgan narsalar
  saqlanib qolishiga KAFOLAT YO'Q (faqat /tmp yoziladi, lekin u ham
  doimiy emas). Shuning uchun SQLite fayl-baza ISHLATILMAYDI.
  Buning o'rniga Upstash Redis (Vercel KV ham aynan shu texnologiyaga
  asoslangan) REST API orqali ishlatiladi — bu haqiqiy, doimiy,
  serverless-ga mos ma'lumot bazasi.
- Fayllar (logotip, banner rasmlari, yuklab olinadigan fayllar) ham
  diskka emas, balki Cloudinary'ga yuklanadi va faqat URL manzili
  Redis'da saqlanadi.

Muhit o'zgaruvchilari (.env.example faylida namunasi bor):
  ADMIN_USERNAME          - admin login
  ADMIN_PASSWORD_HASH     - admin parolining hash'i (pastdagi izohga qarang)
  JWT_SECRET              - JWT token imzolash uchun maxfiy kalit
  UPSTASH_REDIS_REST_URL  - Upstash Redis REST manzili
  UPSTASH_REDIS_REST_TOKEN- Upstash Redis REST tokeni
  CLOUDINARY_CLOUD_NAME   - Cloudinary hisob nomi
  CLOUDINARY_API_KEY      - Cloudinary API kaliti
  CLOUDINARY_API_SECRET   - Cloudinary API maxfiy kaliti
  GROQ_API_KEY            - AI chat uchun Groq API kaliti (HECH QACHON
                            frontendga yozilmaydi, faqat shu yerda,
                            server tomonida ishlatiladi!)

ADMIN_PASSWORD_HASH qanday yaratiladi (terminalda bir marta ishga tushiring):
  python -c "from werkzeug.security import generate_password_hash as h; print(h('MenimKuchliParolim123'))"
Chiqadigan qatorni ADMIN_PASSWORD_HASH environment variable'ga qo'ying.
Hech qachon ochiq (plain-text) parolni environment variable sifatida saqlamang.
"""

import os
import json
import uuid
import datetime
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import check_password_hash
import jwt
import requests

# ----------------------------------------------------------------------------
# 1) ILOVANI YARATISH
# ----------------------------------------------------------------------------
app = Flask(__name__)
# Ishlab chiqish (local) bosqichida frontend boshqa portda/domenda bo'lishi
# mumkinligi uchun CORS yoqilgan. Production'da (Vercel) frontend va backend
# BIR XIL domenda bo'lgani uchun bu odatda kerak emas, lekin zarar ham
# qilmaydi. Xohlasangiz, `origins` ro'yxatini o'z domeningizga toraytiring.
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ----------------------------------------------------------------------------
# 2) MUHIT O'ZGARUVCHILARI
# ----------------------------------------------------------------------------
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")

UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

# DIQQAT: kalit shu yerda ENV orqali o'qiladi, kod ichiga yozilmaydi.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ----------------------------------------------------------------------------
# 3) MA'LUMOT SAQLASH QATLAMI (Upstash Redis REST orqali)
# ----------------------------------------------------------------------------
# SQLite'DAN NIMA UCHUN VOZ KECHILDI?
#   Vercel'da har bir so'rov alohida, vaqtinchalik konteynerda bajarilishi
#   mumkin. Agar SQLite fayliga yozsangiz, keyingi so'rov butunlay boshqa
#   konteynerda ishga tushishi va o'sha faylni "ko'rmasligi" mumkin —
#   ya'ni ma'lumotlar yo'qoladi yoki ziddiyat (race condition) yuzaga keladi.
#   Shuning uchun tashqi, doimiy xizmat (Upstash Redis / Vercel KV) kerak.
#
# Quyida `upstash-redis` python kutubxonasi ishlatiladi. Agar u sozlanmagan
# bo'lsa (masalan, siz hali Upstash hisobini ulamagan bo'lsangiz), backend
# /tmp ichidagi vaqtinchalik JSON faylga tushib qoladi — LEKIN BU FAQAT
# LOCAL SINOV UCHUN! Productionda (haqiqiy Vercel deploy'da) bu fallback
# ISHONCHLI EMAS, chunki /tmp konteynerlar orasida umumiy emas va har doim
# tozalanishi mumkin. Productionga chiqishdan oldin Upstash/Vercel KV'ni
# albatta ulang.
try:
    from upstash_redis import Redis as _UpstashRedis
    _redis_client = None
    if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
        _redis_client = _UpstashRedis(
            url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN
        )
except Exception:
    _redis_client = None

_LOCAL_FALLBACK_PATH = "/tmp/uzunited_local_db.json"


def _local_fallback_read():
    """FAQAT LOCAL SINOV UCHUN fallback o'quvchi. Productionda ishonmang."""
    if not os.path.exists(_LOCAL_FALLBACK_PATH):
        return {}
    try:
        with open(_LOCAL_FALLBACK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _local_fallback_write(data):
    try:
        with open(_LOCAL_FALLBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def kv_get_json(key, default):
    """Redis'dan JSON qiymatni o'qiydi. Topilmasa `default` qaytadi."""
    if _redis_client is not None:
        raw = _redis_client.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default
    # --- local fallback (faqat dev uchun) ---
    data = _local_fallback_read()
    return data.get(key, default)


def kv_set_json(key, value):
    """JSON qiymatni Redis'ga yozadi."""
    if _redis_client is not None:
        _redis_client.set(key, json.dumps(value, ensure_ascii=False))
        return
    # --- local fallback (faqat dev uchun) ---
    data = _local_fallback_read()
    data[key] = value
    _local_fallback_write(data)


def storage_mode():
    return "upstash-redis" if _redis_client is not None else "local-json-fallback(DEV ONLY)"


# ----------------------------------------------------------------------------
# 4) FAYL/RASM YUKLASH QATLAMI (Cloudinary)
# ----------------------------------------------------------------------------
# Nima uchun Cloudinary?
#   - Rasmiy Python SDK bor, sozlash oson, Flask bilan yaxshi ishlaydi.
#   - Bepul reja mavjud, kichik-o'rtacha loyihalar uchun yetarli.
#   - Muqobillar: Vercel Blob (asosan JS/Node SDK'ga mo'ljallangan,
#     Python uchun rasmiy SDK yo'q) yoki Amazon S3 (boto3 orqali,
#     ko'proq sozlash talab qiladi, lekin katta loyihalar uchun mos).
#     Agar S3'ni afzal ko'rsangiz, quyidagi upload_file() funksiyasini
#     boto3.client("s3").upload_fileobj(...) bilan almashtirishingiz kifoya.
_cloudinary_ready = False
try:
    import cloudinary
    import cloudinary.uploader

    if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
        _cloudinary_ready = True
except Exception:
    _cloudinary_ready = False


def upload_file_to_storage(file_storage, folder="uzunited"):
    """
    Flask `request.files['...']` orqali kelgan faylni Cloudinary'ga yuklaydi
    va (url, xato) juftligini qaytaradi.
    """
    if not _cloudinary_ready:
        return None, (
            "Fayl xotirasi sozlanmagan. Vercel muhit o'zgaruvchilarida "
            "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET "
            "qiymatlarini to'ldiring."
        )
    try:
        result = cloudinary.uploader.upload(file_storage, folder=folder)
        return result.get("secure_url"), None
    except Exception as exc:
        return None, "Faylni yuklashda xatolik: " + str(exc)


# ----------------------------------------------------------------------------
# 5) JWT — ADMIN AUTENTIFIKATSIYA
# ----------------------------------------------------------------------------
def create_admin_token():
    payload = {
        "role": "admin",
        "username": ADMIN_USERNAME,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=12),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def admin_required(view_func):
    """Admin endpointlarini `Authorization: Bearer <token>` bilan himoya qiladi."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not JWT_SECRET:
            return jsonify(ok=False, error="Server JWT_SECRET sozlanmagan."), 500
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify(ok=False, error="Token topilmadi."), 401
        token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            if payload.get("role") != "admin":
                raise jwt.InvalidTokenError()
        except jwt.ExpiredSignatureError:
            return jsonify(ok=False, error="Token muddati tugagan, qayta kiring."), 401
        except Exception:
            return jsonify(ok=False, error="Token yaroqsiz."), 401
        return view_func(*args, **kwargs)

    return wrapper


# ----------------------------------------------------------------------------
# 6) YORDAMCHI FUNKSIYALAR
# ----------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "companyName": "UZUNITED",
    "description": "UZUNITED — mobil ilova, veb-sayt, backend tizim va Telegram bot ishlab chiqish bo'yicha IT-kompaniya.",
    "footerText": "UZUNITED — g'oyangizni raqamli mahsulotga aylantiramiz.",
    "heroUrl": "",
}


def new_id():
    return uuid.uuid4().hex[:10]


def now_iso():
    return datetime.datetime.utcnow().isoformat()


# ----------------------------------------------------------------------------
# 7) OCHIQ (PUBLIC) ENDPOINTLAR — sayt tomonidan avtomatik chaqiriladi
# ----------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(ok=True, storage=storage_mode(), cloudinary=_cloudinary_ready)


@app.route("/api/site-settings", methods=["GET"])
def get_site_settings():
    settings = kv_get_json("settings", DEFAULT_SETTINGS)
    return jsonify(ok=True, settings=settings)


@app.route("/api/site-settings", methods=["PUT"])
@admin_required
def update_site_settings():
    body = request.get_json(silent=True) or {}
    current = kv_get_json("settings", DEFAULT_SETTINGS)
    current.update({
        "companyName": body.get("companyName", current.get("companyName")),
        "description": body.get("description", current.get("description")),
        "footerText": body.get("footerText", current.get("footerText")),
        "heroUrl": body.get("heroUrl", current.get("heroUrl")),
    })
    kv_set_json("settings", current)
    return jsonify(ok=True, settings=current)


@app.route("/api/logo", methods=["GET"])
def get_logo():
    logo = kv_get_json("logo", {"url": ""})
    return jsonify(ok=True, logo=logo)


@app.route("/api/logo", methods=["POST"])
@admin_required
def upload_logo():
    if "file" not in request.files:
        return jsonify(ok=False, error="Rasm fayli yuborilmadi (field nomi: file)."), 400
    url, err = upload_file_to_storage(request.files["file"], folder="uzunited/logo")
    if err:
        return jsonify(ok=False, error=err), 500
    kv_set_json("logo", {"url": url})
    return jsonify(ok=True, logo={"url": url})


@app.route("/api/banners", methods=["GET"])
def get_banners():
    banners = kv_get_json("banners", [])
    return jsonify(ok=True, banners=banners)


@app.route("/api/banners", methods=["POST"])
@admin_required
def add_banner():
    title = request.form.get("title") or (request.get_json(silent=True) or {}).get("title", "")
    description = request.form.get("description") or (request.get_json(silent=True) or {}).get("description", "")
    link_url = request.form.get("link_url") or (request.get_json(silent=True) or {}).get("link_url", "#")

    image_url = None
    if "file" in request.files and request.files["file"].filename:
        image_url, err = upload_file_to_storage(request.files["file"], folder="uzunited/banners")
        if err:
            return jsonify(ok=False, error=err), 500

    banners = kv_get_json("banners", [])
    banner = {
        "id": new_id(),
        "title": title or "Reklama",
        "description": description or "",
        "image_url": image_url,
        "link_url": link_url or "#",
        "created_at": now_iso(),
    }
    banners.append(banner)
    kv_set_json("banners", banners)
    return jsonify(ok=True, banner=banner)


@app.route("/api/banners/<banner_id>", methods=["DELETE"])
@admin_required
def delete_banner(banner_id):
    banners = kv_get_json("banners", [])
    new_banners = [b for b in banners if b.get("id") != banner_id]
    kv_set_json("banners", new_banners)
    return jsonify(ok=True, deleted=len(banners) != len(new_banners))


@app.route("/api/files", methods=["GET"])
def get_files():
    files = kv_get_json("files", [])
    return jsonify(ok=True, files=files)


@app.route("/api/files", methods=["POST"])
@admin_required
def add_file():
    name = request.form.get("name") or (request.get_json(silent=True) or {}).get("name", "")
    description = request.form.get("description") or (request.get_json(silent=True) or {}).get("description", "")
    manual_url = request.form.get("url") or (request.get_json(silent=True) or {}).get("url", "")

    file_url = manual_url
    if "file" in request.files and request.files["file"].filename:
        file_url, err = upload_file_to_storage(request.files["file"], folder="uzunited/files")
        if err:
            return jsonify(ok=False, error=err), 500

    if not name or not file_url:
        return jsonify(ok=False, error="Fayl nomi va manzili (yoki fayl o'zi) kerak."), 400

    files = kv_get_json("files", [])
    entry = {
        "id": new_id(),
        "name": name,
        "description": description or "",
        "url": file_url,
        "created_at": now_iso(),
    }
    files.append(entry)
    kv_set_json("files", files)
    return jsonify(ok=True, file=entry)


@app.route("/api/files/<file_id>", methods=["DELETE"])
@admin_required
def delete_file(file_id):
    files = kv_get_json("files", [])
    new_files = [f for f in files if f.get("id") != file_id]
    kv_set_json("files", new_files)
    return jsonify(ok=True, deleted=len(files) != len(new_files))


@app.route("/api/services", methods=["GET"])
def get_services():
    services = kv_get_json("services", [])
    return jsonify(ok=True, services=services)


@app.route("/api/services", methods=["POST"])
@admin_required
def add_service():
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    description = body.get("description", "").strip()
    if not title:
        return jsonify(ok=False, error="Xizmat nomi kerak."), 400
    services = kv_get_json("services", [])
    service = {"id": new_id(), "title": title, "description": description}
    services.append(service)
    kv_set_json("services", services)
    return jsonify(ok=True, service=service)


@app.route("/api/services/<service_id>", methods=["DELETE"])
@admin_required
def delete_service(service_id):
    services = kv_get_json("services", [])
    new_services = [s for s in services if s.get("id") != service_id]
    kv_set_json("services", new_services)
    return jsonify(ok=True, deleted=len(services) != len(new_services))


@app.route("/api/order", methods=["POST"])
def create_order():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    contact = (body.get("contact") or "").strip()
    service = (body.get("service") or "").strip()
    message = (body.get("message") or "").strip()

    if not name or not contact:
        return jsonify(ok=False, error="Ism va aloqa ma'lumoti kerak."), 400

    orders = kv_get_json("orders", [])
    order = {
        "id": new_id(),
        "name": name,
        "contact": contact,
        "service": service,
        "message": message,
        "status": "yangi",
        "created_at": now_iso(),
    }
    orders.append(order)
    kv_set_json("orders", orders)
    return jsonify(ok=True, order=order)


@app.route("/api/orders", methods=["GET"])
@admin_required
def list_orders():
    orders = kv_get_json("orders", [])
    return jsonify(ok=True, orders=list(reversed(orders)))


@app.route("/api/orders/<order_id>", methods=["PUT"])
@admin_required
def update_order(order_id):
    body = request.get_json(silent=True) or {}
    new_status = body.get("status")
    orders = kv_get_json("orders", [])
    found = False
    for o in orders:
        if o.get("id") == order_id:
            o["status"] = new_status or o.get("status")
            found = True
            break
    kv_set_json("orders", orders)
    return jsonify(ok=True, updated=found)


# ----------------------------------------------------------------------------
# 8) ADMIN LOGIN
# ----------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def admin_login():
    """
    Admin panel uchun login. { "username": "...", "password": "..." }
    qabul qiladi va muvaffaqiyatli bo'lsa JWT token qaytaradi.
    """
    if not ADMIN_PASSWORD_HASH or not JWT_SECRET:
        return jsonify(
            ok=False,
            error="Server sozlanmagan: ADMIN_PASSWORD_HASH yoki JWT_SECRET yo'q.",
        ), 500

    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if username != ADMIN_USERNAME or not check_password_hash(ADMIN_PASSWORD_HASH, password):
        return jsonify(ok=False, error="Login yoki parol noto'g'ri."), 401

    token = create_admin_token()
    return jsonify(ok=True, token=token, name=ADMIN_USERNAME)


@app.route("/api/admin/me", methods=["GET"])
@admin_required
def admin_me():
    return jsonify(ok=True, username=ADMIN_USERNAME)


# ----------------------------------------------------------------------------
# 9) AI CHAT — Groq proxy (kalit faqat serverda, brauzerga chiqmaydi!)
# ----------------------------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def ai_chat():
    if not GROQ_API_KEY:
        return jsonify(
            ok=False,
            error="AI chat sozlanmagan: GROQ_API_KEY environment variable yo'q.",
        ), 500

    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []  # [{role, content}, ...]

    if not user_message:
        return jsonify(ok=False, error="Xabar bo'sh bo'lishi mumkin emas."), 400
    if len(user_message) > 2000:
        return jsonify(ok=False, error="Xabar juda uzun."), 400

    # Faqat oxirgi bir nechta xabarni yuboramiz — token sarfini nazorat qilish uchun
    trimmed_history = history[-10:] if isinstance(history, list) else []

    messages = [
        {
            "role": "system",
            "content": (
                "Siz UZUNITED IT-kompaniyasining sun'iy intellekt yordamchisisiz. "
                "Mobil ilova, veb-sayt, backend tizim va Telegram bot ishlab chiqish "
                "xizmatlari haqida qisqa, aniq va o'zbek tilida javob bering."
            ),
        }
    ] + trimmed_history + [{"role": "user", "content": user_message}]

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + GROQ_API_KEY,
            },
            json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.6},
            timeout=25,
        )
        data = resp.json()
    except Exception as exc:
        return jsonify(ok=False, error="AI xizmatiga ulanib bo'lmadi: " + str(exc)), 502

    if "error" in data:
        return jsonify(ok=False, error=data["error"].get("message", "AI xatosi")), 502

    reply = ""
    choices = data.get("choices") or []
    if choices and choices[0].get("message"):
        reply = choices[0]["message"].get("content", "")

    return jsonify(ok=True, reply=reply or "Kechirasiz, javob topilmadi.")


# ----------------------------------------------------------------------------
# 10) 404 — /api/... ostidagi noma'lum manzillar uchun toza JSON javob
# ----------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_e):
    return jsonify(ok=False, error="Endpoint topilmadi."), 404


# Local ishga tushirish uchun: `python api/index.py`
# (Vercel'da bu blok ishlamaydi, chunki Vercel `app` obyektini to'g'ridan-to'g'ri import qiladi.)
if __name__ == "__main__":
    app.run(debug=True, port=5000)
