import io, os, sys, tempfile, sqlite3
from datetime import timedelta

d = tempfile.mkdtemp()
os.environ["DATA_DIR"] = d
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["ZAINCASH_NUMBER"] = "07700000001"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

# old-schema DB (as created by the first version) to exercise the migration
con = sqlite3.connect(os.path.join(d, "deals.db"))
con.executescript("""
CREATE TABLE deals (id INTEGER PRIMARY KEY, shop_name VARCHAR(80), title VARCHAR(140), category VARCHAR(40),
 area VARCHAR(40), old_price INTEGER, new_price INTEGER, image_path VARCHAR(120), link_url VARCHAR(500),
 contact_phone VARCHAR(30), is_approved BOOLEAN, is_vip BOOLEAN, clicks_count INTEGER, created_at DATETIME);
CREATE TABLE click_events (id INTEGER PRIMARY KEY, deal_id INTEGER, created_at DATETIME);
INSERT INTO deals VALUES (1,'محل قديم','عرض قديم من النسخة الأولى','إلكترونيات','الدواسة',10000,7000,'x.webp',
 'https://wa.me/9647701234567','07701234567',1,0,5,'2026-11-01 10:00:00.000000');
INSERT INTO click_events VALUES (1,1,'2026-11-05 10:00:00.000000');
""")
con.commit(); con.close()

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

with main.SessionLocal() as db:
    old = db.get(main.Deal, 1)
    check("migration: old deal readable, paid_until None", old.paid_until is None and old.clicks_count == 5)
    check("migration: old click got source", db.query(main.ClickEvent).first().source == "direct")

adm = client()
r = adm.post("/admin/login", data={"password": "test-pass-123"})
adm.cookies.set("admin_session", r.cookies["admin_session"])
adm.post("/submit", data=dict(shop_name="برغر الموصل", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
         old_price="12000", new_price="8000", link="@burger", contact_phone="0770 123 4567"),
         files={"image": ("a.png", png(), "image/png")})

# deal page
a = client()
r = a.get("/deal/2?src=instagram")
check("deal page 200 + og:image absolute", r.status_code == 200 and 'og:image" content="http://testserver/uploads/' in r.text)
check("deal page has order button + price", "/go/2" in r.text and "8,000" in r.text)
check("src + visitor cookies set", r.cookies.get("src") == "instagram" and r.cookies.get("v") == "1")
a.cookies.set("src", "instagram"); a.cookies.set("v", "1")
a.get("/"); a.get("/go/2")
b = client(); b.get("/", headers={**UA, "referer": "https://www.tiktok.com/@x"})
c = client(); c.get("/")
client().get("/", headers={"user-agent": "Googlebot"})
with main.SessionLocal() as db:
    v = {x.source: x.count for x in db.query(main.Visit).all()}
    check("visits by source " + str(v), v == {"instagram": 1, "tiktok": 1, "direct": 1})
    ev = db.query(main.ClickEvent).filter_by(deal_id=2).one()
    check("click attributed to instagram", ev.source == "instagram")
check("home links to deal page", "/deal/2" in c.get("/").text)
check("missing deal 404", c.get("/deal/999").status_code == 404)

# admin: sources, message, renew
h = adm.get("/admin").text
check("admin shows sources", "إنستغرام" in h and "تيك توك" in h and "مباشر" in h)
check("stats message w/ zaincash + wa link", "07700000001" in h and "https://wa.me/9647701234567?text=" in h)
check("free trial label, mrr 0", "تجربة مجانية" in h and "0% من الهدف" in h)
check("renew", adm.post("/admin/deals/2/renew").status_code == 303)
with main.SessionLocal() as db:
    dl = db.get(main.Deal, 2)
    # paying during the free month extends from the END of the trial, so no free days are lost
    check("renew during trial = trial end + 30", dl.trial_until == main.today() + timedelta(days=30)
          and dl.paid_until == main.today() + timedelta(days=60) and dl.days_left == 60)
adm.post("/admin/deals/2/renew")
adm.post("/admin/deals/2/vip")
with main.SessionLocal() as db:
    check("second renew stacks to +90", db.get(main.Deal, 2).days_left == 90)
h = adm.get("/admin").text
check("mrr = 15,000 (vip)", "15,000 <span" in h and "1% من الهدف" in h and "مشترك حتى" in h)

# edit paid_until → expiring soon / expired
soon = (main.today() + timedelta(days=3)).isoformat()
form = dict(shop_name="برغر الموصل", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
            old_price="12000", new_price="8000", link="https://instagram.com/burger", contact_phone="07701234567",
            is_approved="1", paid_until=soon)
