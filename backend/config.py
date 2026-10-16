import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
os.makedirs(UPLOADS, exist_ok=True)


def _load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
DB_PATH = os.environ.get("LEARNHUB_DB", os.path.join(DATA, "learnhub.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()  # empty -> SQLite file; postgresql://... -> Postgres + pgvector
if DATABASE_URL == "embedded":  # zero-install local Postgres+pgvector (pip install pgserver); data lives in data/pgdata
    import pgserver
    _PG = pgserver.get_server(os.path.join(DATA, "pgdata"))
    DATABASE_URL = _PG.get_uri()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
IS_PG = DATABASE_URL.startswith("postgresql")
SA_URL = (DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1) if IS_PG
          else "sqlite:///" + DB_PATH.replace("\\", "/"))  # SQLAlchemy URL, used by Alembic only
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
EMBED_MODEL = os.environ.get("LEARNHUB_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MODEL_DIR = os.environ.get("LEARNHUB_MODEL_DIR", os.path.join(DATA, "models"))  # Docker bakes the model into the image here
MODEL = os.environ.get("LEARNHUB_MODEL", "claude-haiku-4-5-20251001")
TUTOR_MODEL = os.environ.get("LEARNHUB_TUTOR_MODEL", MODEL)
MAX_UPLOAD = 25 * 1024 * 1024

# --- operations ---
WORKER_MODE = os.environ.get("WORKER_MODE", "inline").lower()  # inline: job workers run inside the web process; external: run backend/worker.py
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "2") or 2)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")  # empty -> error reporting off
SENTRY_TRACES = float(os.environ.get("SENTRY_TRACES", "0") or 0)  # fraction of requests to trace (performance monitoring)
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

# --- accounts: email (SMTP), Google sign-in, public URL used in emailed links and the OAuth redirect ---
APP_URL = os.environ.get("APP_URL", "http://localhost:8100").rstrip("/")
SMTP_HOST = os.environ.get("SMTP_HOST", "")  # empty -> emails are printed to the server console (dev mode)
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER or "noreply@learnhub.local"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SIGNUP_CODE = os.environ.get("SIGNUP_CODE", "")  # set -> registering needs this invite code (protects a public site's API credit)
_rv = os.environ.get("REQUIRE_VERIFIED_EMAIL", "auto").lower()  # auto: enforce only when real email can be sent
REQUIRE_VERIFIED = bool(SMTP_HOST) if _rv == "auto" else _rv in ("1", "true", "yes")
DAILY_GOAL = 20
