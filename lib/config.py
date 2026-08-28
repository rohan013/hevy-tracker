import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _load_dotenv():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# Read before the paths below, so .env can set HEVY_PERSONAL_DIR.
_load_dotenv()

DB_PATH = str(PROJECT_ROOT / "data" / "hevy.db")

# Everything specific to one account lives here, and this repo does not track it.
# Point HEVY_PERSONAL_DIR elsewhere to keep it outside the checkout entirely.
PERSONAL_DIR = Path(os.environ.get("HEVY_PERSONAL_DIR") or PROJECT_ROOT / "personal")
PROGRAM_PATH = PERSONAL_DIR / "program.json"
ROUTINE_BACKUP_DIR = PERSONAL_DIR / "routines"

API_BASE_URL = "https://api.hevyapp.com"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_DELAY_SECONDS = 0.3
# Hevy rate limits aggressively and does not always send Retry-After, so back off
# on a scale of seconds rather than the sub-second retry used for transient 5xx.
RATE_LIMIT_BACKOFF_SECONDS = 20


def get_api_key():
    return os.environ.get("HEVY_API_KEY")
