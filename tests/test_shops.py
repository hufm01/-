"""اختبارات بوابة المحلات: التسجيل، تأكيد الرقم عبر بوت تيليغرام، العروض، الاشتراك، الاسترجاع، العزل بين المحلات."""
import io, os, re, sys, tempfile
from datetime import timedelta

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:TEST-TOKEN"
os.environ["TELEGRAM_BOT_USERNAME"] = "lagta_test_bot"
os.environ["TELEGRAM_CHANNEL"] = "@test_channel"
os.environ["TELEGRAM_ADMIN_CHAT"] = "999"
os.environ["PUBLIC_URL"] = "https://lagta.test"
os.environ["ZAINCASH_NUMBER"] = "07700000001"
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
def texts(chat=None): return [p.get("text", p.get("caption", "")) for m, p in sent if chat is None or str(p["chat_id"]) == str(chat)]

def png(color="red"):
    b = io.BytesIO(); Image.new("RGB", (200, 200), color).save(b, "PNG"); b.seek(0); return b

UA = {"user-agent": "Mozilla/5.0 (iPhone)"}
def client(): return TestClient(main.app, follow_redirects=False, headers=UA)
def keep(c, r, name):
    if name in r.cookies:
        value = r.cookies[name]
        c.cookies.delete(name)  # the client may already hold it under another domain
        c.cookies.set(name, value)
    return r
def shop_row(i):
    with main.SessionLocal() as db: return db.get(main.Shop, i)
def deal_row(i):
    with main.SessionLocal() as db: return db.get(main.Deal, i)
def patch(model, i, **kw):
    with main.SessionLocal() as db:
        o = db.get(model, i)
        for k, v in kw.items(): setattr(o, k, v)
        db.commit()
def hook(update, secret=None):
    return client().post(f"/tg/webhook/{secret or main.WEBHOOK_SECRET}", json=update)
