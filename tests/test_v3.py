"""اختبارات: تاريخ انتهاء العرض، البحث، محركات البحث، تطبيق الهاتف، البيانات المنظّمة."""
import io, os, sys, tempfile
from datetime import timedelta

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ.pop("DATABASE_URL", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image
import main

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name)

def png():
    b = io.BytesIO(); Image.new("RGB", (300, 300), "red").save(b, "PNG"); b.seek(0); return b

UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)

def form(**kw):
    d = dict(shop_name="برغر الموصل", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
             old_price="12000", new_price="8000", link="@burger", contact_phone="07701234567", ends_on="")
    d.update(kw); return d

adm = client()
r = adm.post("/admin/login", data={"password": "test-pass-123"})
adm.cookies.set("admin_session", r.cookies["admin_session"])

# submit: past end date rejected, future accepted (admin auto-publishes)
past = (main.today() - timedelta(days=1)).isoformat()
future = (main.today() + timedelta(days=10)).isoformat()
r = adm.post("/submit", data=form(ends_on=past), files={"image": ("a.png", png(), "image/png")})
check("past end date rejected", r.status_code == 400 and "تاريخ انتهاء" in r.text)
r = adm.post("/submit", data=form(ends_on=future), files={"image": ("a.png", png(), "image/png")})
check("future end date accepted", r.status_code == 303)
r = adm.post("/submit", data=form(shop_name="محل ثاني", title="عرض قهوة", category="مطاعم وكافيهات",
             ends_on="not-a-date"), files={"image": ("a.png", png(), "image/png")})
check("garbage end date rejected", r.status_code == 400)

# search
adm.post("/submit", data=form(shop_name="بوتيك لمسة", title="فستان سهرة تركي", category="ملابس وأحذية",
         area="الكرامة", link="@lamsa"), files={"image": ("a.png", png(), "image/png")})
h = client().get("/", params={"q": "فستان"}).text
check("search finds by title", "فستان سهرة تركي" in h and "برغر الموصل" not in h)
h = client().get("/", params={"q": "برغر"}).text
check("search finds by shop", "برغر الموصل" in h and "بوتيك لمسة" not in h)
check("search keeps filters in hidden inputs", 'name="area" value="الكرامة"' in client().get("/", params={"q": "x", "area": "الكرامة"}).text)
check("search over-long query trimmed", client().get("/", params={"q": "أ" * 500}).status_code == 200)

# expiry: a deal whose end date passed disappears from public lists and detail
with main.SessionLocal() as db:
    d = db.scalar(main.select(main.Deal).where(main.Deal.shop_name == "محل ثاني")) or None
    gone = db.scalar(main.select(main.Deal).where(main.Deal.shop_name == "بوتيك لمسة"))
    gone.ends_on = main.today() - timedelta(days=2)
    db.commit(); gone_id = gone.id
check("expired deal hidden from home", "بوتيك لمسة" not in client().get("/").text)
check("expired deal 404 publicly", client().get(f"/deal/{gone_id}").status_code == 404)
check("expired deal still visible to admin", adm.get(f"/deal/{gone_id}").status_code == 200)

with main.SessionLocal() as db:
    soon = db.scalar(main.select(main.Deal).where(main.Deal.shop_name == "برغر الموصل"))
    soon.ends_on = main.today() + timedelta(days=2)
    db.commit(); soon_id = soon.id
check("ends-soon label on card", "ينتهي بعد 2 يوم" in client().get("/").text)

# edit page round-trips ends_on
check("edit page shows ends_on", 'name="ends_on" value="' in adm.get(f"/admin/deals/{soon_id}/edit").text)

# SEO / PWA routes
r = client().get("/robots.txt")
check("robots blocks admin and links sitemap", r.status_code == 200 and "Disallow: /admin" in r.text and "/sitemap.xml" in r.text)
r = client().get("/sitemap.xml")
check("sitemap lists home and live deals only", r.status_code == 200 and "<loc>http://testserver/</loc>" in r.text
      and f"/deal/{soon_id}" in r.text and f"/deal/{gone_id}" not in r.text)
r = client().get("/manifest.webmanifest")
check("manifest valid json, rtl, standalone", r.status_code == 200 and r.json()["dir"] == "rtl" and r.json()["display"] == "standalone")
check("icon svg served", client().get("/icon.svg").headers["content-type"].startswith("image/svg+xml"))
check("home links manifest + skip link", 'rel="manifest"' in client().get("/").text and 'href="#main"' in client().get("/").text)

# JSON-LD: valid JSON, price, and hostile title cannot break out of the script tag
with main.SessionLocal() as db:
    x = db.get(main.Deal, soon_id)
    x.title = "عرض </script><script>alert(1)</script>"
    db.commit()
h = client().get(f"/deal/{soon_id}").text
start = h.index('type="application/ld+json">') + len('type="application/ld+json">')
payload = h[start:h.index("</script>", start)]
import json as _json
ld = _json.loads(payload)
check("json-ld parses with price + currency", ld["offers"]["price"] == 8000 and ld["offers"]["priceCurrency"] == "IQD")
check("json-ld priceValidUntil set", "priceValidUntil" in ld["offers"])
check("hostile title does not inject script", "<script>alert" not in h.split("</head>")[0])

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
