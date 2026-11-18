"""لوحة الإدارة: قائمة المحلات المشتركة — الحالة، كم دفع كل محل، وكم عرضاً منشوراً له."""
import io, os, re, sys, tempfile
from datetime import timedelta

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
def form(**kw):
    d = dict(shop_name="", title="وجبة", category="مطاعم وكافيهات", area="الزهور",
             old_price="12000", new_price="8000", link="07701234567", contact_phone="")
    d.update(kw); return d
def section(html):
    return html.split('id="subs"')[1].split("</details>")[0]

T = main.today()
adm = client(); keep(adm, adm.post("/admin/login", data={"password": "test-pass-123"}), "admin_session")
check("empty state", "لا توجد محلات مشتركة بعد" in section(adm.get("/admin").text))

# four shops added by the admin, each with deals
for i, (name, phone) in enumerate([("محل مدفوع", "07701110001"), ("محل تجربة", "07701110002"),
                                   ("محل منتهي", "07701110003"), ("محل يدوي", "07701110004")], 1):
    adm.post("/admin/shops/new", data={"name": name, "phone": phone})
    adm.post("/submit", data=form(shop_id=str(i), title=f"عرض {i}"), files={"image": ("a.png", png(), "image/png")})
adm.post("/submit", data=form(shop_id="1", title="عرض 1ب"), files={"image": ("a.png", png(), "image/png")})

# shop 1 pays VIP through the portal; admin approves
link = re.search(r"https?://\S+/shop/reset/[\w-]+", adm.post("/admin/shops/1/reset").text).group(0)
o = client(); keep(o, o.post("/" + link.split("/", 3)[3], data={"password": "owner-pass-1"}), "shop_session")
o.post("/shop/pay", data={"plan": "vip", "txn_ref": "777"})
adm.post("/admin/payments/1/approve")

# shop 3: trial ran out long ago
with main.SessionLocal() as db:
    s = db.get(main.Shop, 3); s.trial_until = T - timedelta(days=20); main.sync_shop_deals(db, s); db.commit()

# shop 4: admin records a payment from the deal row
with main.SessionLocal() as db:
    d4 = db.scalar(main.select(main.Deal.id).where(main.Deal.shop_id == 4))
adm.post(f"/admin/deals/{d4}/renew")
with main.SessionLocal() as db:
    manual = db.scalar(main.select(main.Payment).where(main.Payment.shop_id == 4))
check("manual renewal is recorded as an approved payment", manual is not None and manual.status == "approved"
      and manual.amount == main.BASIC_PRICE and "يدوي" in manual.txn_ref)

h = section(adm.get("/admin").text)
check("summary counts paid / trial / expired", "2 مدفوع" in h and "1 تجربة" in h and "1 منتهي" in h)
check("total collected = VIP + basic", f"{main.VIP_PRICE + main.BASIC_PRICE:,}" in h)
rows = h.split('<a href="#shop-')[1:]
names = [re.search(r'font-extrabold truncate">([^<]+)<', r).group(1) for r in rows]
check("paid shops listed first, expired last", set(names[:2]) == {"محل مدفوع", "محل يدوي"} and names[-1] == "محل منتهي")
paid_row = rows[names.index("محل مدفوع")]
check("paid shop: VIP, amount and payment count", "VIP" in paid_row and f"{main.VIP_PRICE:,}" in paid_row and "1 دفعة" in paid_row)
check("paid shop: 2 of 2 deals live", re.search(r'text-emerald-700">2</span><span class="text-slate-400"> / 2', paid_row))
trial_row = rows[names.index("محل تجربة")]
check("trial shop shows trial and nothing paid", "تجربة" in trial_row and "لم يدفع بعد" in trial_row)
exp_row = rows[names.index("محل منتهي")]
check("expired shop shows 0 live of 1 and days since expiry", "منتهي" in exp_row and "انتهى منذ" in exp_row
      and re.search(r'>0</span><span class="text-slate-400"> / 1', exp_row))
check("non-admin cannot see it", client().get("/admin").status_code == 303)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
