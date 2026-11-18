"""الدومين الرسمي laqtamosul.com: العنوان القديم و www يتحولان إليه، والفحص الصحي وبوت تيليغرام لا يتحولان."""
import os, sys, tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "test-pass-123"
os.environ["LAUNCH_DATE"] = "2000-01-01"
os.environ["PUBLIC_URL"] = "https://laqtamosul.com"
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_ADMIN_CHAT", "DATABASE_URL", "ALLOW_ANONYMOUS_SUBMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
import main

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name)

def get(host, path, method="GET"):
    c = TestClient(main.app, follow_redirects=False, base_url=f"https://{host}")
    return c.request(method, path)

check("canonical host is laqtamosul.com", main.CANONICAL_HOST == "laqtamosul.com")
r = get("lagta.onrender.com", "/deal/5?src=instagram")
check("old onrender address → laqtamosul.com, path and query kept",
      r.status_code == 301 and r.headers["location"] == "https://laqtamosul.com/deal/5?src=instagram")
r = get("www.laqtamosul.com", "/join")
check("www → bare domain", r.status_code == 301 and r.headers["location"] == "https://laqtamosul.com/join")
check("HEAD also redirected", get("lagta.onrender.com", "/", "HEAD").status_code == 301)
check("canonical host served directly", get("laqtamosul.com", "/").status_code == 200)
check("health check on the old host is not redirected", get("lagta.onrender.com", "/healthz").status_code == 200)
check("POST is not redirected (form data would be lost)", get("lagta.onrender.com", "/shop/login", "POST").status_code != 301)
check("telegram webhook path is not redirected", get("lagta.onrender.com", "/tg/webhook/x", "POST").status_code != 301)
check("unrelated hosts untouched", get("testserver", "/").status_code == 200)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
