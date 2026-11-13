import io, os, sys, tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image
import main

def png():
    b = io.BytesIO(); Image.new("RGB", (2000, 1500), "red").save(b, "PNG"); b.seek(0); return b

def form(**kw):
    d = dict(shop_name="برغر الموصل", title="وجبة دبل برغر مع بطاطا", category="مطاعم وكافيهات",
             area="الزهور", old_price="٢٥,٠٠٠", new_price="18000", link="07701234567", contact_phone="0770")
    d.update(kw); return d

pub = TestClient(main.app, follow_redirects=False)
adm = TestClient(main.app, follow_redirects=False)
ok = []
def check(name, cond):
    ok.append(cond); print(("PASS" if cond else "FAIL"), name)

check("home empty 200", pub.get("/").status_code == 200)
check("admin redirects to login", pub.get("/admin").status_code == 303)
check("admin POST blocked", pub.post("/admin/deals/1/delete").headers.get("location") == "/admin/login")

r = pub.post("/submit", data=form(), files={"image": ("a.png", png(), "image/png")})
check("public submit 200", r.status_code == 200 and "وصلنا عرضك" in r.text)
check("pending hidden on home", "برغر الموصل" not in pub.get("/").text)
check("go pending 404", pub.get("/go/1").status_code == 404)

r = pub.post("/submit", data=form(new_price="30000"), files={"image": ("a.png", png(), "image/png")})
check("bad price rejected", r.status_code == 400 and "أقل من السعر السابق" in r.text)
r = pub.post("/submit", data=form(link="javascript:alert(1)"), files={"image": ("a.png", png(), "image/png")})
check("js link rejected", r.status_code == 400)
r = pub.post("/submit", data=form(), files={"image": ("a.png", io.BytesIO(b"not an image"), "image/png")})
check("fake image rejected", r.status_code == 400 and "ليس صورة" in r.text)
r = pub.post("/submit", data=form())
check("missing image rejected", r.status_code == 400 and "أضف صورة" in r.text)
r = pub.post("/submit", data=form(website="spam"), files={"image": ("a.png", png(), "image/png")})
check("honeypot silently dropped", r.status_code == 200)

check("wrong password 401", adm.post("/admin/login", data={"password": "x"}).status_code == 401)
r = adm.post("/admin/login", data={"password": "test-pass-123"})
check("login ok", r.status_code == 303 and "admin_session" in r.cookies)
adm.cookies.set("admin_session", r.cookies["admin_session"])
r = adm.get("/admin")
check("dashboard shows 1 pending", r.status_code == 200 and "بانتظار الموافقة" in r.text and r.text.count("/edit") == 1)

check("approve", adm.post("/admin/deals/1/approve").status_code == 303)
h = pub.get("/").text
check("approved shows on home w/ discount 28%", "برغر الموصل" in h and "-28%" in h and "25,000" in h)
img = h.split('src="/uploads/')[1].split('"')[0]
r = pub.get("/uploads/" + img)
check("image served, resized webp", r.status_code == 200 and max(Image.open(io.BytesIO(r.content)).size) == 1200)

r = pub.get("/go/1")
check("go redirects to whatsapp with a ready message", r.status_code == 302
      and r.headers["location"].startswith("https://wa.me/9647701234567?text="))
pub.get("/go/1"); pub.get("/go/1")  # same visitor again: must not count
TestClient(main.app, follow_redirects=False).get("/go/1")  # a different visitor: counts
pub.get("/go/1", headers={"user-agent": "facebookexternalhit/1.1"}); adm.get("/go/1")
with main.SessionLocal() as db:
    check("clicks = 2 (repeat+bot+admin ignored)", db.get(main.Deal, 1).clicks_count == 2)
check("dashboard 30d clicks", "2 / 30 يوم" in adm.get("/admin").text)

# admin submit → auto-approved; VIP ordering
r = adm.post("/submit", data=form(shop_name="بوتيك لمسة", area="توصيل لكل الموصل", category="ملابس وأحذية",
             link="@lamsa.boutique"), files={"image": ("a.png", png(), "image/png")})
check("admin submit auto-approved", r.status_code == 303 and "بوتيك لمسة" in pub.get("/").text)
adm.post("/admin/deals/1/vip")
h = pub.get("/").text
check("VIP first + badge", h.index("برغر الموصل") < h.index("بوتيك لمسة") and "عرض مميز" in h)
check("ig link", pub.get("/go/2").headers["location"] == "https://instagram.com/lamsa.boutique")

h = pub.get("/", params={"area": "الكرامة"}).text
check("area filter includes citywide only", "بوتيك لمسة" in h and "برغر الموصل" not in h)
h = pub.get("/", params={"category": "مطاعم وكافيهات"}).text
check("category filter", "برغر الموصل" in h and "بوتيك لمسة" not in h)

check("edit page", adm.get("/admin/deals/1/edit").status_code == 200)
r = adm.post("/admin/deals/1/edit", data=form(title="عرض معدّل جديد", is_approved="1", link="https://wa.me/9647701234567"))
h = pub.get("/").text
check("edit saved, VIP unchecked removed", r.status_code == 303 and "عرض معدّل جديد" in h and "عرض مميز" not in h)
check("edit kept image", img in h)

check("delete", adm.post("/admin/deals/1/delete").status_code == 303)
check("deleted gone + image removed", "برغر الموصل" not in pub.get("/").text and pub.get("/uploads/" + img).status_code == 404)
check("404 page", pub.get("/nope").status_code == 404 and "غير موجودة" in pub.get("/nope").text)
adm.post("/admin/logout"); adm.cookies.clear()
check("logged out", adm.get("/admin").status_code == 303)
for _ in range(5): pub.post("/admin/login", data={"password": "x"})
check("login rate limit", pub.post("/admin/login", data={"password": "test-pass-123"}).status_code == 429)

print(f"\n{sum(ok)}/{len(ok)} passed"); sys.exit(0 if all(ok) else 1)
