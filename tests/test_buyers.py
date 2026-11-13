"""اختبارات: متابعة المحلات، قناة لقطة اليوم، كود لقطة ورسالة الواتساب، بلّغ عن لقطة،
التقرير الأسبوعي، تنبيه الأخطاء، والنسخ الاحتياطي اليومي."""
import io, os, sys, tarfile, tempfile
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:TEST-TOKEN"
os.environ["TELEGRAM_BOT_USERNAME"] = "lagta_test_bot"
os.environ["TELEGRAM_CHANNEL"] = "@lagta_mosul"
os.environ["TELEGRAM_ADMIN_CHAT"] = "999"
os.environ["PUBLIC_URL"] = "https://lagta.test"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from PIL import Image
import main

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name)

sent = []
main.telegram_call = lambda method, payload: (sent.append((method, payload)), True)[1]
docs = []
main.tg_send_document = lambda chat, name, data, caption="": (docs.append((chat, name, data, caption)), True)[1]
def to(chat): return [p for m, p in sent if str(p.get("chat_id")) == str(chat)]
def txt(p): return p.get("text") or p.get("caption") or ""

def png():
    b = io.BytesIO(); Image.new("RGB", (200, 200), "red").save(b, "PNG"); b.seek(0); return b
UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)
def keep(c, r, name):
    if name in r.cookies:
        v = r.cookies[name]; c.cookies.delete(name); c.cookies.set(name, v)
