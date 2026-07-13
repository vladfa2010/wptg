"""
Configuration module. Loads env vars, validates required fields.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


# ─── Telegram ─────────────────────────────────────────────
TG_BOT_TOKEN: str = os.environ["TG_BOT_TOKEN"]
ALLOWED_USER_IDS: list[int] = [
    int(uid.strip()) for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",") if uid.strip()
]

# ─── WordPress ────────────────────────────────────────────
WP_BASE_URL: str = os.environ["WP_BASE_URL"].rstrip("/")
WP_LOGIN: str = os.environ["WP_LOGIN"]
WP_PASSWORD: str = os.environ["WP_PASSWORD"]  # Application Password

# ─── Moonshot API (rewrite + categorization) ──────────────
MOONSHOT_API_KEY: str = os.environ["MOONSHOT_API_KEY"]
MOONSHOT_BASE_URL: str = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
MOONSHOT_MODEL: str = os.environ.get("MOONSHOT_MODEL", "moonshot-v1-128k")

# ─── Image generation uses the same Moonshot API key ──────
KIMI_API_KEY: str = os.environ.get("KIMI_API_KEY", MOONSHOT_API_KEY)

# ─── File paths ───────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
TAXONOMY_CACHE_PATH = DATA_DIR / "taxonomy_cache.json"

# ─── Bot settings ─────────────────────────────────────────
REWRITE_MAX_TOKENS = 4096
REWRITE_TEMPERATURE = 0.7
POLLING_TIMEOUT = 30
PAGINATION_SIZE = 10  # terms per page in category editor


def validate() -> None:
    """Fail fast on startup if required env vars are missing."""
    required = {
        "TG_BOT_TOKEN": TG_BOT_TOKEN,
        "ALLOWED_USER_IDS": ALLOWED_USER_IDS,
        "WP_BASE_URL": WP_BASE_URL,
        "WP_LOGIN": WP_LOGIN,
        "WP_PASSWORD": WP_PASSWORD,
        "MOONSHOT_API_KEY": MOONSHOT_API_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