def start(chat, arg=""):
    return hook({"message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat}, "text": f"/start {arg}".strip()}})
def share(chat, phone, user_id=None):
    return hook({"message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat},
                             "contact": {"phone_number": phone, "user_id": chat if user_id is None else user_id}}})
def deal_form(**kw):
    d = dict(title="وجبة دبل برغر", category="مطاعم وكافيهات", area="الزهور", old_price="12000", new_price="8000", link="@burger")
    d.update(kw); return d
def add_deal(c, **kw): return c.post("/shop/deals/new", data=deal_form(**kw), files={"image": ("a.png", png(), "image/png")})

T = main.today()

# ── registration ──
a = client()
check("portal requires login", a.get("/shop").headers.get("location") == "/shop/login")
r = a.post("/shop/register", data={"name": "برغر الموصل", "phone": "0770 123 4567", "password": "short"})
check("short password rejected", r.status_code == 400)
r = a.post("/shop/register", data={"name": "برغر الموصل", "phone": "12345", "password": "secret-pass-1"})
check("bad phone rejected", r.status_code == 400)
r = keep(a, a.post("/shop/register", data={"name": "برغر الموصل", "phone": "0770 123 4567", "password": "secret-pass-1"}), "shop_session")
s = shop_row(1)
check("registered, logged in, phone normalized", r.status_code == 303 and s.phone == "9647701234567" and not s.verified)
check("password stored hashed, never plain", s.password_hash.startswith("scrypt$") and "secret-pass-1" not in s.password_hash)
r = client().post("/shop/register", data={"name": "منتحل", "phone": "+9647701234567", "password": "another-pass"})
check("duplicate phone rejected", r.status_code == 400 and "مسجّل من قبل" in r.text)

h = a.get("/shop").text
check("unverified sees telegram verify link", f"https://t.me/lagta_test_bot?start=v{s.verify_code}" in h and "أكّد رقم هاتفك" in h)
check("unverified cannot add deals or pay", a.get("/shop/deals/new").status_code == 303 and add_deal(a).status_code == 303
      and a.get("/shop/pay").status_code == 303 and deal_row(1) is None)

# ── telegram verification ──
check("webhook rejects wrong secret", hook({"message": {}}, secret="wrong").status_code == 404)
sent.clear(); start(555, "v" + s.verify_code)
check("bot asks to share contact", shop_row(1).tg_chat_id == "555" and sent[-1][1]["reply_markup"]["keyboard"][0][0]["request_contact"])
share(555, "+9647809999999")
check("different number refused", not shop_row(1).verified and "لا يطابق" in texts(555)[-1])
share(555, "9647701234567", user_id=777)
check("someone else's contact card refused", not shop_row(1).verified)
share(555, "+964 770 123 4567")
check("own matching number verifies", shop_row(1).verified and shop_row(1).verify_code == "" and "تم تأكيد حسابك" in texts(555)[-1])
sent.clear(); start(555, "vdeadbeef0000")
check("stale code cannot hijack", shop_row(1).tg_chat_id == "555")
hook({"message": {"chat": {"id": -100, "type": "group"}, "text": "/start"}})
check("group chats ignored", not texts(-100))

# ── first deal: reviewed; approval makes shop trusted and starts trial ──
sent.clear()
r = add_deal(a)
d = deal_row(1)
check("first deal pending, owned by shop, uses shop name", r.headers["location"] == "/shop?ok=pending" and d.shop_id == 1
      and not d.is_approved and d.shop_name == "برغر الموصل" and d.contact_phone == "07701234567")
check("admin notified of pending deal", any("بانتظار موافقتك" in t for t in texts(999)))
check("pending not public", client().get("/deal/1").status_code == 404)
check("basic limit = 1 deal", add_deal(a, title="عرض ثاني").status_code == 303 and deal_row(2) is None)

adm = client()
keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")
h = adm.get("/admin").text
check("admin lists shop", "المحلات المسجّلة" in h and "07701234567" in h)
sent.clear()
adm.post("/admin/deals/1/approve")
s, d = shop_row(1), deal_row(1)
check("approval: shop trusted + trial on shop and deal", s.trusted and s.trial_until == T + timedelta(days=30) and d.trial_until == s.trial_until)
check("shop notified + posted to channel", any("تم قبول عرضك" in t for t in texts(555)) and any(m == "sendPhoto" for m, p in sent))
check("deal public", client().get("/deal/1").status_code == 200)

# ── portal dashboard: live stats ──
for _ in range(3): client().get("/go/1")
h = a.get("/shop").text
check("portal shows clicks and status", "● منشور" in h and ">3</p>" in h and "الشهر المجاني" in h and "باقي 30 يوم" in h)

# ── trusted shop edits publish instantly ──
sent.clear()
r = a.post("/shop/deals/1/edit", data=deal_form(new_price="7000"))
d = deal_row(1)
check("trusted edit live at once, image kept", r.headers["location"] == "/shop?ok=deal" and d.new_price == 7000 and d.is_approved and d.image_path)
check("admin informed of edit", any("عدّل محل موثوق" in t for t in texts(999)))
r = a.post("/shop/deals/1/edit", data=deal_form(new_price="99999"))
check("invalid edit rejected", r.status_code == 400 and deal_row(1).new_price == 7000)

# ── isolation between shops ──
b = client()
keep(b, b.post("/shop/register", data={"name": "بوتيك لمسة", "phone": "07809998877", "password": "other-pass-22"}), "shop_session")
check("other shop cannot open, edit or delete my deal", b.get("/shop/deals/1/edit").status_code == 404
      and b.post("/shop/deals/1/edit", data=deal_form(title="مخترق")).status_code == 404
      and b.post("/shop/deals/1/delete").status_code == 404 and deal_row(1).title == "وجبة دبل برغر")
check("other shop sees none of my data", "وجبة دبل برغر" not in b.get("/shop").text)
forged = a.cookies.get("shop_session").split(".")
b2 = client(); b2.cookies.set("shop_session", f"2.{forged[1]}.{forged[2]}")
check("forged shop id in cookie rejected", b2.get("/shop").headers.get("location") == "/shop/login")

# ── manual verification by admin (shop without telegram) ──
adm.post("/admin/shops/2/verify")
check("manual verify works and keeps link code", shop_row(2).verified and shop_row(2).verify_code)
start(888, "v" + shop_row(2).verify_code)
check("verified shop can link telegram later", shop_row(2).tg_chat_id == "888" and shop_row(2).verify_code == "")

# ── payment request → admin approval ──
check("pay page shows zaincash number and both plans", "07700000001" in a.get("/shop/pay").text and "15,000" in a.get("/shop/pay").text)
check("payment needs ref or proof", a.post("/shop/pay", data={"plan": "vip"}).status_code == 400)
sent.clear()
r = a.post("/shop/pay", data={"plan": "vip", "txn_ref": "TX-778899"}, files={"proof": ("p.png", png("blue"), "image/png")})
with main.SessionLocal() as db: pay = db.get(main.Payment, 1)
check("payment recorded pending", r.headers["location"] == "/shop?ok=paid" and pay.status == "pending" and pay.amount == 15000)
check("admin alerted", any("طلب اشتراك" in t and "TX-778899" in t for t in texts(999)))
check("second request blocked while pending", a.post("/shop/pay", data={"plan": "basic", "txn_ref": "X"}).status_code == 303
      and main.SessionLocal().query(main.Payment).count() == 1)
check("not activated before approval", not shop_row(1).is_paid and "قيد المراجعة" in a.get("/shop").text)
check("proof private: public 404, shop 303, admin 200", client().get(f"/uploads/{pay.proof_path}").status_code == 404
      and a.get(f"/admin/proofs/{pay.proof_path}").status_code == 303 and adm.get(f"/admin/proofs/{pay.proof_path}").status_code == 200)
check("admin sees pending payment", "دفعات تنتظر تأكيدك" in adm.get("/admin").text and "TX-778899" in adm.get("/admin").text)
check("shop cannot approve own payment", a.post("/admin/payments/1/approve").headers.get("location") == "/admin/login" and not shop_row(1).is_paid)
sent.clear()
adm.post("/admin/payments/1/approve")
s, d = shop_row(1), deal_row(1)
check("approval extends from trial end, sets VIP, syncs deals", s.paid_until == T + timedelta(days=60) and s.is_vip and d.is_vip and d.paid_until == s.paid_until)
check("shop notified of activation", any("تم تأكيد دفعتك" in t for t in texts(555)))
check("payment cannot be approved twice", adm.post("/admin/payments/1/approve").status_code == 404 and shop_row(1).paid_until == T + timedelta(days=60))
check("mrr counts shop once", "15,000 <span" in adm.get("/admin").text)

# ── VIP limit = 3 ──
add_deal(a, title="عرض ثاني"); add_deal(a, title="عرض ثالث")
check("VIP allows 3 deals, published directly", deal_row(3) is not None and deal_row(3).is_approved and deal_row(3).is_vip)
check("4th deal blocked", add_deal(a, title="عرض رابع").status_code == 303 and deal_row(4) is None)
h = client().get("/").text
check("shop counted once in homepage stats", "محل وبيج" in h and ">1</dd>" in h)

# ── expiry hides all shop deals; rejected payment; renewal restores ──
patch(main.Shop, 1, paid_until=T - timedelta(days=10))
with main.SessionLocal() as db: main.sync_shop_deals(db, db.get(main.Shop, 1)); db.commit()
check("lapsed shop: all deals hidden", "برغر الموصل" not in client().get("/").text and "انتهى الاشتراك" in a.get("/shop").text)
a.post("/shop/pay", data={"plan": "basic", "txn_ref": "BAD-1"})
sent.clear(); adm.post("/admin/payments/2/reject")
check("rejection notifies and does not activate", not shop_row(1).is_paid and any("لم نتمكن" in t for t in texts(555)))
a.post("/shop/pay", data={"plan": "basic", "txn_ref": "GOOD-2"})
adm.post("/admin/payments/3/approve")
check("renewal after lapse counts from today, deals back", shop_row(1).paid_until == T + timedelta(days=30) and "برغر الموصل" in client().get("/").text)

# ── reminders ──
patch(main.Shop, 1, paid_until=T + timedelta(days=3), reminded_on=None)
sent.clear()
check("reminder sent 3 days before", main.run_reminders() == 1 and "ينتهي بعد 3 أيام" in texts(555)[-1] and "https://lagta.test/shop/pay" in texts(555)[-1])
check("not repeated same day", main.run_reminders() == 0)

# ── login / logout / password reset ──
c = client()
check("wrong password refused", c.post("/shop/login", data={"phone": "07701234567", "password": "nope-nope"}).status_code == 401)
r = keep(c, c.post("/shop/login", data={"phone": "07701234567", "password": "secret-pass-1"}), "shop_session")
check("login works", r.status_code == 303 and c.get("/shop").status_code == 200)
sent.clear()
r = client().post("/shop/forgot", data={"phone": "07701234567"})
link = re.search(r"https?://\S+/shop/reset/\S+", texts(555)[-1]).group(0)
path = link[link.index("/shop/reset/"):]
r2 = client().post("/shop/forgot", data={"phone": "07700000000"})
check("forgot: same reply for unknown number", "إن كان الرقم مسجّلاً" in r.text and "إن كان الرقم مسجّلاً" in r2.text and len(sent) == 1)
check("only a hash of the token is stored", path.rsplit("/", 1)[1] not in shop_row(1).reset_hash)
n = client()
check("reset page opens", n.get(path).status_code == 200)
r = keep(n, n.post(path, data={"password": "brand-new-pass"}), "shop_session")
check("reset sets new password and logs in", r.status_code == 303 and n.get("/shop").status_code == 200)
check("reset link single-use", client().get(path).status_code == 400)
check("old sessions die after password change", c.get("/shop").headers.get("location") == "/shop/login" and a.get("/shop").headers.get("location") == "/shop/login")
check("old password dead, new works", client().post("/shop/login", data={"phone": "07701234567", "password": "secret-pass-1"}).status_code == 401
      and client().post("/shop/login", data={"phone": "07701234567", "password": "brand-new-pass"}).status_code == 303)
patch(main.Shop, 1, reset_hash="x" * 64, reset_expires=main.utcnow() - timedelta(minutes=1))
r = adm.post("/admin/shops/1/reset")
check("admin can generate a reset link", r.status_code == 200 and "/shop/reset/" in r.text and "wa.me/9647701234567" in r.text)

# ── trust toggle: edits go back to review ──
adm.post("/admin/shops/1/trust")
n.post("/shop/deals/1/edit", data=deal_form(title="عنوان معدّل"))
check("untrusted edit returns to review", not shop_row(1).trusted and not deal_row(1).is_approved)

# ── login throttling per phone ──
for _ in range(8): client().post("/shop/login", data={"phone": "07809998877", "password": "guess-guess"})
check("login throttled per phone", client().post("/shop/login", data={"phone": "07809998877", "password": "other-pass-22"}).status_code == 429)

# ── admin deletes a shop with everything it owns ──
img = deal_row(1).image_path
adm.post("/admin/shops/1/delete")
check("shop deletion removes deals, payments, images", shop_row(1) is None and deal_row(1) is None
      and main.SessionLocal().query(main.Payment).count() == 0 and not (main.UPLOAD_DIR / img).exists())
check("no secrets in pages", "TEST-TOKEN" not in adm.get("/admin").text and main.WEBHOOK_SECRET not in adm.get("/admin").text)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