def hook(update): return client().post(f"/tg/webhook/{main.WEBHOOK_SECRET}", json=update)
def say(chat, text): return hook({"message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "text": text}})
def deal_form(**kw):
    d = dict(title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور", old_price="12000", new_price="8000", link="07701234567")
    d.update(kw); return d

adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")
s = client(); keep(s, s.post("/shop/register", data={"name": "برغر الموصل", "phone": "07701234567", "password": "secret-pass-1"}), "shop_session")
adm.post("/admin/shops/1/verify")
s.post("/shop/deals/new", data=deal_form(), files={"image": ("a.png", png(), "image/png")})
sent.clear()
adm.post("/admin/deals/1/approve")

# ── channel button ──
h = client().get("/").text
check("home shows channel button", 'href="https://t.me/lagta_mosul"' in h and "لقطة اليوم" in h)

# ── follow a shop ──
h = client().get("/deal/1").text
check("deal page has follow button", "https://t.me/lagta_test_bot?start=f1" in h and "تابع برغر الموصل" in h)
say(700, "/start f1"); say(700, "/start f1"); say(701, "/start f1")
with main.SessionLocal() as db:
    n = db.query(main.ShopFollower).filter_by(shop_id=1).count()
check("followers recorded once each", n == 2 and "صرت تتابع" in txt(to(700)[-1]))
say(702, "/start f99999")
check("unknown shop id falls back to welcome", main.SessionLocal().query(main.ShopFollower).filter_by(chat_id="702").count() == 0)
check("portal shows follower count", "2 متابع" in s.get("/shop").text)

# shop is trusted now: a new deal reaches followers once, and the old one did not
check("approved first deal was not sent to followers before they followed", not [p for p in to(700) if "عرض جديد من محل تتابعه" in txt(p)])
adm.post("/admin/shops/1/trust")  # make sure trusted (approve already did)
adm.post("/admin/shops/1/trust")
adm.post("/admin/deals/1/vip")  # VIP to allow more deals
with main.SessionLocal() as db:
    sh = db.get(main.Shop, 1); main.renew_shop(db, sh, vip=True); db.commit()
sent.clear()
s.post("/shop/deals/new", data=deal_form(title="بيتزا عائلية"), files={"image": ("a.png", png(), "image/png")})
f700 = [p for p in to(700) if "عرض جديد من محل تتابعه" in txt(p)]
check("new deal sent to each follower", len(f700) == 1 and len([p for p in to(701) if "تتابعه" in txt(p)]) == 1
      and "src=follow" in txt(f700[0]))
main.announce_deal(2, "https://lagta.test")
check("never sent twice", len([p for p in to(700) if "تتابعه" in txt(p)]) == 1)
say(700, "/stop")
check("/stop unfollows", main.SessionLocal().query(main.ShopFollower).filter_by(chat_id="700").count() == 0)

# ── WhatsApp order message + Lagta code ──
r = client().get("/go/1")
q = parse_qs(urlparse(r.headers["location"]).query)
check("whatsapp opens with a ready message naming the site", r.headers["location"].startswith("https://wa.me/9647701234567?text=")
      and "على لقطة" in q["text"][0] and "وجبة دبل برغر" in q["text"][0] and "كود" not in q["text"][0])
check("no code box before shop sets an offer", "كود لقطة" not in client().get("/deal/1").text)
s.post("/shop/code", data={"code_offer": "خصم 5% إضافي"})
h = client().get("/deal/1").text
check("deal page shows code and offer", "LAGTA1" in h and "خصم 5% إضافي" in h)
r = client().get("/go/1")
check("whatsapp message includes the code", "LAGTA1" in parse_qs(urlparse(r.headers["location"]).query)["text"][0])
s.post("/shop/code/sales", data={"count": "3"}); s.post("/shop/code/sales", data={"count": "2"})
check("code sales logged", "5 زبون قال الكود" in s.get("/shop").text)
check("bad sales count ignored", s.post("/shop/code/sales", data={"count": "5000"}).status_code == 303
      and "5 زبون قال الكود" in s.get("/shop").text)
with main.SessionLocal() as db:
    d = db.get(main.Deal, 1); d.link_url = "https://instagram.com/burger"; db.commit()
check("non-whatsapp links untouched", client().get("/go/1").headers["location"] == "https://instagram.com/burger")

# ── tips ──
sent.clear()
r = client().post("/tip", data={"shop_name": "كافيه السطح", "where": "instagram.com/sateh", "note": "لاتيه بنص السعر"})
check("tip accepted and admin alerted", r.status_code == 200 and "شكراً" in r.text
      and any("كافيه السطح" in txt(p) for p in to(999)))
check("tip validation", client().post("/tip", data={"shop_name": "x", "where": ""}).status_code == 400)
check("honeypot tip dropped", client().post("/tip", data={"shop_name": "spam", "where": "spam.com", "website": "x"}).status_code == 200
      and main.SessionLocal().query(main.DealTip).count() == 1)
h = adm.get("/admin").text
check("admin lists tips", "محلات اقترحها الزوار" in h and "كافيه السطح" in h)
adm.post("/admin/tips/1/done")
check("tip dismissed", main.SessionLocal().query(main.DealTip).count() == 0)
check("footer links tip page", 'href="/tip"' in client().get("/").text)

# ── weekly report ──
with main.SessionLocal() as db:
    sh = db.get(main.Shop, 1); sh.tg_chat_id = "555"; sh.created_at = main.utcnow() - timedelta(days=10); db.commit()
sent.clear()
check("weekly report sent", main.run_weekly_reports() == 1)
rep = txt(to(555)[-1])
check("report has views, clicks, followers, code sales", "تقرير برغر الموصل الأسبوعي" in rep and "مشاهدة" in rep
      and "اطلب العرض" in rep and "متابع" in rep and "LAGTA1" in rep and "https://lagta.test/shop" in rep)
check("not repeated within the week", main.run_weekly_reports() == 0)

# ── error alert ──
@main.app.get("/__boom")
def _boom():
    raise RuntimeError("test explosion")
sent.clear()
r = TestClient(main.app, raise_server_exceptions=False, headers=UA).get("/__boom")
import time; time.sleep(0.3)
check("friendly 500 page", r.status_code == 500 and "حدث خطأ مؤقت" in r.text)
check("admin alerted once", any("test explosion" in txt(p) for p in to(999)))
TestClient(main.app, raise_server_exceptions=False, headers=UA).get("/__boom"); time.sleep(0.3)
check("same error throttled", len([p for p in to(999) if "test explosion" in txt(p)]) == 1)

# ── daily backup ──
name, data = main.build_backup()
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    names = tar.getnames()
check("backup has database and images", name.startswith("lagta-backup-") and "deals.db" in names
      and any(n.startswith("uploads/") for n in names))
docs.clear()
check("backup sent to admin chat", main.run_daily_backup() and docs[0][0] == "999" and "نسخة احتياطية" in docs[0][3])

# ── deleting the shop removes followers and code sales ──
say(800, "/start f1")
adm.post("/admin/shops/1/delete")
with main.SessionLocal() as db:
    check("shop deletion cleans followers and code sales", db.query(main.ShopFollower).count() == 0 and db.query(main.CodeSale).count() == 0)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
