"""
لقطة — منصة تجمع عروض وتخفيضات محلات وبيجات ومطاعم الموصل.

التشغيل محلياً:
    pip install -r requirements.txt
    set ADMIN_PASSWORD=كلمة-سر-قوية        (على لينكس/ماك: export)
    python main.py
ثم افتح http://localhost:8000

متغيرات البيئة:
    ADMIN_PASSWORD  كلمة مرور لوحة الإدارة (إن لم تُحدَّد تُولَّد واحدة عشوائية وتُطبع في الطرفية)
    SECRET_KEY      مفتاح توقيع جلسة الأدمن (اختياري — إن لم يُحدَّد يُشتق من كلمة المرور)
    ZAINCASH_NUMBER رقم زين كاش الذي يظهر في رسالة الإحصائيات الجاهزة
    CONTACT_WHATSAPP رقم واتساب عام يظهر للزوار في التذييل وصفحة المحلات (اختياري)
    DATABASE_URL    افتراضياً SQLite، ويكفي وضع رابط PostgreSQL للترقية
    DATA_DIR        مجلد قاعدة البيانات والصور (اربطه بقرص دائم على Render/Railway)
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import DictLoader, Environment, select_autoescape
from PIL import Image, ImageOps
from sqlalchemy import (Boolean, Date, DateTime, Integer, String, create_engine, delete, func,
                        inspect, or_, select, text, update)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

# ───────────────────────────── الإعدادات ─────────────────────────────

SITE_NAME = "لقطة"

CITYWIDE = "توصيل لكل الموصل"
AREAS = [
    "المجموعة الثقافية",
    "الزهور",
    "الكرامة",
    "الدواسة",
    "العربي",
    "الجانب الأيمن",
    CITYWIDE,
]
CATEGORIES = [
    "مطاعم وكافيهات",
    "ملابس وأحذية",
    "إلكترونيات",
    "تجميل وعناية",
    "أثاث ومنزلية",
    "ذهب وإكسسوارات",
]
CATEGORY_ICONS = dict(zip(CATEGORIES, ["🍔", "👗", "📱", "💄", "🛋️", "💍"]))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'deals.db').as_posix()}"
if DATABASE_URL.startswith("postgres://"):  # صيغة Render/Railway القديمة
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(9)
    print(f"\n[!] ADMIN_PASSWORD غير محدد. كلمة مرور مؤقتة لهذه الجلسة: {ADMIN_PASSWORD}\n")

# بدون SECRET_KEY يُشتق المفتاح من كلمة المرور، فتبقى جلسة الأدمن صالحة بعد إعادة تشغيل الخادم.
SECRET_KEY = (os.getenv("SECRET_KEY") or "").encode() or hashlib.sha256(
    b"mosul-deals:" + ADMIN_PASSWORD.encode()).digest()
# كلمة المرور جزء من مفتاح التوقيع: تغييرها يُبطل كل الجلسات القديمة تلقائياً.
SESSION_KEY = hmac.new(SECRET_KEY, ADMIN_PASSWORD.encode(), hashlib.sha256).digest()

SESSION_COOKIE = "admin_session"
SESSION_DAYS = 7
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB قبل الضغط
MAX_IMAGE_SIDE = 1200
MAX_IMAGE_PIXELS = 50_000_000  # يمنع صوراً صغيرة الحجم هائلة الأبعاد من استنزاف الذاكرة
MAX_BODY_BYTES = MAX_IMAGE_BYTES + 1024 * 1024
PAGE_SIZE = 40
CLICKED_COOKIE = "clk"  # العروض التي ضغطها الزائر مؤخراً، حتى لا تُحسب النقرة المكررة

# الباقات والهدف الشهري
BASIC_PRICE = 6_000
VIP_PRICE = 15_000
MONTHLY_GOAL = 1_000_000
SUBSCRIPTION_DAYS = 30
EXPIRY_WARNING_DAYS = 5
ZAINCASH_NUMBER = os.getenv("ZAINCASH_NUMBER", "")  # يظهر في رسالة الإحصائيات الجاهزة
CONTACT_WHATSAPP = os.getenv("CONTACT_WHATSAPP", "")  # رقم واتساب عام للتواصل (يظهر للزوار إن ضُبط)

# تتبع مصدر الزائر: ضع ?src=instagram (أو tiktok / telegram ...) في رابط البايو
SRC_COOKIE = "src"
VISITOR_COOKIE = "v"
REFERRER_SOURCES = {
    "instagram": "instagram", "tiktok": "tiktok", "t.me": "telegram", "telegram": "telegram",
    "facebook": "facebook", "fb.": "facebook", "whatsapp": "whatsapp", "wa.me": "whatsapp",
    "google": "google",
}
SOURCE_LABELS = {
    "instagram": "إنستغرام", "tiktok": "تيك توك", "telegram": "تيليغرام", "facebook": "فيسبوك",
    "whatsapp": "واتساب", "google": "غوغل", "direct": "مباشر", "other": "مواقع أخرى",
}
# روبوتات المعاينة ومحركات البحث وأدوات الفحص والمراقبة: لا تُحسب زواراً ولا نقرات
BOT_RE = re.compile(
    r"bot|crawl|spider|preview|facebookexternalhit|whatsapp|telegram|slurp"
    r"|curl|wget|python|httpx|go-http|uptime|monitor|pingdom|headless|lighthouse", re.I)

# ─────────────────────────── قاعدة البيانات ───────────────────────────

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    """تاريخ اليوم بتوقيت العراق (UTC+3)."""
    return (utcnow() + timedelta(hours=3)).date()


class Base(DeclarativeBase):
    pass


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(140))
    category: Mapped[str] = mapped_column(String(40), index=True)
    area: Mapped[str] = mapped_column(String(40), index=True)
    old_price: Mapped[int] = mapped_column(Integer)
    new_price: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str] = mapped_column(String(120))
    link_url: Mapped[str] = mapped_column(String(500))
    contact_phone: Mapped[str] = mapped_column(String(30), default="")
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    paid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # نهاية الاشتراك المدفوع
    ends_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # آخر يوم لصلاحية العرض نفسه

    @property
    def ends_soon(self) -> bool:
        return self.ends_on is not None and 0 <= (self.ends_on - today()).days <= 3

    @property
    def ends_text(self) -> str:
        left = (self.ends_on - today()).days
        return "اليوم" if left == 0 else f"بعد {left} يوم"

    @property
    def days_left(self) -> Optional[int]:
        return (self.paid_until - today()).days if self.paid_until else None

    @property
    def monthly_fee(self) -> int:
        return VIP_PRICE if self.is_vip else BASIC_PRICE

    @property
    def contact_wa(self) -> str:
        return normalize_phone(self.contact_phone)

    @property
    def discount_percent(self) -> int:
        if self.old_price <= 0:
            return 0
        return round((self.old_price - self.new_price) * 100 / self.old_price)

    @property
    def savings(self) -> int:
        return max(self.old_price - self.new_price, 0)

    @property
    def is_new(self) -> bool:
        return utcnow() - self.created_at < timedelta(days=3)

    @property
    def image_url(self) -> str:
        return f"/uploads/{self.image_path}"


class ClickEvent(Base):
    """سجل زمني لكل نقرة — يسمح بحساب «نقرات آخر 30 يوماً» لتقرير الاشتراك الشهري."""

    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(20), default="direct")


class Visit(Base):
    """عدّاد زوار يومي تقريبي لكل مصدر (زائر واحد = مرة واحدة كل 24 ساعة)."""

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(20))
    count: Mapped[int] = mapped_column(Integer, default=0)


Base.metadata.create_all(engine)


def ensure_columns() -> None:
    """ترقية بسيطة لقواعد البيانات القديمة: يضيف الأعمدة الجديدة إن لم تكن موجودة."""
    wanted = {
        "deals": {"paid_until": "DATE", "ends_on": "DATE"},
        "click_events": {"source": "VARCHAR(20) DEFAULT 'direct'"},
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


ensure_columns()


def live_deal():
    """شرط: العرض لم ينتهِ بعد (بدون تاريخ انتهاء = مستمر)."""
    return or_(Deal.ends_on.is_(None), Deal.ends_on >= today())


def live(deal: "Deal") -> bool:
    """هل العرض ساري الآن (لم يتجاوز آخر يوم له)؟"""
    return deal.ends_on is None or deal.ends_on >= today()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────── أدوات مساعدة ───────────────────────────

_RATE: dict[str, list[float]] = {}


def too_many(key: str, limit: int, window: int) -> bool:
    now = time.time()
    hits = [t for t in _RATE.get(key, []) if now - t < window]
    if hits:
        _RATE[key] = hits
    else:
        _RATE.pop(key, None)  # لا نُبقي مفاتيح فارغة حتى لا تكبر الذاكرة مع الوقت
    return len(hits) >= limit


def record_hit(key: str) -> None:
    _RATE.setdefault(key, []).append(time.time())


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def make_session_token() -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(SESSION_KEY, exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def is_admin(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    exp, _, sig = token.partition(".")
    if not (exp.isascii() and exp.isdigit()) or len(exp) > 12 or int(exp) < time.time():
        return False
    expected = hmac.new(SESSION_KEY, exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.encode(), expected.encode())


class AdminLoginRequired(Exception):
    pass


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise AdminLoginRequired()


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def parse_optional_date(raw: str) -> tuple[Optional[date], bool]:
    """يعيد (التاريخ أو None، هل المُدخل صالح)."""
    raw = (raw or "").strip()
    if not raw:
        return None, True
    try:
        return date.fromisoformat(raw), True
    except ValueError:
        return None, False


def parse_price(raw: str) -> Optional[int]:
    """يقبل الأرقام العربية والفواصل: «٢٥,٠٠٠» أو «25.000» → 25000 (الدينار بلا كسور)."""
    cleaned = re.sub(r"[,\.٬،\s]", "", (raw or "").translate(_DIGITS))
    return int(cleaned) if cleaned.isdigit() else None


def normalize_phone(raw: str) -> str:
    """يحوّل رقماً عراقياً بأي صيغة إلى الصيغة الدولية بلا رموز (9647…)، أو نص فارغ إن لم يصلح."""
    digits = re.sub(r"\D", "", (raw or "").translate(_DIGITS))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "964" + digits[1:]
    elif digits.startswith("7") and len(digits) == 10:
        digits = "964" + digits
    return digits if 10 <= len(digits) <= 15 else ""


def normalize_link(raw: str) -> str:
    """يحوّل رقم واتساب أو اسم حساب إنستغرام أو رابطاً كاملاً إلى رابط http(s) آمن."""
    raw = (raw or "").strip().translate(_DIGITS)
    if not raw:
        raise ValueError("أدخل رابط الإنستغرام أو رقم الواتساب")

    if re.fullmatch(r"[+\d\s\-()]{7,20}", raw):  # رقم هاتف → واتساب
        digits = normalize_phone(raw)
        if not digits:
            raise ValueError("رقم الواتساب غير صحيح")
        return f"https://wa.me/{digits}"

    # اسم حساب إنستغرام: كل ما يبدأ بـ @، أو اسم بلا امتداد موقع معروف (burger.house حساب، shop.com موقع)
    if re.fullmatch(r"@?[A-Za-z0-9._]{2,30}", raw):
        is_domain = re.search(r"\.(com|net|org|iq|me|co|io|app|shop|store|link|site|online)$", raw, re.I)
        if raw.startswith("@") or not is_domain:
            return f"https://instagram.com/{raw.lstrip('@')}"

    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or "." not in parsed.netloc or len(raw) > 500:
        raise ValueError("الرابط غير صالح — أدخل رابط إنستغرام/واتساب أو رقم واتساب")
    return raw


def save_image(upload: UploadFile) -> str:
    """يتحقق من الصورة، يصغّرها ويحوّلها إلى WebP خفيف، ويعيد اسم الملف."""
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("حجم الصورة كبير — الحد الأقصى 8 ميغابايت")
    invalid = ValueError("الملف ليس صورة صالحة (JPG / PNG / WebP)")
    try:
        img = Image.open(BytesIO(data))  # يقرأ الترويسة فقط، فنفحص الأبعاد قبل فك الضغط
    except Exception:
        raise invalid
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ValueError("أبعاد الصورة كبيرة جداً — صغّرها ثم أعد المحاولة")
    try:
        img.draft("RGB", (MAX_IMAGE_SIDE * 2, MAX_IMAGE_SIDE * 2))  # فك JPEG بحجم أصغر = ذاكرة أقل
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    except Exception:
        raise invalid
    name = f"{uuid.uuid4().hex}.webp"
    img.save(UPLOAD_DIR / name, "WEBP", quality=80)
    return name


def delete_image(name: str) -> None:
    if name:
        (UPLOAD_DIR / Path(name).name).unlink(missing_ok=True)


def clean_source(raw: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", (raw or "").lower())[:20]


def detect_source(request: Request) -> str:
    """مصدر الزيارة من ?src= / ?utm_source= أو من الموقع المُحيل. نص فارغ إن لم يُعرف."""
    explicit = clean_source(request.query_params.get("src") or request.query_params.get("utm_source"))
    if explicit:
        return explicit
    ref_host = urlparse(request.headers.get("referer", "")).netloc.lower()
    if not ref_host or ref_host == request.headers.get("host", "").lower():
        return ""
    for needle, source in REFERRER_SOURCES.items():
        if needle in ref_host:
            return source
    return "other"


def track_visit(request: Request, response, db: Session) -> None:
    """يسجّل الزائر مرة واحدة يومياً مع مصدره، ويحفظ المصدر في كوكي ليُنسب له النقر لاحقاً."""
    if request.method != "GET" or BOT_RE.search(request.headers.get("user-agent", "")) or is_admin(request):
        return
    detected = detect_source(request)
    source = detected or clean_source(request.cookies.get(SRC_COOKIE)) or "direct"
    if detected:
        response.set_cookie(SRC_COOKIE, detected, max_age=86400, httponly=True, samesite="lax")
    if not request.cookies.get(VISITOR_COOKIE):
        day = today()
        # زيادة ذرّية داخل قاعدة البيانات حتى لا تضيع زيارات عند تزامن الطلبات
        updated = db.execute(
            update(Visit).where(Visit.day == day, Visit.source == source).values(count=Visit.count + 1)
        ).rowcount
        if not updated:
            db.add(Visit(day=day, source=source, count=1))
        db.commit()
        response.set_cookie(VISITOR_COOKIE, "1", max_age=86400, httponly=True, samesite="lax")


def stats_message(deal: "Deal", clicks: int) -> str:
    """رسالة نهاية الشهر الجاهزة للإرسال لصاحب المحل."""
    lines = [
        f"هلا بيكم 🌹 هاي نتيجة عرض «{deal.shop_name}» بموقع {SITE_NAME} خلال آخر 30 يوم:",
        f"📊 {clicks:,} شخص ضغطوا «اطلب العرض» وتحولوا لصفحتكم.",
    ]
    if clicks >= 20:
        lines.append(f"يعني بالباقة الأساسية كل زبون يوصلكم بحوالي {round(BASIC_PRICE / clicks):,} دينار بس.")
    lines += [
        "",
        "إذا تحبون نستمر:",
        f"▫️ الأساسية — {BASIC_PRICE:,} د.ع بالشهر: عرضكم يبقى منشور وتغيّروه وقت ما تريدون.",
        f"🔥 VIP — {VIP_PRICE:,} د.ع بالشهر: عرضكم مثبّت بأعلى الموقع بإطار ذهبي.",
        "",
        f"الدفع زين كاش على {ZAINCASH_NUMBER}." if ZAINCASH_NUMBER else "الدفع عبر زين كاش.",
        "أكمّل وياكم؟",
    ]
    return "\n".join(lines)


def validate_deal(shop_name, title, category, area, old_price, new_price, link, contact_phone):
    """يعيد (البيانات النظيفة، قائمة الأخطاء)."""
    errors: list[str] = []
    data = {
        "shop_name": (shop_name or "").strip()[:80],
        "title": (title or "").strip()[:140],
        "category": category,
        "area": area,
        "contact_phone": (contact_phone or "").strip()[:30],
    }
    if len(data["shop_name"]) < 2:
        errors.append("اكتب اسم المحل أو البيج")
    if len(data["title"]) < 3:
        errors.append("اكتب وصفاً قصيراً للعرض")
    if category not in CATEGORIES:
        errors.append("اختر الفئة")
    if area not in AREAS:
        errors.append("اختر المنطقة")

    old_p, new_p = parse_price(old_price), parse_price(new_price)
    if old_p is None or new_p is None or new_p < 250 or max(old_p, new_p) > 1_000_000_000:
        errors.append("أدخل السعرين بالأرقام (بالدينار العراقي)")
    elif new_p >= old_p:
        errors.append("السعر بعد الخصم يجب أن يكون أقل من السعر السابق")
    else:
        data["old_price"], data["new_price"] = old_p, new_p

    try:
        data["link_url"] = normalize_link(link)
    except ValueError as e:
        errors.append(str(e))
    return data, errors


# ───────────────────────────── القوالب ─────────────────────────────

TEMPLATES = {
    "base.html": """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}{{ site_name }} — أقوى عروض وتخفيضات الموصل اليومية{% endblock %}</title>
