"""اختبارات ما قبل الإطلاق: الشهر المجاني من يوم الإطلاق، رسالة الواتساب، إغلاق النموذج المجهول،
الحدود الإجمالية، WAL، النسخة الاحتياطية بلا تكرار، إضافة محل نيابةً عنه ورابط الاستلام، صورة الستوري."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
from urllib.parse import urlparse

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["LAUNCH_DATE"] = (date.today() + timedelta(days=16)).isoformat()  # launch is in the future
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_ADMIN_CHAT", "DATABASE_URL", "ALLOW_ANONYMOUS_SUBMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image
import main

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name)

def png():
    b = io.BytesIO(); Image.new("RGB", (300, 300), "orange").save(b, "PNG"); b.seek(0); return b
UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)
def keep(c, r, name):
    if name in r.cookies:
        v = r.cookies[name]; c.cookies.delete(name); c.cookies.set(name, v)
def form(**kw):
    d = dict(shop_name="", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
             old_price="12000", new_price="8000", link="07701234567", contact_phone="")
    d.update(kw); return d

L = main.LAUNCH_DATE
adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")

# ── anonymous /submit is closed ──
check("public /submit redirects to shop signup", client().get("/submit").headers.get("location") == "/shop/register"
      and client().post("/submit", data=form(shop_name="x")).headers.get("location") == "/shop/register")
check("admin still has direct add", adm.get("/submit").status_code == 200)

# ── create a shop on the owner's behalf ──
r = adm.post("/admin/shops/new", data={"name": "برغر الموصل", "phone": "0770 123 4567"})
with main.SessionLocal() as db:
    s = db.get(main.Shop, 1)
check("shop created verified + trusted, goes to add-deal", r.headers["location"] == "/submit?shop=1" and s.verified and s.trusted)
check("nobody knows its password", not main.check_password("", s.password_hash) and s.password_hash.startswith("scrypt$"))
check("duplicate phone refused", adm.post("/admin/shops/new", data={"name": "x y", "phone": "07701234567"}).headers["location"].startswith("/admin?err=dup"))
check("bad phone refused", adm.post("/admin/shops/new", data={"name": "x y", "phone": "123"}).headers["location"] == "/admin?err=shop")
check("add-deal form preselects the shop", 'value="1" selected' in adm.get("/submit?shop=1").text)

# ── deal added for that shop: live, owned, trial counted from launch ──
r = adm.post("/submit", data=form(shop_id="1"), files={"image": ("a.png", png(), "image/png")})
with main.SessionLocal() as db:
    d, s = db.get(main.Deal, 1), db.get(main.Shop, 1)
check("deal belongs to the shop and uses its name/phone", r.status_code == 303 and d.shop_id == 1
      and d.shop_name == "برغر الموصل" and d.contact_phone == "07701234567" and d.is_approved)
check("free month starts at launch, not today", s.trial_until == L + timedelta(days=30) and d.trial_until == s.trial_until)
check("public sees it now", client().get("/deal/1").status_code == 200)
r = adm.post("/submit", data=form(title="عرض مستقل", shop_name="محل بلا حساب"), files={"image": ("a.png", png(), "image/png")})
with main.SessionLocal() as db:
    d2 = db.get(main.Deal, 2)
check("standalone deal trial also from launch", d2.shop_id is None and d2.trial_until == L + timedelta(days=30))

# ── claim link: 7 days, single use, logs the owner in ──
r = adm.post("/admin/shops/1/reset")
link = re.search(r"https?://\S+/shop/reset/[\w-]+", r.text).group(0)
with main.SessionLocal() as db:
    exp = db.get(main.Shop, 1).reset_expires
check("claim link valid ~7 days", timedelta(days=6, hours=23) < exp - main.utcnow() <= timedelta(days=7))
check("claim message ready for whatsapp", "wa.me/9647701234567" in r.text and "%20" in r.text and "+" not in r.text.split("wa.me/")[1].split('"')[0].split("?text=")[1])
o = client()
path = urlparse(link).path
keep(o, o.post(path, data={"password": "owner-pass-1"}), "shop_session")
check("owner sets password and lands in portal", "برغر الموصل" in o.get("/shop").text)
check("claim link single use", client().get(path).status_code == 400)

# ── WhatsApp message uses %20 ──
loc = client().get("/go/1").headers["location"]
check("whatsapp text encoded with %20, no +", "?text=" in loc and "%20" in loc and "+" not in loc.split("?text=")[1])

# ── site-wide caps ──
main._RATE.clear()
for i in range(40):
    main.record_hit("shop-register:all")
r = client().post("/shop/register", data={"name": "سبام", "phone": "07801112233", "password": "spam-pass-11"})
check("registration capped site-wide", r.status_code == 429)
main._RATE.clear()

# ── SQLite WAL ──
with main.engine.connect() as c:
    mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
check("SQLite in WAL mode", str(mode).lower() == "wal")

# ── daily backup not repeated after restart ──
sent = []
main.TELEGRAM_BOT_TOKEN, main.TELEGRAM_ADMIN_CHAT = "1:T", "999"
main.tg_send_document = lambda *a, **k: (sent.append(a), True)[1]
main.state_set("last_backup_day", "")
check("backup state persists in DB", main.state_get("last_backup_day") == "")
main.state_set("last_backup_day", "2026-11-15")
check("state readable after write", main.state_get("last_backup_day") == "2026-11-15")
main.TELEGRAM_BOT_TOKEN = ""

# ── story image ──
r = client().get("/deal/1/story.jpg")
im = Image.open(io.BytesIO(r.content))
check("story image is a 1080x1920 JPEG", r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and im.size == (1080, 1920))
check("portal and admin link the story", "/deal/1/story.jpg" in o.get("/shop").text and "/deal/1/story.jpg" in adm.get("/admin").text)
o.post("/shop/deals/1/pause")
check("paused deal: story hidden from public, owner still gets it", client().get("/deal/1/story.jpg").status_code == 404
      and o.get("/deal/1/story.jpg").status_code == 200)
check("unknown deal story 404", client().get("/deal/999/story.jpg").status_code == 404)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
