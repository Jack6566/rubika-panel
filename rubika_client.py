"""
Rubika integration layer (wraps the `rubpy` library, v7.x).
===========================================================

ALL rubpy-specific calls live in THIS file on purpose. rubpy is an unofficial
library, so a few method names/response shapes may differ between versions.
If something fails on the server, you only need to fix it HERE.

LOGIN is handled by the standalone `login.py` script, which uses rubpy's OWN
interactive login flow (it generates the RSA public/private keys and saves the
session correctly on its own). This file only OPENS already-saved sessions and
sends messages, which is the robust part.

Inspect the installed rubpy API on the server:
    python -c "import rubpy; print(rubpy.__version__)"
    python -c "from rubpy import Client; print([m for m in dir(Client) if not m.startswith('_')])"
"""
import os

from rubpy import Client

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "data", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


def session_path(phone: str) -> str:
    safe = phone.replace("+", "").replace(" ", "")
    return os.path.join(SESSIONS_DIR, f"acc_{safe}")


def open_client(phone: str) -> Client:
    """Return a rubpy client bound to the account's SAVED session.

    For an already-authorized session, connect() loads it without prompting.
    """
    return Client(name=session_path(phone))


# --------------------------------------------------------------------------- #
# Tolerant field extractors (shapes vary across rubpy versions)
# --------------------------------------------------------------------------- #
def _guid_of(obj):
    if obj is None:
        return None
    for attr in ("object_guid", "user_guid", "guid"):
        v = getattr(obj, attr, None)
        if v:
            return v
        if isinstance(obj, dict) and obj.get(attr):
            return obj.get(attr)
    # sometimes nested under .user
    user = getattr(obj, "user", None)
    if user is not None and user is not obj:
        return _guid_of(user)
    if isinstance(obj, dict) and isinstance(obj.get("user"), dict):
        return _guid_of(obj["user"])
    return None


def _name_of(obj, default="-"):
    for attr in ("first_name", "name", "title"):
        v = getattr(obj, attr, None)
        if v:
            return v
        if isinstance(obj, dict) and obj.get(attr):
            return obj.get(attr)
    user = getattr(obj, "user", None)
    if user is not None and user is not obj:
        return _name_of(user, default)
    if isinstance(obj, dict) and isinstance(obj.get("user"), dict):
        return _name_of(obj["user"], default)
    return default


def _type_of(obj):
    abs_obj = getattr(obj, "abs_object", None) or obj
    t = getattr(abs_obj, "type", None)
    if t is None and isinstance(abs_obj, dict):
        t = abs_obj.get("type")
    return (t or "").lower()


# --------------------------------------------------------------------------- #
# Recipients: contacts + groups
# --------------------------------------------------------------------------- #
async def get_contacts(client: Client) -> list:
    """Return a list of (guid, name) for contact users."""
    result = await client.get_contacts()
    users = getattr(result, "users", None)
    if users is None and isinstance(result, dict):
        users = result.get("users", [])
    out = []
    for u in users or []:
        guid = _guid_of(u)
        if guid:
            out.append((guid, _name_of(u)))
    return out


async def get_groups(client: Client) -> list:
    """Return a list of (guid, name) for every group the account is in."""
    result = await client.get_chats()
    chats = getattr(result, "chats", None)
    if chats is None and isinstance(result, dict):
        chats = result.get("chats", [])
    out = []
    for chat in chats or []:
        if _type_of(chat) == "group":
            guid = _guid_of(chat)
            if guid:
                out.append((guid, _name_of(chat)))
    return out


async def get_recipients(client: Client):
    """Return (contacts, groups) lists of (guid, name)."""
    contacts = await get_contacts(client)
    groups = await get_groups(client)
    return contacts, groups


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
async def send_content(client: Client, guid: str, content: dict):
    """Send the configured content (text / photo / file) to one recipient."""
    ct = content["content_type"]
    caption = content.get("content_text") or ""
    if ct == "text":
        await client.send_message(guid, content.get("content_text") or "")
    elif ct in ("photo", "file"):
        await client.send_file(guid, file=content["media_path"], caption=caption)
    else:
        raise ValueError(f"unknown content type: {ct}")


async def send_to_saved(client: Client, content: dict):
    """Send content to the account's own Saved Messages as a health test.

    The account's own chat guid equals its user guid (get_me).
    """
    me = await client.get_me()
    guid = _guid_of(me)
    if not guid:
        raise RuntimeError("could not resolve self guid for saved-messages test")
    await send_content(client, guid, content)
    return guid
