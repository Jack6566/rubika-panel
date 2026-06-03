"""Loads all settings from the .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _id_list(name: str) -> list:
    """Parse a comma/space separated list of numeric IDs from env."""
    raw = os.getenv(name, "") or ""
    ids = []
    for part in raw.replace(",", " ").split():
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


API_ID = _int("API_ID")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = _int("OWNER_ID")
# Extra admins who can also use the panel (comma separated numeric IDs)
ADMIN_IDS = _id_list("ADMIN_IDS")
LOG_GROUP_ID = _int("LOG_GROUP_ID")
SEND_DELAY = _float("SEND_DELAY", 0.5)
# Marker placed at the END of a file's caption in your Rubika Saved Messages.
# When set, the bot finds that marked message and FORWARDS it to everyone
# (no re-upload). Leave empty to use the normal "set content" flow instead.
FORWARD_MARKER = os.getenv("FORWARD_MARKER", "").strip()

# Everyone allowed to use the bot = the owner + any extra admins
ALLOWED_IDS = [i for i in ([OWNER_ID] + ADMIN_IDS) if i]


def validate() -> list:
    """Returns a list of human-readable problems with the configuration."""
    problems = []
    if not API_ID:
        problems.append("API_ID is missing")
    if not API_HASH:
        problems.append("API_HASH is missing")
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN is missing")
    if not OWNER_ID:
        problems.append("OWNER_ID is missing")
    if not LOG_GROUP_ID:
        problems.append("LOG_GROUP_ID is missing")
    return problems
