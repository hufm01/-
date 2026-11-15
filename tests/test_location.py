"""موقع المحل: تحديد بالـ GPS أو رابط غوغل ماب من البوابة والإدارة، وزر الاتجاهات والخريطة في صفحة العرض."""
import io, os, re, sys, tempfile
from urllib.parse import urlparse

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["LAUNCH_DATE"] = "2000-01-01"
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

# ── parser ──
P = main.parse_location
check("pin in long google maps link wins over map view",
      P("https://www.google.com/maps/place/X/@36.30,43.10,17z/data=!3m1!4b1!8m2!3d36.3467!4d43.1299") == (36.3467, 43.1299))
check("@lat,lng link", P("https://www.google.com/maps/@36.3456,43.1234,15z") == (36.3456, 43.1234))
check("?q= and query=%2C links", P("https://maps.google.com/?q=36.35,43.14") == (36.35, 43.14)
      and P("https://www.google.com/maps/search/?api=1&query=36.36%2C43.15") == (36.36, 43.15))
check("plain coordinates", P(" 36.34 , 43.13 ") == (36.34, 43.13))
check("swapped / outside Iraq / short link / junk refused",
      P("43.13, 36.34") is None and P("51.5, -0.12") is None and P("https://maps.app.goo.gl/abc") is None
      and P("مطعم") is None and P("") is None)

# ── setup: admin adds a shop + deal, owner claims ──
adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")
adm.post("/admin/shops/new", data={"name": "برغر الموصل", "phone": "07701234567"})
adm.post("/submit", data=dict(shop_id="1", shop_name="", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
                              old_price="12000", new_price="8000", link="07701234567", contact_phone=""),
         files={"image": ("a.png", png(), "image/png")})
link = re.search(r"https?://\S+/shop/reset/[\w-]+", adm.post("/admin/shops/1/reset").text).group(0)
o = client(); keep(o, o.post(urlparse(link).path, data={"password": "owner-pass-1"}), "shop_session")

pub = client()
check("no location: no directions button or map", "اذهب للمحل" not in pub.get("/deal/1").text and "output=embed" not in pub.get("/deal/1").text)
check("portal shows the location section open", 'id="location"' in o.get("/shop").text and "حدّد موقعي" in o.get("/shop").text)

# ── GPS from the portal ──
r = o.post("/shop/location", data={"lat": "36.3401234", "lng": "43.1309876"})
with main.SessionLocal() as db:
    s = db.get(main.Shop, 1)
check("GPS saved (rounded)", r.headers["location"].startswith("/shop?ok=loc") and (s.lat, s.lng) == (36.340123, 43.130988))
page = pub.get("/deal/1").text
check("deal page has directions + map", "google.com/maps/dir/?api=1&amp;destination=36.340123,43.130988" in page
      and "output=embed" in page and "اذهب للمحل" in page)
check("portal confirms with a map", "محدد ✓" in o.get("/shop?ok=loc").text and "تم حفظ موقع المحل" in o.get("/shop?ok=loc").text)

check("GPS outside Iraq refused, old location kept",
      o.post("/shop/location", data={"lat": "48.85", "lng": "2.35"}).headers["location"].startswith("/shop?ok=locbad"))
with main.SessionLocal() as db:
    check("location unchanged after bad input", db.get(main.Shop, 1).lat == 36.340123)
check("garbage GPS refused", o.post("/shop/location", data={"lat": "abc", "lng": "1"}).headers["location"].startswith("/shop?ok=locbad"))

# ── pasted link ──
o.post("/shop/location", data={"maps": "https://www.google.com/maps/@36.35,43.15,16z"})
with main.SessionLocal() as db:
    s = db.get(main.Shop, 1)
check("pasted google maps link saved", (s.lat, s.lng) == (36.35, 43.15))

# ── clear ──
o.post("/shop/location", data={"clear": "1"})
with main.SessionLocal() as db:
    check("location cleared", db.get(main.Shop, 1).lat is None)
check("cleared: button gone", "اذهب للمحل" not in pub.get("/deal/1").text)

# ── auth ──
client().post("/shop/location", data={"lat": "36.3", "lng": "43.1"})
with main.SessionLocal() as db:
    check("anonymous cannot set location", db.get(main.Shop, 1).lat is None)
check("non-admin cannot use admin endpoint", client().post("/admin/shops/1/location", data={"maps": "36.3, 43.1"}).status_code in (303, 401, 403))

# ── admin sets it ──
r = adm.post("/admin/shops/1/location", data={"maps": "36.3333, 43.1111"})
with main.SessionLocal() as db:
    check("admin saves location", r.headers["location"] == "/admin#shop-1" and db.get(main.Shop, 1).lat == 36.3333)
check("admin bad link shows error", adm.post("/admin/shops/1/location", data={"maps": "https://maps.app.goo.gl/x"}).headers["location"].startswith("/admin?err=locbad"))
check("admin list links the location", "📍 الموقع" in adm.get("/admin").text)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