check("edit page shows date", 'type="date"' in adm.get("/admin/deals/2/edit").text)
check("edit paid_until", adm.post("/admin/deals/2/edit", data=form).status_code == 303)
h = adm.get("/admin").text
check("expiring warning", "ينتهي خلال 3 يوم" in h and 'data-expiring="1"' in h)
form["paid_until"] = (main.today() - timedelta(days=2)).isoformat()
adm.post("/admin/deals/2/edit", data=form)
h = adm.get("/admin").text
check("expired label + excluded from mrr", "انتهى الاشتراك منذ 2 يوم" in h and "0% من الهدف" in h)
form["paid_until"] = "garbage"
check("bad date rejected", adm.post("/admin/deals/2/edit", data=form).status_code == 400)
form["paid_until"] = ""
adm.post("/admin/deals/2/edit", data=form)
with main.SessionLocal() as db:
    check("empty date clears subscription", db.get(main.Deal, 2).paid_until is None)

adm.post("/admin/deals/2/approve")
check("hidden deal: public 404, admin 200", client().get("/deal/2").status_code == 404 and adm.get("/deal/2").status_code == 200)
check("admin visits not counted", sum(1 for _ in [0]) and main.SessionLocal().query(main.Visit).count() == 3)

# ── review fixes ──
adm.post("/admin/deals/2/approve")  # publish again
r = client().get("/")
check("security headers", r.headers.get("x-content-type-options") == "nosniff" and r.headers.get("x-frame-options") == "DENY")
check("gzip on html", r.headers.get("content-encoding") == "gzip")
check("admin pages no-store", adm.get("/admin").headers.get("cache-control") == "no-store")
img = r.text.split('src="/uploads/')[1].split('"')[0]
check("uploads cached long", "immutable" in client().get("/uploads/" + img).headers.get("cache-control", ""))
check("oversized body 413", client().post("/submit", content=b"x", headers={**UA, "content-length": str(50 * 1024 * 1024)}).status_code == 413)
check("huge id -> 404 page, not 500", client().get("/deal/99999999999999999999").status_code == 404 and client().get("/go/abc").status_code == 404)
check("bad page param -> 404", client().get("/?page=abc").status_code == 404)
check("non-ascii session cookie safe", client().get("/admin", headers={**UA, "cookie": b"admin_session=9999999999.\xe9\xe8"}).status_code == 303)

main.PAGE_SIZE = 1
p1 = client().get("/"); p2 = client().get("/?page=2")
check("pagination: page1 has next, no prev", "page=2" in p1.text and "السابق" not in p1.text)
check("pagination: page2 different deal + prev", "السابق" in p2.text and ("/deal/2" in p1.text) != ("/deal/2" in p2.text))
check("pagination: beyond end is empty state", "لا توجد عروض" in client().get("/?page=50").text)
main.PAGE_SIZE = 40

check("ig username with dot", main.normalize_link("burger.house") == "https://instagram.com/burger.house"
      and main.normalize_link("@noor.iq") == "https://instagram.com/noor.iq"
      and main.normalize_link("myshop.com") == "https://myshop.com")
big = io.BytesIO(); Image.new("L", (9000, 9000)).save(big, "PNG"); big.seek(0)
r = adm.post("/submit", data=dict(shop_name="xx", title="yyy zzz", category="إلكترونيات", area="الزهور", old_price="9000",
             new_price="5000", link="07701234567"), files={"image": ("b.png", big, "image/png")})
check("pixel-bomb image rejected", r.status_code == 400 and "أبعاد الصورة" in r.text)
r = adm.post("/submit", data=dict(shop_name="xx", title="yyy zzz", category="إلكترونيات", area="الزهور",
             old_price="99999999999999", new_price="5000", link="07701234567"), files={"image": ("a.png", png(), "image/png")})
check("absurd old price rejected", r.status_code == 400)

with main.SessionLocal() as db:
    before = db.query(main.ClickEvent).filter_by(deal_id=2).count()
adm.post("/admin/deals/2/delete")
with main.SessionLocal() as db:
    check("delete removes its click events", before == 1 and db.query(main.ClickEvent).filter_by(deal_id=2).count() == 0)

# session survives restart without SECRET_KEY, dies when the password changes
tok = main.make_session_token()
import importlib
importlib.reload(main)
from starlette.requests import Request as R
def req(t): return R({"type": "http", "headers": [(b"cookie", f"admin_session={t}".encode())]})
check("session valid after restart", main.is_admin(req(tok)))
os.environ["ADMIN_PASSWORD"] = "another-pass"
importlib.reload(main)
check("session invalid after password change", not main.is_admin(req(tok)))

print(f"\n{sum(ok)}/{len(ok)} passed"); sys.exit(0 if all(ok) else 1)