<meta name="description" content="أقوى عروض وتخفيضات محلات ومطاعم وبيجات الموصل في مكان واحد، محدّثة يومياً.">
<meta name="theme-color" content="#0a0a0f">
{% block og %}
<meta property="og:title" content="{{ site_name }} — أقوى عروض وتخفيضات الموصل اليومية">
<meta property="og:description" content="كل عروض محلات ومطاعم وبيجات الموصل بمكان واحد.">
{% endblock %}
<meta property="og:type" content="website">
<meta property="og:locale" content="ar_IQ">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/app.css">
<style>
.no-scrollbar::-webkit-scrollbar{display:none}.no-scrollbar{scrollbar-width:none}
@keyframes shine{0%{background-position:200% 0}100%{background-position:-200% 0}}
.vip-badge{background:linear-gradient(110deg,#fbbf24 30%,#fef08a 50%,#fbbf24 70%);background-size:200% 100%;animation:shine 3s linear infinite}
.glow-text{background:linear-gradient(100deg,#fbbf24,#fb923c 35%,#f43f5e);-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 0 22px rgba(244,63,94,.5))}
.ambient{position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(620px 320px at 85% -4%,rgba(244,63,94,.24),transparent 70%),radial-gradient(520px 320px at 0% 8%,rgba(251,146,60,.15),transparent 70%)}
.glass{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09)}
:focus-visible{outline:2px solid #fb7185;outline-offset:2px;border-radius:6px}
select option{background:#15151c;color:#f1f5f9}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.35}}
.live-dot{animation:pulse-dot 1.6s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){.vip-badge,.live-dot{animation:none}}
</style>
</head>
<body class="overflow-x-clip font-sans antialiased {% block body %}bg-[#0a0a0f] text-slate-100{% endblock %}">
{% block ambient %}<div class="ambient"></div>{% endblock %}
<a href="#main" class="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:right-2 focus:z-50 bg-white text-slate-900 font-bold rounded-full px-4 py-2">تخطي إلى المحتوى</a>
<header class="sticky top-0 z-30 bg-[#0a0a0f]/85 backdrop-blur-md border-b border-white/10">
  <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
    <a href="/" class="text-2xl font-extrabold text-white">🔥 <span class="glow-text">{{ site_name }}</span></a>
    <a href="/submit" class="text-sm font-bold bg-white text-slate-900 rounded-full px-4 py-2 active:scale-95 transition">+ أضف عرض محلك</a>
  </div>
</header>
<main id="main" class="max-w-5xl mx-auto px-4 pb-16" tabindex="-1">{% block content %}{% endblock %}</main>
<footer class="text-center text-xs text-slate-400 pb-8 space-y-2">
  <nav class="flex flex-wrap justify-center gap-x-4 gap-y-1 font-bold" aria-label="روابط الموقع">
    <a href="/join" class="underline">للمحلات والبيجات</a>
    <a href="/about" class="underline">من نحن</a>
    <a href="/terms" class="underline">الشروط والخصوصية</a>
    {% if contact_wa %}<a href="https://wa.me/{{ contact_wa }}" target="_blank" rel="noopener" class="underline">تواصل واتساب</a>{% endif %}
  </nav>
  <p>© {{ site_name }} — كل عروض الموصل بمكان واحد</p>
</footer>
<script>
document.addEventListener('click',function(e){
  var b=e.target.closest('[data-share]'); if(!b)return;
  e.preventDefault();
  var text=b.dataset.share, url=location.origin+(b.dataset.url||'/');
  if(navigator.share){navigator.share({text:text,url:url}).catch(function(){})}
  else{window.open('https://wa.me/?text='+encodeURIComponent(text+'\\n'+url),'_blank','noopener')}
});
</script>
</body>
</html>""",
    "macros.html": """{% macro card(d) %}
<article class="group relative flex flex-col bg-[#15151c] rounded-3xl overflow-hidden
  {{ 'ring-1 ring-amber-400/80 shadow-[0_0_30px_-6px_rgba(251,191,36,.55)]' if d.is_vip else 'ring-1 ring-white/10' }}">
  <div class="relative aspect-square bg-white/5">
    <a href="/deal/{{ d.id }}" class="block w-full h-full"><img src="{{ d.image_url }}" alt="{{ d.title }}" loading="lazy" decoding="async" class="w-full h-full object-cover"></a>
    <span class="absolute bottom-2 left-2 bg-rose-600 text-white text-sm font-extrabold rounded-lg px-2 py-0.5 shadow" dir="ltr">-{{ d.discount_percent }}%</span>
    {% if d.is_vip %}
    <span class="vip-badge absolute top-2 right-2 text-amber-950 text-[11px] font-extrabold rounded-full px-2.5 py-1 shadow">🔥 عرض مميز</span>
    {% elif d.is_new %}
    <span class="absolute top-2 right-2 bg-white/95 text-slate-800 text-[11px] font-extrabold rounded-full px-2.5 py-1 shadow">✨ جديد</span>
    {% endif %}
    <button type="button" aria-label="مشاركة العرض" data-url="/deal/{{ d.id }}" data-share="{{ d.shop_name }} — {{ d.title }} بـ {{ d.new_price | price }} د.ع بدل {{ d.old_price | price }} 🔥"
            class="absolute top-2 left-2 w-8 h-8 grid place-items-center rounded-full bg-black/45 text-white backdrop-blur active:scale-90 transition">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/></svg>
    </button>
  </div>
  <div class="flex flex-col flex-1 p-3">
    <p class="text-xs font-bold text-rose-400 truncate">{{ d.shop_name }}</p>
    <h3 class="text-sm font-bold leading-snug mt-0.5 line-clamp-2 min-h-[2.5rem]"><a href="/deal/{{ d.id }}">{{ d.title }}</a></h3>
    <p class="text-[11px] text-slate-400 mt-1 truncate">📍 {{ d.area }}</p>
    {% if d.ends_soon %}<p class="mt-1 text-[11px] font-extrabold text-amber-300">⏳ ينتهي {{ d.ends_text }}</p>{% endif %}
    <div class="mt-auto pt-2">
      <div class="flex items-baseline justify-between gap-1">
        <p class="text-lg font-extrabold text-white leading-none">{{ d.new_price | price }} <span class="text-[11px] font-bold">د.ع</span></p>
        <p class="text-[11px] text-slate-400 line-through">{{ d.old_price | price }}</p>
      </div>
      <p class="mt-1.5 inline-block text-[11px] font-bold text-emerald-300 bg-emerald-400/10 rounded-md px-1.5 py-0.5">وفّر {{ d.savings | price }} د.ع</p>
      <a href="/go/{{ d.id }}" target="_blank" rel="nofollow noopener"
         class="mt-2.5 block text-center text-sm font-extrabold rounded-full py-2.5 active:scale-95 transition
         {{ 'bg-amber-400 text-amber-950' if d.is_vip else 'bg-gradient-to-l from-rose-600 to-orange-500 text-white' }}">اطلب العرض</a>
    </div>
  </div>
</article>
{% endmacro %}""",
    "home.html": """{% extends "base.html" %}
{% from "macros.html" import card %}
{% macro chip(label, url, active) %}
<a href="{{ url }}" {{ 'data-active aria-current=true' if active }} class="shrink-0 rounded-full px-3.5 py-2 text-sm font-bold border transition active:scale-95
  {{ 'bg-white text-slate-900 border-white' if active else 'bg-white/5 text-slate-300 border-white/10' }}">{{ label }}</a>
{% endmacro %}

{% block content %}
<section class="pt-6 pb-2 text-center">
  <p class="inline-flex items-center gap-2 text-[11px] font-bold tracking-wide text-amber-300 border border-amber-400/40 bg-amber-400/10 rounded-full px-3 py-1">
    <span class="live-dot w-1.5 h-1.5 rounded-full bg-amber-400"></span>لقطات جديدة كل يوم</p>
  <h1 class="mt-3 text-4xl md:text-6xl font-extrabold leading-[1.2] tracking-tight">لا تفوّت <span class="glow-text">لقطة</span><br>بالموصل</h1>
  <p class="mt-3 text-sm md:text-base text-slate-400 max-w-md mx-auto">تخفيضات المحلات والمطاعم والبيجات، مرتّبة حسب منطقتك. اختر العرض واطلبه مباشرة.</p>
  <p class="mt-4 inline-block text-sm font-bold text-white glass rounded-full px-4 py-1.5">{{ total }} عرض متاح الآن</p>
</section>

<section class="pt-3 space-y-2">
  <form action="/" method="get" role="search" class="relative">
    <label for="q" class="sr-only">ابحث عن محل أو عرض</label>
    <input id="q" type="search" name="q" value="{{ q }}" placeholder="ابحث عن محل أو عرض…" maxlength="40"
           class="w-full rounded-full bg-white/5 border border-white/10 text-white placeholder-slate-500 px-5 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500">
    {% if area %}<input type="hidden" name="area" value="{{ area }}">{% endif %}
    {% if category %}<input type="hidden" name="category" value="{{ category }}">{% endif %}
  </form>
  <div class="flex gap-2 overflow-x-auto no-scrollbar -mx-4 px-4">
    {{ chip('الكل', filter_url(area, ''), not category) }}
    {% for c in categories %}{{ chip(category_icons[c] ~ ' ' ~ c, filter_url(area, c), c == category) }}{% endfor %}
  </div>
  <div class="flex gap-2 overflow-x-auto no-scrollbar -mx-4 px-4">
    {{ chip('📍 كل المناطق', filter_url('', category), not area) }}
    {% for a in areas %}{{ chip(a, filter_url(a, category), a == area) }}{% endfor %}
  </div>
</section>

{% if deals %}
{% set vip = deals | selectattr('is_vip') | list %}
{% set normal = deals | rejectattr('is_vip') | list %}
{% if vip %}
<h2 class="mt-5 mb-2 text-base font-extrabold">🔥 عروض مميزة</h2>
<section class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
  {% for d in vip %}{{ card(d) }}{% endfor %}
</section>
{% endif %}
{% if normal %}
<h2 class="mt-5 mb-2 text-base font-extrabold">أحدث العروض <span class="text-sm font-medium text-slate-400">({{ normal | length }})</span></h2>
<section class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
  {% for d in normal %}{{ card(d) }}{% endfor %}
</section>
{% endif %}

{% if prev_url or next_url %}
<nav class="mt-6 flex justify-center gap-3" aria-label="صفحات العروض">
  {% if prev_url %}<a href="{{ prev_url }}" class="glass rounded-full px-5 py-2.5 text-sm font-bold active:scale-95 transition">→ السابق</a>{% endif %}
  {% if next_url %}<a href="{{ next_url }}" class="bg-white text-slate-900 rounded-full px-6 py-2.5 text-sm font-extrabold active:scale-95 transition">عروض أكثر ←</a>{% endif %}
</nav>
{% endif %}

<section class="mt-10 rounded-3xl glass p-6 text-center">
  <p class="text-xl font-extrabold">عندك محل أو بيج <span class="glow-text">بالموصل</span>؟</p>
  <p class="text-sm text-slate-400 mt-1">انشر عرضك هنا ووصّله لزبائن جدد كل يوم.</p>
  <a href="/submit" class="mt-3 inline-block bg-amber-400 text-amber-950 font-extrabold rounded-xl px-6 py-2.5 active:scale-95 transition">أضف عرضك مجاناً</a>
</section>

<script>
document.querySelectorAll('[data-active]').forEach(function(el){el.scrollIntoView({inline:'center',block:'nearest'})});
</script>
{% else %}
<div class="mt-16 text-center text-slate-500">
  <p class="text-5xl">🛍️</p>
  <p class="mt-3 font-bold">لا توجد عروض بهذه الفلترة حالياً</p>
  <a href="/" class="mt-3 inline-block text-rose-600 font-bold underline">عرض كل العروض</a>
</div>
{% endif %}
{% endblock %}""",
    "deal_form.html": """{% extends "base.html" %}
{% block title %}{{ heading }} — {{ site_name }}{% endblock %}
{% set inp = "w-full rounded-xl border border-white/15 bg-white/5 text-white placeholder-slate-500 [color-scheme:dark] px-3 py-2.5 text-base focus:outline-none focus:ring-2 focus:ring-rose-500" %}
{% block content %}
<div class="max-w-lg mx-auto pt-6">
  <h1 class="text-2xl font-extrabold">{{ heading }}</h1>
  {% if intro %}<p class="text-sm text-slate-500 mt-1">{{ intro }}</p>{% endif %}

  {% if errors %}
  <ul class="mt-4 bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-xl p-3 space-y-1">
    {% for e in errors %}<li>• {{ e }}</li>{% endfor %}
  </ul>
  {% endif %}

  <form method="post" action="{{ action }}" enctype="multipart/form-data" class="mt-5 space-y-4">
    <label class="block"><span class="text-sm font-bold">اسم المحل / البيج</span>
      <input name="shop_name" value="{{ form.shop_name }}" required maxlength="80" class="{{ inp }} mt-1"></label>
    <label class="block"><span class="text-sm font-bold">وصف العرض</span>
      <input name="title" value="{{ form.title }}" required maxlength="140" placeholder="مثال: وجبة برغر دبل + بطاطا + مشروب" class="{{ inp }} mt-1"></label>
    <div class="grid grid-cols-2 gap-3">
      <label class="block"><span class="text-sm font-bold">الفئة</span>
        <select name="category" required class="{{ inp }} mt-1">
          <option value="">اختر…</option>
          {% for c in categories %}<option {{ 'selected' if c == form.category }}>{{ c }}</option>{% endfor %}
        </select></label>
      <label class="block"><span class="text-sm font-bold">المنطقة</span>
        <select name="area" required class="{{ inp }} mt-1">
          <option value="">اختر…</option>
          {% for a in areas %}<option {{ 'selected' if a == form.area }}>{{ a }}</option>{% endfor %}
        </select></label>
    </div>
    <div class="grid grid-cols-2 gap-3">
      <label class="block"><span class="text-sm font-bold">السعر السابق (د.ع)</span>
        <input id="old_price" name="old_price" value="{{ form.old_price }}" required inputmode="numeric" placeholder="25000" class="{{ inp }} mt-1"></label>
      <label class="block"><span class="text-sm font-bold">السعر بعد الخصم (د.ع)</span>
        <input id="new_price" name="new_price" value="{{ form.new_price }}" required inputmode="numeric" placeholder="18000" class="{{ inp }} mt-1"></label>
    </div>
    <p id="discount_hint" class="hidden -mt-2 text-sm font-bold rounded-xl px-3 py-2"></p>
    <label class="block"><span class="text-sm font-bold">آخر يوم للعرض <span class="font-normal text-slate-400">(اختياري — يختفي تلقائياً بعده)</span></span>
      <input type="date" name="ends_on" value="{{ form.ends_on }}" class="{{ inp }} mt-1"></label>
    <label class="block"><span class="text-sm font-bold">رابط الطلب (إنستغرام أو رقم واتساب)</span>
      <input name="link" value="{{ form.link }}" required dir="ltr" placeholder="07701234567" class="{{ inp }} mt-1 text-left">
      <span class="block text-xs text-slate-400 mt-1">اكتب رقم الواتساب، أو اسم حساب الإنستغرام، أو الرابط كاملاً.</span></label>
    <label class="block"><span class="text-sm font-bold">رقم للتواصل معك <span class="font-normal text-slate-400">(لا يظهر للزوار)</span></span>
      <input name="contact_phone" value="{{ form.contact_phone }}" inputmode="tel" dir="ltr" placeholder="07701234567" class="{{ inp }} mt-1 text-left"></label>
    <label class="block"><span class="text-sm font-bold">صورة العرض {% if deal %}<span class="font-normal text-slate-400">(اتركها فارغة للإبقاء على الصورة الحالية)</span>{% endif %}</span>
      <img id="image_preview" {% if deal %}src="{{ deal.image_url }}"{% endif %} alt="" class="{{ '' if deal else 'hidden' }} mt-2 w-28 h-28 object-cover rounded-xl ring-1 ring-slate-200">
      <input id="image_input" type="file" name="image" accept="image/jpeg,image/png,image/webp" {{ '' if deal else 'required' }}
             class="mt-2 block w-full text-sm file:ml-3 file:rounded-full file:border-0 file:bg-rose-50 file:text-rose-700 file:font-bold file:px-4 file:py-2"></label>

    {% if deal %}
    <label class="block"><span class="text-sm font-bold">الاشتراك المدفوع ينتهي في <span class="font-normal text-slate-400">(فارغ = تجربة مجانية)</span></span>
      <input type="date" name="paid_until" value="{{ form.paid_until }}" class="{{ inp }} mt-1"></label>
    <div class="flex gap-6 glass rounded-xl p-3">
      <label class="flex items-center gap-2 text-sm font-bold"><input type="checkbox" name="is_approved" value="1" {{ 'checked' if form.is_approved }} class="w-5 h-5 accent-emerald-600"> منشور (موافَق عليه)</label>
      <label class="flex items-center gap-2 text-sm font-bold"><input type="checkbox" name="is_vip" value="1" {{ 'checked' if form.is_vip }} class="w-5 h-5 accent-amber-500"> 🔥 VIP</label>
    </div>
    {% else %}
    <div class="hidden" aria-hidden="true"><input name="website" tabindex="-1" autocomplete="off"></div>
    {% endif %}

    <button id="submit_btn" class="w-full bg-rose-600 text-white font-extrabold rounded-xl py-3 text-base active:scale-95 transition disabled:opacity-60">{{ submit_label }}</button>
    {% if deal %}<a href="/admin" class="block text-center text-sm text-slate-500">إلغاء والعودة للوحة التحكم</a>{% endif %}
  </form>
</div>
<script>
(function(){
  var AR='٠١٢٣٤٥٦٧٨٩', FA='۰۱۲۳۴۵۶۷۸۹';
  function num(v){
    v=v.replace(/[٠-٩۰-۹]/g,function(c){var i=AR.indexOf(c);return i>-1?i:FA.indexOf(c)}).replace(/[^0-9]/g,'');
    return v?parseInt(v,10):null;
  }
  var o=document.getElementById('old_price'), n=document.getElementById('new_price'), h=document.getElementById('discount_hint');
  function hint(){
    var a=num(o.value), b=num(n.value);
    if(a===null||b===null){h.className='hidden';return}
    if(b>=a){h.textContent='السعر بعد الخصم يجب أن يكون أقل من السعر السابق';h.className='-mt-2 text-sm font-bold rounded-xl px-3 py-2 bg-rose-50 text-rose-700';return}
    h.textContent='نسبة الخصم: '+Math.round((a-b)*100/a)+'% — الزبون يوفّر '+(a-b).toLocaleString('en-US')+' د.ع';
    h.className='-mt-2 text-sm font-bold rounded-xl px-3 py-2 bg-emerald-50 text-emerald-700';
  }
  o.addEventListener('input',hint); n.addEventListener('input',hint); hint();
  var f=document.getElementById('image_input'), p=document.getElementById('image_preview');
  f.addEventListener('change',function(){
    if(!f.files||!f.files[0])return;
    if(f.files[0].size>8*1024*1024){alert('حجم الصورة كبير — الحد الأقصى 8 ميغابايت');f.value='';p.classList.add('hidden');return}
    p.src=URL.createObjectURL(f.files[0]);p.classList.remove('hidden');
  });
  var btn=document.getElementById('submit_btn');
  btn.form.addEventListener('submit',function(){btn.disabled=true;btn.textContent='جارٍ الإرسال…'});
})();
</script>
{% endblock %}""",
    "message.html": """{% extends "base.html" %}
{% block content %}
<div class="max-w-md mx-auto pt-16 text-center">
  <p class="text-6xl">{{ emoji }}</p>
  <h1 class="mt-4 text-2xl font-extrabold">{{ heading }}</h1>
  <p class="mt-2 text-slate-500">{{ body }}</p>
  <a href="/" class="mt-6 inline-block bg-white text-slate-900 font-bold rounded-full px-6 py-3">العودة للعروض</a>
</div>
{% endblock %}""",
    "deal.html": """{% extends "base.html" %}
{% from "macros.html" import card %}
{% block title %}{{ deal.title }} — {{ deal.shop_name }} | {{ site_name }}{% endblock %}
{% block og %}
<meta property="og:title" content="{{ deal.shop_name }} — {{ deal.title }}">
<meta property="og:description" content="{{ deal.new_price | price }} د.ع بدل {{ deal.old_price | price }} — خصم {{ deal.discount_percent }}% 🔥">
<meta property="og:image" content="{{ og_image }}">
<meta property="og:url" content="{{ og_url }}">
<meta property="og:type" content="product">
<link rel="canonical" href="{{ og_url }}">
<script type="application/ld+json">{{ jsonld | safe }}</script>
{% endblock %}
{% block content %}
<a href="/" class="inline-block mt-3 text-sm font-bold text-slate-500">→ كل العروض</a>
{% if not deal.is_approved %}
<p class="mt-2 bg-rose-50 border border-rose-200 text-rose-700 text-sm font-bold rounded-xl p-3">هذا العرض غير منشور — تراه لأنك مسجّل كمدير.</p>
{% endif %}
<article class="mt-2 bg-[#15151c] rounded-3xl overflow-hidden md:grid md:grid-cols-2
  {{ 'ring-1 ring-amber-400/80 shadow-[0_0_36px_-6px_rgba(251,191,36,.55)]' if deal.is_vip else 'ring-1 ring-white/10' }}">
  <div class="relative aspect-square bg-white/5">
    <img src="{{ deal.image_url }}" alt="{{ deal.title }}" class="w-full h-full object-cover">
    <span class="absolute bottom-3 left-3 bg-rose-600 text-white text-lg font-extrabold rounded-lg px-2.5 py-0.5 shadow" dir="ltr">-{{ deal.discount_percent }}%</span>
    {% if deal.is_vip %}<span class="vip-badge absolute top-3 right-3 text-amber-950 text-xs font-extrabold rounded-full px-3 py-1 shadow">🔥 عرض مميز</span>{% endif %}
  </div>
  <div class="p-4 md:p-6 flex flex-col">
    <p class="text-sm font-bold text-rose-400">{{ deal.shop_name }}</p>
    <h1 class="text-xl font-extrabold leading-snug mt-1">{{ deal.title }}</h1>
    <div class="mt-2 flex flex-wrap gap-2 text-xs font-bold text-slate-300">
      <a href="{{ filter_url(deal.area, '') }}" class="bg-white/10 rounded-full px-3 py-1">📍 {{ deal.area }}</a>
      <a href="{{ filter_url('', deal.category) }}" class="bg-white/10 rounded-full px-3 py-1">{{ category_icons[deal.category] }} {{ deal.category }}</a>
    </div>
    <div class="mt-4 flex items-baseline gap-3">
      <p class="text-3xl font-extrabold text-white leading-none">{{ deal.new_price | price }} <span class="text-sm font-bold">د.ع</span></p>
      <p class="text-sm text-slate-400 line-through">{{ deal.old_price | price }} د.ع</p>
    </div>
    <p class="mt-2 self-start text-sm font-bold text-emerald-300 bg-emerald-400/10 rounded-md px-2 py-1">وفّر {{ deal.savings | price }} د.ع</p>
    {% if deal.ends_soon %}<p class="mt-2 text-sm font-extrabold text-amber-300">⏳ ينتهي {{ deal.ends_text }}</p>{% endif %}
    <div class="mt-5 md:mt-auto flex gap-2">
      <a href="/go/{{ deal.id }}" target="_blank" rel="nofollow noopener"
         class="flex-1 text-center text-base font-extrabold rounded-full py-3.5 active:scale-95 transition
         {{ 'bg-amber-400 text-amber-950' if deal.is_vip else 'bg-gradient-to-l from-rose-600 to-orange-500 text-white' }}">اطلب العرض</a>
      <button type="button" data-url="/deal/{{ deal.id }}" data-share="{{ deal.shop_name }} — {{ deal.title }} بـ {{ deal.new_price | price }} د.ع بدل {{ deal.old_price | price }} 🔥"
              class="shrink-0 px-5 rounded-full border border-white/20 text-sm font-bold text-slate-200 active:scale-95 transition">مشاركة</button>
    </div>
  </div>
</article>

{% if similar %}
<h2 class="mt-6 mb-2 text-base font-extrabold">عروض مشابهة</h2>
<section class="grid grid-cols-2 md:grid-cols-4 gap-3">
  {% for d in similar %}{{ card(d) }}{% endfor %}
</section>
{% endif %}
{% endblock %}""",
    "join.html": """{% extends "base.html" %}
{% block title %}للمحلات والبيجات — انشر عرضك في {{ site_name }}{% endblock %}
{% block content %}
<section class="pt-8 text-center">
  <p class="inline-flex items-center gap-2 text-[11px] font-bold tracking-wide text-amber-300 border border-amber-400/40 bg-amber-400/10 rounded-full px-3 py-1">لأصحاب المحلات والبيجات</p>
  <h1 class="mt-3 text-4xl md:text-6xl font-extrabold leading-[1.2] tracking-tight">وصّل عرضك <span class="glow-text">لزبائن الموصل</span></h1>
  <p class="mt-3 text-sm md:text-base text-slate-400 max-w-md mx-auto">{{ site_name }} يجمع عروض المدينة بمكان واحد. انشر عرضك، والزبون يضغط ويوصل مباشرة لواتسابك أو صفحتك.</p>
  <a href="/submit" class="mt-5 inline-block bg-gradient-to-l from-rose-600 to-orange-500 text-white font-extrabold rounded-full px-8 py-3.5 active:scale-95 transition">جرّب شهراً مجاناً</a>
</section>

<h2 class="mt-10 mb-3 text-lg font-extrabold">شلون يشتغل؟</h2>
<section class="grid md:grid-cols-3 gap-3">
  {% for n, t, d in [
      ('1', 'أرسل عرضك', 'صورة المنتج، السعر قبل وبعد، ورابط صفحتك أو رقم الواتساب. تاخذ دقيقتين.'),
      ('2', 'نراجعه وننشره', 'نتأكد أن العرض واضح وحقيقي، ثم يظهر في الموقع لكل الزوار.'),
      ('3', 'تستلم الإحصائية', 'نهاية كل شهر نرسل لك عدد الأشخاص اللي ضغطوا «اطلب العرض» ووصلوا لك.')] %}
  <div class="glass rounded-3xl p-5">
    <p class="w-9 h-9 grid place-items-center rounded-full bg-white text-slate-900 font-extrabold">{{ n }}</p>
    <p class="mt-3 font-extrabold">{{ t }}</p>
    <p class="mt-1 text-sm text-slate-400">{{ d }}</p>
  </div>
  {% endfor %}
</section>

<h2 class="mt-10 mb-3 text-lg font-extrabold">الباقات</h2>
<p class="glass rounded-2xl p-4 text-sm font-bold text-emerald-300">🎁 أول شهر مجاناً لكل محل — بدون دفع وبدون التزام. تشوف النتيجة بعينك ثم تقرر.</p>
<section class="mt-3 grid md:grid-cols-2 gap-3">
  <div class="rounded-3xl bg-[#15151c] ring-1 ring-white/10 p-5">
    <p class="font-extrabold">الباقة الأساسية</p>
    <p class="mt-2 text-3xl font-extrabold">{{ basic_price | price }} <span class="text-sm font-bold text-slate-400">د.ع / شهر</span></p>
    <ul class="mt-4 space-y-2 text-sm text-slate-300">
      <li>✓ عرضك منشور طول الشهر</li>
      <li>✓ تغيّر العرض وقت ما تريد</li>
      <li>✓ يظهر بفلتر منطقتك وفئتك</li>
      <li>✓ إحصائية شهرية بعدد النقرات</li>
    </ul>
  </div>
  <div class="rounded-3xl bg-[#15151c] ring-1 ring-amber-400/80 shadow-[0_0_30px_-6px_rgba(251,191,36,.55)] p-5">
    <p class="font-extrabold"><span class="vip-badge text-amber-950 text-[11px] rounded-full px-2.5 py-1">🔥 VIP</span></p>
    <p class="mt-2 text-3xl font-extrabold">{{ vip_price | price }} <span class="text-sm font-bold text-slate-400">د.ع / شهر</span></p>
    <ul class="mt-4 space-y-2 text-sm text-slate-300">
      <li>✓ كل مميزات الأساسية</li>
      <li>✓ عرضك مثبّت بأعلى الموقع</li>
      <li>✓ إطار ذهبي وشارة «عرض مميز»</li>
      <li>✓ أماكن VIP محدودة حتى يبقى عرضك بارزاً</li>
    </ul>
  </div>
</section>
<p class="mt-3 text-xs text-slate-500">الدفع شهري عبر زين كاش، وبدون تجديد تلقائي. التفاصيل في <a href="/terms" class="underline">الشروط</a>.</p>

<h2 class="mt-10 mb-3 text-lg font-extrabold">أسئلة متكررة</h2>
<section class="space-y-2">
  {% for q, a in [
      ('شلون تحسبون النقرات؟', 'كل شخص يضغط «اطلب العرض» ينحسب مرة واحدة. الضغط المتكرر من نفس الشخص والروبوتات ما تنحسب، حتى يبقى الرقم صادق.'),
      ('شنو يصير بعد الشهر المجاني؟', 'نرسل لك الإحصائية وتختار: تكمل بإحدى الباقتين أو توقف. إذا ما تجدد، العرض ينرفع من الموقع وما عليك أي مبلغ.'),
      ('أكدر أغيّر العرض خلال الشهر؟', 'نعم، راسلنا بالعرض الجديد ونبدّله لك بنفس اليوم.'),
      ('عندي بس صفحة إنستغرام، يصير؟', 'يصير. الزبون يضغط ويتحول مباشرة لصفحتك أو لواتسابك.')] %}
  <details class="glass rounded-2xl p-4">
    <summary class="font-bold cursor-pointer">{{ q }}</summary>
    <p class="mt-2 text-sm text-slate-400">{{ a }}</p>
  </details>
  {% endfor %}
</section>

<section class="mt-10 rounded-3xl glass p-6 text-center">
  <p class="text-xl font-extrabold">جاهز تنشر أول عرض؟</p>
  <div class="mt-4 flex flex-wrap justify-center gap-3">
    <a href="/submit" class="bg-amber-400 text-amber-950 font-extrabold rounded-full px-6 py-3 active:scale-95 transition">أضف عرضك الآن</a>
    {% if contact_wa %}<a href="https://wa.me/{{ contact_wa }}" target="_blank" rel="noopener" class="border border-white/20 text-slate-200 font-bold rounded-full px-6 py-3 active:scale-95 transition">راسلنا واتساب</a>{% endif %}
  </div>
</section>
{% endblock %}""",
    "about.html": """{% extends "base.html" %}
{% block title %}من نحن — {{ site_name }}{% endblock %}
{% block content %}
<article class="max-w-2xl mx-auto pt-8 space-y-4 text-slate-300 leading-relaxed">
  <h1 class="text-3xl font-extrabold text-white">من نحن</h1>
  <p><b class="text-white">{{ site_name }}</b> موقع موصلي يجمع عروض وتخفيضات المحلات والمطاعم والبيجات داخل مدينة الموصل بمكان واحد، حتى تلقى أحسن سعر بدون ما تدوّر بعشرين صفحة.</p>
  <h2 class="text-xl font-extrabold text-white pt-2">شلون تستفيد؟</h2>
  <p>اختر منطقتك أو الفئة اللي تهمك، تصفّح العروض، واضغط «اطلب العرض» حتى تتحول مباشرة لواتساب المحل أو صفحته. الطلب والدفع والتوصيل يتم بينك وبين المحل مباشرة.</p>
  <h2 class="text-xl font-extrabold text-white pt-2">ملاحظة مهمة</h2>
  <p>{{ site_name }} وسيط إعلاني فقط: لا نبيع المنتجات ولا نستلم ثمنها. نراجع العروض قبل نشرها، لكن المحل هو المسؤول عن صحة السعر وتوفّر المنتج وجودته. إذا لقيت عرضاً غير صحيح بلّغنا ونرفعه.</p>
  <h2 class="text-xl font-extrabold text-white pt-2">عندك محل أو بيج؟</h2>
  <p>أول شهر مجاناً. <a href="/join" class="underline text-amber-300 font-bold">شوف التفاصيل والباقات</a>.</p>
  {% if contact_wa %}
  <h2 class="text-xl font-extrabold text-white pt-2">تواصل معنا</h2>
  <p><a href="https://wa.me/{{ contact_wa }}" target="_blank" rel="noopener" class="inline-block bg-white text-slate-900 font-bold rounded-full px-6 py-3">راسلنا واتساب</a></p>
  {% endif %}
</article>
{% endblock %}""",
    "terms.html": """{% extends "base.html" %}
{% block title %}الشروط والخصوصية — {{ site_name }}{% endblock %}
{% block content %}
<article class="max-w-2xl mx-auto pt-8 space-y-3 text-slate-300 leading-relaxed">
  <h1 class="text-3xl font-extrabold text-white">الشروط والخصوصية</h1>

  <h2 class="text-xl font-extrabold text-white pt-3">للزوار</h2>
  <ul class="list-disc pr-5 space-y-1">
    <li>{{ site_name }} وسيط إعلاني: الطلب والدفع والتوصيل والاستبدال بينك وبين المحل مباشرة.</li>
    <li>الأسعار والعروض مسؤولية المحل المعلن، وقد تتغير أو تنتهي بدون إشعار.</li>
  </ul>

  <h2 class="text-xl font-extrabold text-white pt-3">للمحلات المعلنة</h2>
  <ul class="list-disc pr-5 space-y-1">
    <li>أول شهر مجاني لكل محل، بدون أي التزام بعده.</li>
    <li>الاشتراك شهري ({{ basic_price | price }} د.ع للأساسية، {{ vip_price | price }} د.ع لـ VIP) ويُدفع مقدماً عبر زين كاش.</li>
    <li>لا يوجد تجديد تلقائي: إذا لم تجدد يُرفع العرض عند انتهاء المدة.</li>
    <li>المبلغ المدفوع لا يُسترد بعد بدء نشر العرض، إلا إذا توقف الموقع عن العمل من طرفنا، فتُعوَّض الأيام المفقودة تمديداً.</li>
    <li>العرض يجب أن يكون حقيقياً: السعر «قبل» هو سعر بيع فعلي، والمنتج متوفر.</li>
    <li>يحق للإدارة رفض أو إزالة أي عرض مضلل أو مخالف للقانون أو للذوق العام.</li>
    <li>عدد النقرات يُحسب مرة لكل زائر، وتُستثنى النقرات المكررة والروبوتات.</li>
  </ul>

  <h2 class="text-xl font-extrabold text-white pt-3">الخصوصية</h2>
  <ul class="list-disc pr-5 space-y-1">
    <li>لا نطلب من الزوار تسجيلاً ولا بيانات شخصية.</li>
    <li>نستعمل ملفات تعريف بسيطة (cookies) لعدّ الزيارات والنقرات بدون تكرار، ولمعرفة المنصة التي جاء منها الزائر.</li>
    <li>رقم التواصل الذي يدخله صاحب المحل في النموذج لا يُعرض للزوار، ويُستعمل للتواصل معه فقط.</li>
    <li>لا نبيع أي بيانات ولا نشاركها مع أي طرف.</li>
  </ul>
</article>
{% endblock %}""",
    "admin_login.html": """{% extends "base.html" %}
{% block title %}دخول الإدارة — {{ site_name }}{% endblock %}
{% block content %}
<form method="post" action="/admin/login" class="max-w-xs mx-auto pt-16 space-y-4">
  <h1 class="text-2xl font-extrabold text-center">🔒 لوحة الإدارة</h1>
  {% if error %}<p class="bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-xl p-3">{{ error }}</p>{% endif %}
  <input type="password" name="password" required autofocus placeholder="كلمة المرور" autocomplete="current-password"
         class="w-full rounded-xl border border-white/15 bg-white/5 text-white placeholder-slate-500 px-3 py-2.5 text-base focus:outline-none focus:ring-2 focus:ring-rose-500">
  <button class="w-full bg-white text-slate-900 font-bold rounded-full py-3">دخول</button>
</form>
{% endblock %}""",
    "admin.html": """{% extends "base.html" %}
{% block title %}لوحة الإدارة — {{ site_name }}{% endblock %}
{% block body %}bg-slate-50 text-slate-800{% endblock %}
{% block ambient %}{% endblock %}
{% set btn = "text-xs font-bold rounded-lg px-3 py-2 border" %}
{% block content %}
<div class="pt-5 flex items-center justify-between">
  <h1 class="text-2xl font-extrabold">لوحة الإدارة</h1>
  <form method="post" action="/admin/logout"><button class="text-sm text-slate-500 underline">تسجيل الخروج</button></form>
</div>

<section class="mt-4 bg-slate-900 text-white rounded-2xl p-4">
  <div class="flex items-baseline justify-between">
    <p class="text-sm text-slate-300">الدخل الشهري من الاشتراكات السارية</p>
    <p class="text-xs text-slate-400">الهدف {{ goal | price }}</p>
  </div>
  <p class="text-3xl font-extrabold mt-1">{{ stats.mrr | price }} <span class="text-sm font-bold">د.ع</span></p>
  <div class="mt-3 h-2.5 bg-white/15 rounded-full overflow-hidden"><div class="h-full bg-amber-400 rounded-full" style="width: {{ goal_percent }}%"></div></div>
  <p class="text-xs text-slate-400 mt-1">{{ goal_percent }}% من الهدف</p>
</section>

<section class="mt-3 grid grid-cols-2 md:grid-cols-5 gap-3 text-center">
  {% for label, value, color in [
      ('كل العروض', stats.total, 'text-slate-900'),
      ('بانتظار الموافقة', stats.pending, 'text-rose-600'),
      ('عروض VIP', stats.vip, 'text-amber-500'),
      ('إجمالي النقرات', stats.clicks, 'text-emerald-600'),
      ('نقرات آخر 30 يوماً', stats.clicks_30d, 'text-emerald-600')] %}
  <div class="bg-white rounded-2xl border border-slate-100 p-3 {{ 'col-span-2 md:col-span-1' if loop.last }}">
    <p class="text-2xl font-extrabold {{ color }}">{{ value | price }}</p>
    <p class="text-xs text-slate-500 mt-1">{{ label }}</p>
  </div>
  {% endfor %}
</section>

<section class="mt-3 bg-white rounded-2xl border border-slate-100 p-4">
  <h2 class="font-extrabold">مصادر الزوار <span class="text-xs font-medium text-slate-400">(آخر 30 يوماً)</span></h2>
  {% if sources %}
  <table class="w-full mt-2 text-sm">
    <tr class="text-xs text-slate-400"><th class="text-right font-medium py-1">المصدر</th><th class="font-medium">زوار</th><th class="font-medium">نقرات على العروض</th></tr>
    {% for label, visitors, clicks in sources %}
    <tr class="border-t border-slate-100"><td class="py-1.5 font-bold">{{ label }}</td><td class="text-center">{{ visitors | price }}</td><td class="text-center text-emerald-600 font-bold">{{ clicks | price }}</td></tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="text-sm text-slate-400 mt-2">لا توجد زيارات مسجّلة بعد.</p>
  {% endif %}
  <p class="text-xs text-slate-400 mt-3">حتى يُحسب المصدر بدقة، أضف لرابط الموقع في كل منصة النهاية الخاصة بها:</p>
  <p dir="ltr" class="text-xs text-slate-500 font-mono text-left mt-1 leading-relaxed">/?src=instagram<br>/?src=tiktok<br>/?src=telegram</p>
</section>

<div class="mt-4 flex items-center gap-2 overflow-x-auto no-scrollbar -mx-4 px-4">
  {% for key, label in [('all', 'الكل'), ('pending', 'بانتظار الموافقة'), ('live', 'منشور'), ('vip', 'VIP'), ('expiring', '⏳ اشتراك ينتهي')] %}
  <button type="button" data-tab="{{ key }}" class="shrink-0 rounded-full px-3.5 py-2 text-sm font-bold border border-slate-200 bg-white text-slate-600">{{ label }}</button>
  {% endfor %}
</div>
<p class="mt-2 text-xs text-slate-400">العروض التي تضيفها أنت من زر «أضف عرض محلك» تُنشر مباشرة.</p>

<section class="mt-3 space-y-3">
  {% for d in deals %}
  <div data-deal data-status="{{ 'live' if d.is_approved else 'pending' }}" data-vip="{{ 1 if d.is_vip else 0 }}"
       data-expiring="{{ 1 if d.paid_until is not none and d.days_left <= warn_days else 0 }}"
       class="bg-white rounded-2xl p-3 border {{ 'border-slate-100' if d.is_approved else 'border-rose-300 bg-rose-50/40' }}">
    <div class="flex gap-3">
      <img src="{{ d.image_url }}" alt="" loading="lazy" class="w-20 h-20 rounded-xl object-cover shrink-0">
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="font-extrabold truncate">{{ d.shop_name }}</span>
          {% if d.is_vip %}<span class="text-[10px] font-extrabold bg-amber-300 text-amber-950 rounded-full px-2 py-0.5">VIP</span>{% endif %}
          {% if not d.is_approved %}<span class="text-[10px] font-extrabold bg-rose-600 text-white rounded-full px-2 py-0.5">بانتظار الموافقة</span>{% endif %}
        </div>
        <p class="text-sm truncate">{{ d.title }}</p>
        <p class="text-xs text-slate-500">{{ d.category }} · {{ d.area }}</p>
        <p class="text-xs mt-0.5"><span class="line-through text-slate-400">{{ d.old_price | price }}</span>
          ← <b class="text-emerald-600">{{ d.new_price | price }} د.ع</b> <span dir="ltr">(-{{ d.discount_percent }}%)</span></p>
      </div>
      <div class="text-center shrink-0">
        <p class="text-xl font-extrabold text-emerald-600">{{ d.clicks_count | price }}</p>
        <p class="text-[10px] text-slate-500">نقرة</p>
        <p class="text-[10px] text-slate-400 mt-1">{{ clicks_30d.get(d.id, 0) }} / 30 يوم</p>
      </div>
    </div>
    <p class="text-[11px] text-slate-400 mt-2 truncate" dir="ltr">{{ d.link_url }}{% if d.contact_phone %} · ☎ {{ d.contact_phone }}{% endif %} · {{ d.created_at.strftime('%Y-%m-%d') }}</p>
    <p class="text-xs font-bold mt-1">
      {% if d.paid_until is none %}<span class="text-slate-400">تجربة مجانية — لا يوجد اشتراك مدفوع</span>
      {% elif d.days_left < 0 %}<span class="text-rose-600">⛔ انتهى الاشتراك منذ {{ -d.days_left }} يوم</span>
      {% elif d.days_left <= warn_days %}<span class="text-amber-600">⏳ الاشتراك ينتهي خلال {{ d.days_left }} يوم</span>
      {% else %}<span class="text-emerald-600">✓ مشترك حتى {{ d.paid_until }} ({{ d.monthly_fee | price }} د.ع)</span>{% endif %}
    </p>
    <div class="mt-2 flex flex-wrap gap-2">
      <form method="post" action="/admin/deals/{{ d.id }}/approve">
        <button class="{{ btn }} {{ 'border-slate-300 text-slate-600' if d.is_approved else 'bg-emerald-600 border-emerald-600 text-white' }}">{{ 'إخفاء' if d.is_approved else '✓ موافقة ونشر' }}</button></form>
      <form method="post" action="/admin/deals/{{ d.id }}/vip">
        <button class="{{ btn }} {{ 'border-amber-400 text-amber-700' if d.is_vip else 'bg-amber-400 border-amber-400 text-amber-950' }}">{{ 'إلغاء VIP' if d.is_vip else '🔥 تفعيل VIP' }}</button></form>
      <form method="post" action="/admin/deals/{{ d.id }}/renew" onsubmit="return confirm('تسجيل دفعة وتمديد الاشتراك 30 يوماً؟')">
        <button class="{{ btn }} border-emerald-300 text-emerald-700">💰 دفع (+30 يوم)</button></form>
      {% if d.contact_wa %}
      <a href="https://wa.me/{{ d.contact_wa }}?text={{ messages[d.id] | urlencode }}" target="_blank" rel="noopener" class="{{ btn }} border-emerald-300 text-emerald-700">📊 أرسل الإحصائية</a>
      {% endif %}
      <button type="button" data-copy="{{ messages[d.id] }}" class="{{ btn }} border-slate-300 text-slate-700">نسخ الرسالة</button>
      <a href="/deal/{{ d.id }}" target="_blank" class="{{ btn }} border-slate-300 text-slate-700">عرض</a>
      <a href="/admin/deals/{{ d.id }}/edit" class="{{ btn }} border-slate-300 text-slate-700">تعديل</a>
      <form method="post" action="/admin/deals/{{ d.id }}/delete" onsubmit="return confirm('حذف هذا العرض نهائياً؟')">
        <button class="{{ btn }} border-rose-300 text-rose-600">حذف</button></form>
    </div>
  </div>
  {% else %}
  <p class="text-center text-slate-500 py-10">لا توجد عروض بعد.</p>
  {% endfor %}
</section>
<script>
(function(){
  var tabs=document.querySelectorAll('[data-tab]'), deals=document.querySelectorAll('[data-deal]');
  function show(key){
    tabs.forEach(function(t){
      var on=t.dataset.tab===key;
      t.classList.toggle('bg-slate-900',on);t.classList.toggle('text-white',on);t.classList.toggle('border-slate-900',on);
      t.classList.toggle('bg-white',!on);t.classList.toggle('text-slate-600',!on);
    });
    deals.forEach(function(d){
      var ok=key==='all'||(key==='vip'?d.dataset.vip==='1':key==='expiring'?d.dataset.expiring==='1':d.dataset.status===key);
      d.classList.toggle('hidden',!ok);
    });
    try{sessionStorage.setItem('adminTab',key)}catch(e){}
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.tab)})});
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-copy]'); if(!b||!navigator.clipboard)return;
    navigator.clipboard.writeText(b.dataset.copy).then(function(){
      var old=b.textContent; b.textContent='تم النسخ ✓'; setTimeout(function(){b.textContent=old},1500);
    });
  });
  var saved='all';try{saved=sessionStorage.getItem('adminTab')||'all'}catch(e){}
  show(saved);
})();
</script>
{% endblock %}""",
}

jinja = Environment(loader=DictLoader(TEMPLATES), autoescape=select_autoescape(default=True))
jinja.filters["price"] = lambda n: f"{int(n):,}"


def render(name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("site_name", SITE_NAME)
    ctx.setdefault("areas", AREAS)
    ctx.setdefault("categories", CATEGORIES)
    ctx.setdefault("category_icons", CATEGORY_ICONS)
    ctx.setdefault("filter_url", filter_url)
    ctx.setdefault("contact_wa", normalize_phone(CONTACT_WHATSAPP))
    ctx.setdefault("basic_price", BASIC_PRICE)
    ctx.setdefault("vip_price", VIP_PRICE)
    return HTMLResponse(jinja.get_template(name).render(**ctx), status_code=status_code)


def filter_url(area: str = "", category: str = "", page: int = 1) -> str:
    params = {k: v for k, v in (("area", area), ("category", category)) if v}
    if page > 1:
        params["page"] = page
    return "/?" + urlencode(params) if params else "/"


# ───────────────────────────── التطبيق ─────────────────────────────

app = FastAPI(title=SITE_NAME, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# رقم العرض في المسار: ضمن حدود عمود INTEGER حتى لا يسبب رقم هائل خطأ في قاعدة البيانات
DealId = Annotated[int, PathParam(ge=1, le=2_147_483_647)]


@app.middleware("http")
async def limits_and_headers(request: Request, call_next):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:  # نرفض قبل أن يُكتب الملف على القرص
        return HTMLResponse("حجم الطلب كبير جداً — الحد الأقصى للصورة 8 ميغابايت.", status_code=413)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    path = request.url.path
    if path.startswith("/uploads/") and response.status_code == 200:
        # أسماء الصور عشوائية ولا تتغير، فيحتفظ بها هاتف الزائر ولا يعيد تحميلها
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
def _bad_request(request: Request, exc: RequestValidationError):
    return render("message.html", 404, emoji="🤷", heading="الصفحة غير موجودة",
                  body="الرابط غير صحيح.")


@app.exception_handler(AdminLoginRequired)
def _admin_redirect(request: Request, exc: AdminLoginRequired):
    return RedirectResponse("/admin/login", status_code=303)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return render("message.html", 404, emoji="🤷", heading="الصفحة غير موجودة",
                      body="قد يكون العرض انتهى أو تم حذفه.")
    return HTMLResponse(str(exc.detail), status_code=exc.status_code)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return {"ok": True}


# ── صفحات ثابتة: للمحلات، من نحن، الشروط ──

@app.get("/join", response_class=HTMLResponse)
def join_page():
    return render("join.html")


@app.get("/about", response_class=HTMLResponse)
def about_page():
    return render("about.html")


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    return render("terms.html")


# ── تطبيق الهاتف (PWA) والأيقونة ──

ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<rect width="100" height="100" rx="22" fill="#0a0a0f"/>'
            '<text x="50" y="68" font-size="58" text-anchor="middle">🔥</text></svg>')


@app.get("/icon.svg")
def icon():
    return Response(ICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": f"{SITE_NAME} — عروض الموصل", "short_name": SITE_NAME, "lang": "ar", "dir": "rtl",
        "start_url": "/", "display": "standalone", "background_color": "#0a0a0f", "theme_color": "#0a0a0f",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    }
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")


# ── محركات البحث ──

@app.get("/robots.txt")
def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    body = f"User-agent: *\nDisallow: /admin\nDisallow: /go/\nDisallow: /submit\nSitemap: {base}/sitemap.xml\n"
    return Response(body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)):
    base = str(request.base_url).rstrip("/")
    ids = db.scalars(select(Deal.id).where(Deal.is_approved.is_(True), live_deal())).all()
    urls = [f"{base}/", f"{base}/join", f"{base}/about", f"{base}/terms"] + [f"{base}/deal/{i}" for i in ids]
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    body += [f"<url><loc>{u}</loc></url>" for u in urls]
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")


# ── الصفحة الرئيسية ──

# HEAD مسموح: خدمات مراقبة التشغيل تفحص الموقع به
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home(request: Request, area: str = "", category: str = "", page: int = 1, q: str = "",
         db: Session = Depends(get_db)):
    page = min(max(page, 1), 1000)
    area = area if area in AREAS else ""
    category = category if category in CATEGORIES else ""
    q = " ".join((q or "").split())[:40]

    stmt = select(Deal).where(Deal.is_approved.is_(True), live_deal())
    if q:
        stmt = stmt.where(or_(Deal.title.ilike(f"%{q}%"), Deal.shop_name.ilike(f"%{q}%")))
    if area == CITYWIDE:
        stmt = stmt.where(Deal.area == CITYWIDE)
    elif area:  # محلات المنطقة + كل من يوصّل لكل الموصل
        stmt = stmt.where(Deal.area.in_([area, CITYWIDE]))
    if category:
        stmt = stmt.where(Deal.category == category)
    # نطلب عرضاً إضافياً واحداً لنعرف هل توجد صفحة تالية
    stmt = (stmt.order_by(Deal.is_vip.desc(), Deal.created_at.desc(), Deal.id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1))
    deals = db.scalars(stmt).all()
    has_next = len(deals) > PAGE_SIZE

    total = db.scalar(select(func.count()).select_from(Deal).where(Deal.is_approved.is_(True), live_deal()))
    resp = render("home.html", deals=deals[:PAGE_SIZE], area=area, category=category, total=total, q=q,
                  prev_url=filter_url(area, category, page - 1) if page > 1 else "",
                  next_url=filter_url(area, category, page + 1) if has_next else "")
    track_visit(request, resp, db)
    return resp


# ── صفحة العرض (رابط خاص قابل للمشاركة مع صورة معاينة) ──

def deal_jsonld(deal: "Deal", base: str) -> str:
    """بيانات منظّمة (schema.org) تجعل جوجل يعرض السعر والتوفّر في نتائج البحث."""
    ld = {
        "@context": "https://schema.org", "@type": "Product",
        "name": deal.title, "brand": {"@type": "Brand", "name": deal.shop_name},
        "description": f"{deal.shop_name} — {deal.title}", "image": base + deal.image_url,
        "offers": {"@type": "Offer", "price": deal.new_price, "priceCurrency": "IQD",
                   "availability": "https://schema.org/InStock", "url": f"{base}/deal/{deal.id}"},
    }
    if deal.ends_on:
        ld["offers"]["priceValidUntil"] = deal.ends_on.isoformat()
    # نهرب "<" حتى لا يُغلق نص مستخدم وسم السكربت
    return json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c")



@app.get("/deal/{deal_id}", response_class=HTMLResponse)
def deal_page(deal_id: DealId, request: Request, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if not deal or not (is_admin(request) or (deal.is_approved and live(deal))):
        raise HTTPException(404)  # العرض المنتهي يُعامل كغير موجود للزوّار: الرسالة تشرح ذلك
    similar = db.scalars(
        select(Deal)
        .where(Deal.is_approved.is_(True), live_deal(), Deal.category == deal.category, Deal.id != deal.id)
        .order_by(Deal.is_vip.desc(), Deal.created_at.desc())
        .limit(4)
    ).all()
    base = str(request.base_url).rstrip("/")
    resp = render("deal.html", deal=deal, similar=similar,
                  og_image=base + deal.image_url, og_url=f"{base}/deal/{deal.id}",
                  jsonld=deal_jsonld(deal, base))
    track_visit(request, resp, db)
    return resp


# ── تتبع النقرات ──

@app.get("/go/{deal_id}")
def go(deal_id: DealId, request: Request, db: Session = Depends(get_db)):
    deal = db.get(Deal, deal_id)
    if not deal or not deal.is_approved or not live(deal):
        raise HTTPException(404)

    # لا نحسب نقرات الأدمن ولا روبوتات المعاينة حتى تبقى الإحصائية صادقة أمام المحل
    # ولا نحسب ضغط الزائر نفسه على العرض نفسه أكثر من مرة خلال 6 ساعات
    resp = RedirectResponse(deal.link_url, status_code=302, headers={"Cache-Control": "no-store"})
    is_bot = bool(BOT_RE.search(request.headers.get("user-agent", "")))
    clicked = [c for c in request.cookies.get(CLICKED_COOKIE, "").split(".") if c.isascii() and c.isdigit()]
    if not is_bot and not is_admin(request) and str(deal_id) not in clicked:
        db.execute(update(Deal).where(Deal.id == deal_id).values(clicks_count=Deal.clicks_count + 1))
        source = clean_source(request.cookies.get(SRC_COOKIE)) or "direct"
        db.add(ClickEvent(deal_id=deal_id, source=source))
        db.commit()
        resp.set_cookie(CLICKED_COOKIE, ".".join((clicked + [str(deal_id)])[-60:]),
                        max_age=6 * 3600, httponly=True, samesite="lax")
    return resp


# ── نموذج «أضف عرض محلك» ──

EMPTY_FORM = dict(shop_name="", title="", category="", area="", old_price="", new_price="",
                  link="", contact_phone="")


def _submit_page(form: dict, errors: list[str], admin: bool, status: int = 200):
    return render(
        "deal_form.html", status, form=form, errors=errors, deal=None, action="/submit",
        heading="أضف عرض محلك",
        intro="" if admin else "أرسل عرضك مجاناً — يظهر في الموقع بعد مراجعة سريعة من الإدارة.",
        submit_label="نشر العرض" if admin else "إرسال العرض للمراجعة",
    )


@app.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request):
    return _submit_page(EMPTY_FORM, [], is_admin(request))


@app.post("/submit", response_class=HTMLResponse)
def submit_deal(
    request: Request,
    shop_name: str = Form(""), title: str = Form(""), category: str = Form(""), area: str = Form(""),
    old_price: str = Form(""), new_price: str = Form(""), link: str = Form(""),
    contact_phone: str = Form(""), website: str = Form(""), ends_on: str = Form(""),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    admin = is_admin(request)
    form = dict(shop_name=shop_name, title=title, category=category, area=area,
                old_price=old_price, new_price=new_price, link=link, contact_phone=contact_phone)
    thanks = dict(emoji="✅", heading="وصلنا عرضك!",
                  body="سيظهر في الموقع بعد مراجعته من الإدارة خلال وقت قصير.")

    if website:  # حقل مخفي لا يملؤه إلا السبام
        return render("message.html", **thanks)

    # حدّان لكل زائر: 10 عروض مقبولة في الساعة، و40 محاولة إجمالاً (معالجة الصور مكلفة على الخادم)
    rate_key = f"submit:{client_ip(request)}"
    attempt_key = f"submit-try:{client_ip(request)}"
    if not admin:
        if too_many(rate_key, limit=10, window=3600) or too_many(attempt_key, limit=40, window=3600):
            return _submit_page(form, ["أرسلت عروضاً كثيرة — حاول مرة أخرى بعد ساعة."], admin, 429)
        record_hit(attempt_key)

    data, errors = validate_deal(shop_name, title, category, area, old_price, new_price, link, contact_phone)
    ends, ends_ok = parse_optional_date(ends_on)
    if not ends_ok or (ends and ends < today()):
        errors.append("تاريخ انتهاء العرض غير صحيح — اختر اليوم أو تاريخاً لاحقاً")
    data["ends_on"] = ends
    if not image or not image.filename:
        errors.append("أضف صورة للعرض")
    if not errors:
        try:
            data["image_path"] = save_image(image)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return _submit_page(form, errors, admin, 400)

    db.add(Deal(**data, is_approved=admin))
    db.commit()
    record_hit(rate_key)

    if admin:
        return RedirectResponse("/admin", status_code=303)
    return render("message.html", **thanks)


# ── لوحة الإدارة ──

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return render("admin_login.html", error="")


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form("")):
    # حدّ لكل عنوان IP، وحدّ إجمالي يصمد حتى لو زوّر المهاجم عنوانه عبر X-Forwarded-For
    rate_key = f"login:{client_ip(request)}"
    if too_many(rate_key, limit=5, window=900) or too_many("login:all", limit=50, window=900):
        return render("admin_login.html", 429, error="محاولات كثيرة — انتظر 15 دقيقة.")
    if not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        record_hit(rate_key)
        record_hit("login:all")
        return render("admin_login.html", 401, error="كلمة المرور غير صحيحة.")

    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(SESSION_COOKIE, make_session_token(), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_dashboard(db: Session = Depends(get_db)):
    deals = db.scalars(
        select(Deal).order_by(Deal.is_approved.asc(), Deal.is_vip.desc(), Deal.created_at.desc())
    ).all()
    cutoff = utcnow() - timedelta(days=30)
    clicks_30d = dict(db.execute(
        select(ClickEvent.deal_id, func.count()).where(ClickEvent.created_at >= cutoff)
        .group_by(ClickEvent.deal_id)
    ).all())
    stats = dict(
        total=len(deals),
        pending=sum(not d.is_approved for d in deals),
        vip=sum(d.is_vip for d in deals),
        clicks=sum(d.clicks_count for d in deals),
        clicks_30d=sum(clicks_30d.values()),
        # الدخل الشهري = اشتراكات سارية لعروض منشورة
        mrr=sum(d.monthly_fee for d in deals if d.is_approved and d.paid_until and d.days_left >= 0),
    )

    # مصادر الزوار والنقرات خلال آخر 30 يوماً
    visits_by_src = dict(db.execute(
        select(Visit.source, func.sum(Visit.count)).where(Visit.day >= today() - timedelta(days=30))
        .group_by(Visit.source)
    ).all())
    clicks_by_src = dict(db.execute(
        select(ClickEvent.source, func.count()).where(ClickEvent.created_at >= cutoff)
        .group_by(ClickEvent.source)
    ).all())
    sources = sorted(
        ((SOURCE_LABELS.get(s, s), int(visits_by_src.get(s, 0)), int(clicks_by_src.get(s, 0)))
         for s in set(visits_by_src) | set(clicks_by_src)),
        key=lambda row: (-row[1], -row[2]),
    )
    messages = {d.id: stats_message(d, clicks_30d.get(d.id, 0)) for d in deals}

    return render("admin.html", deals=deals, stats=stats, clicks_30d=clicks_30d, sources=sources,
                  messages=messages, goal=MONTHLY_GOAL, warn_days=EXPIRY_WARNING_DAYS,
                  goal_percent=min(100, stats["mrr"] * 100 // MONTHLY_GOAL))


def _get_deal(db: Session, deal_id: int) -> Deal:
    deal = db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(404)
    return deal


@app.post("/admin/deals/{deal_id}/approve", dependencies=[Depends(require_admin)])
def admin_toggle_approve(deal_id: DealId, db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    deal.is_approved = not deal.is_approved
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/deals/{deal_id}/vip", dependencies=[Depends(require_admin)])
def admin_toggle_vip(deal_id: DealId, db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    deal.is_vip = not deal.is_vip
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/deals/{deal_id}/renew", dependencies=[Depends(require_admin)])
def admin_renew(deal_id: DealId, db: Session = Depends(get_db)):
    """تسجيل دفعة: يمدّد الاشتراك 30 يوماً من تاريخ انتهائه (أو من اليوم إن كان منتهياً) وينشر العرض."""
    deal = _get_deal(db, deal_id)
    start = max(deal.paid_until or today(), today())
    deal.paid_until = start + timedelta(days=SUBSCRIPTION_DAYS)
    deal.is_approved = True
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/deals/{deal_id}/delete", dependencies=[Depends(require_admin)])
def admin_delete(deal_id: DealId, db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    image = deal.image_path
    # نحذف سجل نقراته معه: SQLite قد يعيد استخدام الرقم لعرض جديد فيرث نقرات لم يحصل عليها
    db.execute(delete(ClickEvent).where(ClickEvent.deal_id == deal_id))
    db.delete(deal)
    db.commit()
    delete_image(image)
    return RedirectResponse("/admin", status_code=303)


def _edit_page(deal: Deal, form: dict, errors: list[str], status: int = 200):
    return render("deal_form.html", status, form=form, errors=errors, deal=deal,
                  action=f"/admin/deals/{deal.id}/edit", heading="تعديل العرض", intro="",
                  submit_label="حفظ التعديلات")


@app.get("/admin/deals/{deal_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_edit_page(deal_id: DealId, db: Session = Depends(get_db)):
    deal = _get_deal(db, deal_id)
    form = dict(shop_name=deal.shop_name, title=deal.title, category=deal.category, area=deal.area,
                old_price=deal.old_price, new_price=deal.new_price, link=deal.link_url,
                contact_phone=deal.contact_phone, is_approved=deal.is_approved, is_vip=deal.is_vip,
                paid_until=deal.paid_until.isoformat() if deal.paid_until else "",
                ends_on=deal.ends_on.isoformat() if deal.ends_on else "")
    return _edit_page(deal, form, [])


@app.post("/admin/deals/{deal_id}/edit", dependencies=[Depends(require_admin)])
def admin_edit(
    deal_id: DealId,
    shop_name: str = Form(""), title: str = Form(""), category: str = Form(""), area: str = Form(""),
    old_price: str = Form(""), new_price: str = Form(""), link: str = Form(""),
    contact_phone: str = Form(""), is_approved: str = Form(""), is_vip: str = Form(""),
    paid_until: str = Form(""), ends_on: str = Form(""),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    deal = _get_deal(db, deal_id)
    form = dict(shop_name=shop_name, title=title, category=category, area=area,
                old_price=old_price, new_price=new_price, link=link, contact_phone=contact_phone,
                is_approved=bool(is_approved), is_vip=bool(is_vip), paid_until=paid_until)

    data, errors = validate_deal(shop_name, title, category, area, old_price, new_price, link, contact_phone)
    try:
        data["paid_until"] = date.fromisoformat(paid_until) if paid_until.strip() else None
    except ValueError:
        errors.append("تاريخ انتهاء الاشتراك غير صحيح")
    ends, ends_ok = parse_optional_date(ends_on)
    if not ends_ok:
        errors.append("تاريخ انتهاء العرض غير صحيح")
    data["ends_on"] = ends
    old_image = None
    if not errors and image and image.filename:
        try:
            data["image_path"] = save_image(image)
            old_image = deal.image_path
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return _edit_page(deal, form, errors, 400)

    for key, value in data.items():
        setattr(deal, key, value)
    deal.is_approved = bool(is_approved)
    deal.is_vip = bool(is_vip)
    db.commit()
    if old_image:
        delete_image(old_image)
    return RedirectResponse("/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")
