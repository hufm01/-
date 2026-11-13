"""اختبارات: مشاهدات العروض ونسبة الطلب، الإيقاف المؤقت من بوابة المحل، صفحات المناطق والفئات لمحركات البحث."""
import io, os, sys, tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_ADMIN_CHAT", "DATABASE_URL"):
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
    b = io.BytesIO(); Image.new("RGB", (200, 200), "red").save(b, "PNG"); b.seek(0); return b
UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)
def keep(c, r, name):
    if name in r.cookies:
        v = r.cookies[name]; c.cookies.delete(name); c.cookies.set(name, v)
def views(i):
    return main.views_since(main.SessionLocal(), 30).get(i, 0)
def deal_form(**kw):
    d = dict(title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور", old_price="12000", new_price="8000", link="@burger")
    d.update(kw); return d

adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")

# shop with one approved deal
s = client(); keep(s, s.post("/shop/register", data={"name": "برغر الموصل", "phone": "07701234567", "password": "secret-pass-1"}), "shop_session")
adm.post("/admin/shops/1/verify")
s.post("/shop/deals/new", data=deal_form(), files={"image": ("a.png", png(), "image/png")})
adm.post("/admin/deals/1/approve")

# ── views ──
v1 = client()
v1.get("/deal/1"); v1.get("/deal/1")
check("visitor view counted once per 6h", views(1) == 1)
client().get("/deal/1")
check("second visitor counted", views(1) == 2)
client().get("/deal/1", headers={"user-agent": "Googlebot/2.1"})
adm.get("/deal/1"); s.get("/deal/1")
check("bots, admin and the owner are not counted", views(1) == 2)
c = client(); c.get("/go/1")
check("ordering straight from the card counts as a view", views(1) == 3)
c.get("/deal/1")
check("...but not twice when they then open the page", views(1) == 3)
for _ in range(8): client().get("/deal/1")
h = s.get("/shop").text
check("portal shows views and request rate", "11 مشاهدة" in h and "نسبة الطلب" in h and ">9%<" in h)
check("admin sees views", "👁 11" in adm.get("/admin").text)

# ── pause / resume ──
r = s.post("/shop/deals/1/pause")
check("pause hides from public", r.headers["location"] == "/shop?ok=paused" and "برغر الموصل" not in client().get("/").text
      and client().get("/deal/1").status_code == 404 and client().get("/go/1").status_code == 404)
check("owner can still preview paused deal", s.get("/deal/1").status_code == 200)
check("portal and admin label paused", "موقوف مؤقتاً" in s.get("/shop").text and "أوقفه المحل" in adm.get("/admin").text)
check("paused deal not in sitemap or stats", "/deal/1<" not in client().get("/sitemap.xml").text and "بالطريق" in client().get("/").text)
s.post("/shop/deals/1/pause")
check("resume shows it again", "برغر الموصل" in client().get("/").text and client().get("/deal/1").status_code == 200)
o = client(); keep(o, o.post("/shop/register", data={"name": "محل آخر", "phone": "07809998877", "password": "other-pass-22"}), "shop_session")
check("other shop cannot pause my deal", o.post("/shop/deals/1/pause").status_code == 404 and not main.SessionLocal().get(main.Deal, 1).paused)

# ── deleting a deal removes its view history ──
adm.post("/admin/deals/1/delete")
check("views deleted with deal", main.SessionLocal().query(main.DealView).filter_by(deal_id=1).count() == 0)

# ── SEO: area/category pages ──
h = client().get("/", params={"area": "الزهور", "category": "مطاعم وكافيهات"}).text
check("filter page has its own title", "<title>عروض مطاعم وكافيهات في الزهور — لقطة</title>" in h)
check("filter page has its own description", 'content="أقوى عروض مطاعم وكافيهات في الزهور اليوم' in h)
check("canonical drops sort and page", 'rel="canonical" href="http://testserver/?area=' in client().get("/", params={"area": "الزهور", "sort": "price", "page": 2}).text
      and "sort=" not in client().get("/", params={"area": "الزهور", "sort": "price"}).text.split('rel="canonical"')[1][:120])
check("citywide wording", "عروض وتخفيضات مع توصيل لكل الموصل" in client().get("/", params={"area": main.CITYWIDE}).text)
check("search results not indexed", 'name="robots" content="noindex"' in client().get("/", params={"q": "برغر"}).text
      and 'noindex' not in client().get("/").text)
check("plain home keeps default title", "<title>لقطة — أقوى عروض وتخفيضات الموصل اليومية</title>" in client().get("/").text)
sm = client().get("/sitemap.xml").text
check("sitemap lists every area and category", sm.count("?area=") == len(main.AREAS) and sm.count("?category=") == len(main.CATEGORIES))
import xml.dom.minidom
try:
    xml.dom.minidom.parseString(sm.encode()); valid = True
except Exception:
    valid = False
check("sitemap is valid XML", valid)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
