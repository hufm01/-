"""نسخة احتياطية يومية: قاعدة SQLite بأمان + مجلد الصور مضغوطاً.

الاستخدام:
    python scripts/backup.py                 # ينسخ إلى ./backups
    python scripts/backup.py /مسار/آخر       # أو إلى مجلد تحدده
    python scripts/backup.py --keep 14       # يحتفظ بآخر 14 نسخة فقط

على Render: شغّله من Shell الخدمة، أو اربطه بـ Cron Job يومياً، ثم انسخ الملفات
من القرص إلى مكان خارجي (Google Drive أو جهازك) بشكل دوري.

ملاحظة: إن كان DATABASE_URL يشير إلى PostgreSQL فاستعمل pg_dump بدلاً من هذا السكربت.
"""

import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_FILE = DATA_DIR / "deals.db"
UPLOAD_DIR = DATA_DIR / "uploads"


def backup(dest: Path, keep: int | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = dest / f"lagta-{stamp}"
    target.mkdir()

    # نسخة متسقة من SQLite حتى لو كان التطبيق يكتب في نفس اللحظة
    if DB_FILE.exists():
        src = sqlite3.connect(DB_FILE)
        dst = sqlite3.connect(target / "deals.db")
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

    if UPLOAD_DIR.exists():
        with tarfile.open(target / "uploads.tar.gz", "w:gz") as tar:
            tar.add(UPLOAD_DIR, arcname="uploads")

    archive = shutil.make_archive(str(target), "zip", target)
    shutil.rmtree(target)

    if keep:
        old = sorted(dest.glob("lagta-*.zip"))[:-keep]
        for f in old:
            f.unlink()
    return Path(archive)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # الطرفية في ويندوز قد لا تعرض العربية
    args = sys.argv[1:]
    keep = None
    if "--keep" in args:
        i = args.index("--keep")
        keep = int(args[i + 1])
        del args[i:i + 2]
    dest = Path(args[0]) if args else ROOT / "backups"
    out = backup(dest, keep)
    print(f"نسخة احتياطية: {out}")
