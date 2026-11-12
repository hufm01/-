"""اختبارات الأتمتة: دورة التجربة والاشتراك، تيليغرام، الترتيب، شارة الأكثر طلباً، أرقام الواجهة."""
import io, os, sys, tempfile
from datetime import timedelta

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:TEST-TOKEN"
os.environ["TELEGRAM_CHANNEL"] = "@test_channel"
os.environ["TELEGRAM_ADMIN_CHAT"] = "999"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image
import main

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name)

# never touch the real Telegram API: capture calls instead
sent = []
def fake_call(method, payload):
    sent.append((method, payload)); return True
main.telegram_call = fake_call

def png():
    b = io.BytesIO(); Image.new("RGB", (200, 200), "red").save(b, "PNG"); b.seek(0); return b

UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)
def form(**kw):
    d = dict(shop_name="برغر الموصل", title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور",
             old_price="12000", new_price="8000", link="@burger", contact_phone="07701234567")
    d.update(kw); return d
def submit(c, **kw): return c.post("/submit", data=form(**kw), files={"image": ("a.png", png(), "image/png")})
def deal(i):
    with main.SessionLocal() as db: return db.get(main.Deal, i)
def patch(i, **kw):
    with main.SessionLocal() as db:
        d = db.get(main.Deal, i)
        for k, v in kw.items(): setattr(d, k, v)
        db.commit()

T = main.today()
h = client().get("/").text
check("empty site shows launch state, no filters", "بالطريق" in h and 'name="q"' not in h)

# ── public submit: pending, no trial yet, admin notified ──
submit(client())
check("public submit: no trial before approval", deal(1).trial_until is None and not deal(1).is_approved)
check("admin notified on telegram", sent and sent[-1][0] == "sendMessage" and sent[-1][1]["chat_id"] == "999"
      and "برغر الموصل" in sent[-1][1]["text"] and "/admin" in sent[-1][1]["text"])

adm = client()
r = adm.post("/admin/login", data={"password": "test-pass-123"})
adm.cookies.set("admin_session", r.cookies["admin_session"])

# ── approval starts the free month and posts to the channel, once ──
sent.clear()
adm.post("/admin/deals/1/approve")
d = deal(1)
check("approve starts 30-day trial", d.trial_until == T + timedelta(days=30) and d.paid_until is None)
check("approve posts photo to channel", len(sent) == 1 and sent[0][0] == "sendPhoto"
      and sent[0][1]["chat_id"] == "@test_channel" and sent[0][1]["photo"].startswith("http://testserver/uploads/")
      and "/deal/1?src=telegram" in sent[0][1]["caption"] and "8,000" in sent[0][1]["caption"])
check("marked as posted", d.tg_posted)
adm.post("/admin/deals/1/approve"); adm.post("/admin/deals/1/approve")  # hide, show again
check("re-approve does not repost or reset trial", len(sent) == 1 and deal(1).trial_until == T + timedelta(days=30))
check("no token leaks into page", "TEST-TOKEN" not in adm.get("/admin").text)

# ── auto-hide when the free month ends (after the grace period) ──
patch(1, trial_until=T - timedelta(days=main.GRACE_DAYS))
check("within grace: still visible", "برغر الموصل" in client().get("/").text and client().get("/go/1").status_code == 302)
patch(1, trial_until=T - timedelta(days=main.GRACE_DAYS + 1))
check("after grace: hidden from home", "برغر الموصل" not in client().get("/").text)
check("after grace: page and order link 404", client().get("/deal/1").status_code == 404 and client().get("/go/1").status_code == 404)
h = adm.get("/admin").text
check("admin sees auto-hidden + trial ended", "مخفي تلقائياً" in h and "انتهت التجربة المجانية" in h)

# ── payment brings it back instantly ──
adm.post("/admin/deals/1/renew")
d = deal(1)
check("renew after expiry counts from today", d.paid_until == T + timedelta(days=30))
check("visible again after payment", "برغر الموصل" in client().get("/").text and client().get("/deal/1").status_code == 200)
check("mrr counts paid deal", "6,000 <span" in adm.get("/admin").text)

# ── paid subscription lapses → hidden; trial date no longer matters ──
patch(1, paid_until=T - timedelta(days=10), trial_until=T + timedelta(days=20))
check("lapsed paid sub hides deal even with later trial date", "برغر الموصل" not in client().get("/").text)
check("lapsed sub not counted in mrr", "0% من الهدف" in adm.get("/admin").text)

# ── admin can make a deal permanent by clearing both dates ──
adm.post("/admin/deals/1/edit", data=form(link="https://instagram.com/burger", is_approved="1", paid_until="", trial_until=""))
d = deal(1)
check("cleared dates = permanent and visible", d.paid_until is None and d.trial_until is None and "برغر الموصل" in client().get("/").text)
check("admin label permanent", "دائم" in adm.get("/admin").text)
check("edit page has both date fields", 'name="trial_until"' in adm.get("/admin/deals/1/edit").text)

# ── admin's own submissions: published, trial started, posted ──
sent.clear()
submit(adm, shop_name="بوتيك لمسة", title="فستان سهرة", category="ملابس وأحذية", area="الكرامة", old_price="100000", new_price="30000", link="@lamsa")
d = deal(2)
check("admin submit: live + trial + posted", d.is_approved and d.trial_until == T + timedelta(days=30) and len(sent) == 1 and d.tg_posted)
submit(adm, shop_name="موبايلات النور", title="سماعة بلوتوث", category="إلكترونيات", area="الدواسة", old_price="20000", new_price="18000", link="@noor")

# ── telegram failure must not break approval, and retries later ──
main.telegram_call = lambda m, p: False
submit(client(), shop_name="كافيه ليالي", title="لاتيه وكيك")
r = adm.post("/admin/deals/4/approve")
check("approval works when telegram is down", r.status_code == 303 and deal(4).is_approved and not deal(4).tg_posted)
main.telegram_call = fake_call; sent.clear()
adm.post("/admin/deals/4/renew")
check("posted on next publish action", len(sent) == 1 and deal(4).tg_posted)

# ── homepage numbers are computed, not typed ──
h = client().get("/").text
check("stats: 4 deals, 4 shops, best discount 70%", ">4</dd>" in h and "70%</dd>" in h and "محل وبيج" in h)

# ── sorting ──
def order(sort):
    t = client().get("/", params={"sort": sort}).text
    return sorted(["برغر الموصل", "بوتيك لمسة", "موبايلات النور", "كافيه ليالي"], key=t.index)
check("sort by discount", order("discount")[0] == "بوتيك لمسة" and order("discount")[-1] == "موبايلات النور")
check("sort by price", order("price")[0] in ("برغر الموصل", "كافيه ليالي") and order("price")[-1] == "بوتيك لمسة")
check("unknown sort falls back safely", client().get("/", params={"sort": "'; drop"}).status_code == 200)
check("sort survives in filter links", "sort=discount" in client().get("/", params={"sort": "discount"}).text.split("ملابس")[0])
adm.post("/admin/deals/3/vip")
check("VIP stays first under any sort", order("discount")[0] == "موبايلات النور" and order("price")[0] == "موبايلات النور")

# ── "most requested" badge appears only from real clicks ──
check("no hot badge without clicks", "الأكثر طلباً" not in client().get("/").text)
for _ in range(main.HOT_MIN_CLICKS):
    client().get("/go/2")
h = client().get("/").text
check("hot badge after enough clicks", h.count("📈 الأكثر طلباً") == 1)
for _ in range(5):
    client().get("/go/2")
check("social proof on deal page", "شخص طلبوا هذا العرض" in client().get("/deal/2").text)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
