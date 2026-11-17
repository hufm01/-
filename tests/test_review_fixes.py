"""أخطاء وُجدت في مراجعة 18 نوفمبر: كل فحص هنا كان يفشل قبل الإصلاح."""
import io, os, re, sys, tempfile
from datetime import timedelta
from urllib.parse import urlparse

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["LAUNCH_DATE"] = "2000-01-01"
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_ADMIN_CHAT", "DATABASE_URL", "ALLOW_ANONYMOUS_SUBMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image, ImageFont
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
def deal_form(**kw):
    d = dict(title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
             old_price="12000", new_price="8000", link="07701234567", ends_on="")
    d.update(kw); return d
def add_deal(c, **kw):
    return c.post("/shop/deals/new", data=deal_form(**kw), files={"image": ("a.png", png(), "image/png")})

adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")

# ── 1) shop added by admin, owner claims and publishes the first deal himself ──
adm.post("/admin/shops/new", data={"name": "برغر الموصل", "phone": "07701234567"})
link = re.search(r"https?://\S+/shop/reset/[\w-]+", adm.post("/admin/shops/1/reset").text).group(0)
o = client(); keep(o, o.post(urlparse(link).path, data={"password": "owner-pass-1"}), "shop_session")
r = add_deal(o)
with main.SessionLocal() as db:
    s, d = db.get(main.Shop, 1), db.get(main.Deal, 1)
check("trusted shop's own first deal is live", r.headers["location"] == "/shop?ok=deal" and d.is_approved)
check("...and its free month started (not free forever)", s.trial_until == main.today() + timedelta(days=30)
      and d.trial_until == s.trial_until)
check("portal shows the free month, not 'not started'", "الشهر المجاني" in o.get("/shop").text)

# ── 2) unpublished-deal banner wording ──
with main.SessionLocal() as db:
    db.get(main.Deal, 1).is_approved = False; db.commit()
check("owner sees 'awaiting review', not 'you are the admin'",
      "ينتظر مراجعة الإدارة" in o.get("/deal/1").text and "مسجّل كمدير" not in o.get("/deal/1").text)
check("admin still sees the admin wording", "مسجّل كمدير" in adm.get("/deal/1").text)
with main.SessionLocal() as db:
    db.get(main.Deal, 1).is_approved = True; db.commit()

# ── 3) story fonts use the basic layout (no double bidi if raqm appears on the server) ──
check("story fonts force basic layout", main._font(40).layout_engine == ImageFont.Layout.BASIC
      and main._latin_font(40).layout_engine == ImageFont.Layout.BASIC)

# ── 4) "most requested" badge ignores hidden deals ──
with main.SessionLocal() as db:
    for i in range(3):  # three hidden deals with lots of clicks
        h = main.Deal(shop_name="مخفي", title=f"عرض مخفي {i}", category="مطاعم وكافيهات", area="الزهور",
                      old_price=10000, new_price=5000, image_path="x.webp", link_url="https://instagram.com/x",
                      is_approved=False)
        db.add(h); db.flush()
        db.add_all([main.ClickEvent(deal_id=h.id) for _ in range(10)])
    db.add_all([main.ClickEvent(deal_id=1) for _ in range(4)])
    db.commit()
check("live deal gets the 'most requested' badge despite busier hidden deals", "الأكثر طلباً" in client().get("/").text)

# ── 5) plan limits ──
with main.SessionLocal() as db:
    s = db.get(main.Shop, 1)
    s.is_vip, s.paid_until = True, main.today() + timedelta(days=20)
    main.sync_shop_deals(db, s); db.commit()
add_deal(o, title="عرض ثاني"); add_deal(o, title="عرض ثالث")
with main.SessionLocal() as db:
    mine = db.scalars(main.select(main.Deal).where(main.Deal.shop_id == 1)).all()
check("VIP shop runs 3 deals", len(mine) == 3 and not any(d.paused for d in mine))

# pays for basic: only the newest stays running
adm_pay = o.post("/shop/pay", data={"plan": "basic", "txn_ref": "123456"})
with main.SessionLocal() as db:
    pid = db.scalar(main.select(main.Payment.id))
adm.post(f"/admin/payments/{pid}/approve")
with main.SessionLocal() as db:
    mine = db.scalars(main.select(main.Deal).where(main.Deal.shop_id == 1).order_by(main.Deal.id)).all()
    running = [d.title for d in mine if not d.paused]
check("downgrade to basic pauses extra deals, keeps the newest", running == ["عرض ثالث"])
r = o.post("/shop/deals/1/pause")
with main.SessionLocal() as db:
    still = db.get(main.Deal, 1).paused
check("cannot un-pause past the plan limit", r.headers["location"] == "/shop?ok=limit" and still)
check("limit message shown", "باقتك تسمح بعدد محدود" in o.get("/shop?ok=limit").text)
with main.SessionLocal() as db:
    running_id = db.scalar(main.select(main.Deal.id).where(main.Deal.shop_id == 1, main.Deal.paused.is_(False)))
o.post(f"/shop/deals/{running_id}/pause")  # pause the running one
r = o.post("/shop/deals/1/pause")  # now there is room
with main.SessionLocal() as db:
    check("can swap which deal runs", r.headers["location"] == "/shop?ok=resumed" and not db.get(main.Deal, 1).paused)

# admin toggles VIP off: same rule
with main.SessionLocal() as db:
    s = db.get(main.Shop, 1); s.is_vip = True
    for d in db.scalars(main.select(main.Deal).where(main.Deal.shop_id == 1)).all():
        d.paused = False
    db.commit()
adm.post("/admin/deals/1/vip")
with main.SessionLocal() as db:
    n_running = sum(not d.paused for d in db.scalars(main.select(main.Deal).where(main.Deal.shop_id == 1)).all())
check("admin removing VIP also applies the basic limit", n_running == 1)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
