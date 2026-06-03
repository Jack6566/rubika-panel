"""
Rubika integration layer (wraps the `rubpy` library).
=====================================================

ALL rubpy-specific calls live in THIS file on purpose. rubpy is an unofficial
library, so a few method names/response shapes may differ between versions.
If something fails on the server, you only need to fix it HERE.

How to inspect the installed rubpy API on the server:
    python -c "import rubpy; print(rubpy.__version__)"
    python -c "from rubpy import Client; print([m for m in dir(Client) if not m.startswith('_')])"

Assumed rubpy (>=7) async API (adjust here if your version differs):
    client = Client(name=<session path>)
    await client.connect()
    sent = await client.send_code(phone_number=...)            -> sent.phone_code_hash, sent.status
    await client.sign_in(phone_number=..., phone_code_hash=..., phone_code=...)
    await client.register_device(...)  # handled internally by some versions
    me = await client.get_me()
    await client.send_message(object_guid, text)
    await client.send_file(object_guid, file=path, caption=...)
    chats  = await client.get_chats()                          -> .chats[*].abs_object.type / .object_guid
    contacts = await client.get_contacts()                     -> users list
"""
import os

from rubpy import Client

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "data", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


def session_path(phone: str) -> str:
    safe = phone.replace("+", "").replace(" ", "")
    return os.path.join(SESSIONS_DIR, f"acc_{safe}")


def new_client(phone: str) -> Client:
    """Create a rubpy client bound to a per-account session file."""
    return Client(name=session_path(phone))


# --------------------------------------------------------------------------- #
# Login (two phase: code, optional password)
# --------------------------------------------------------------------------- #
async def send_login_code(phone: str):
    """Connect and request an SMS code. Returns (client, phone_code_hash)."""
    client = new_client(phone)
    await client.connect()
    sent = await client.send_code(phone_number=phone)
    # rubpy returns an object carrying the hash; be tolerant about its shape.
    phone_code_hash = (
        getattr(sent, "phone_code_hash", None)
        or (sent.get("phone_code_hash") if isinstance(sent, dict) else None)
    )
    return client, phone_code_hash


async def sign_in_with_code(client: Client, phone: str, phone_code_hash: str, code: str):
    """Complete sign-in with the SMS code. Returns the raw result."""
    return await client.sign_in(
        phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code
    )


async def finalize_login(client: Client):
    """Register device / fetch own info after a successful sign-in."""
    try:
        # Some rubpy versions need an explicit device registration.
        await client.register_device("RubikaPanel")
    except Exception:
        pass
    me = await client.get_me()
    return me


# --------------------------------------------------------------------------- #
# Recipients: contacts + groups
# --------------------------------------------------------------------------- #
def _guid_of(obj):
    return (
        getattr(obj, "object_guid", None)
        or getattr(obj, "guid", None)
        or (obj.get("object_guid") if isinstance(obj, dict) else None)
    )


def _type_of(obj):
    abs_obj = getattr(obj, "abs_object", None) or obj
    t = getattr(abs_obj, "type", None)
    if t is None and isinstance(abs_obj, dict):
        t = abs_obj.get("type")
    return (t or "").lower()


async def get_contacts(client: Client) -> list:
    """Return a list of (guid, name) for contact users."""
    result = await client.get_contacts()
    users = getattr(result, "users", None)
    if users is None and isinstance(result, dict):
        users = result.get("users", [])
    out = []
    for u in users or []:
        guid = _guid_of(u)
        name = (
            getattr(u, "first_name", None)
            or (u.get("first_name") if isinstance(u, dict) else None)
            or "-"
        )
        if guid:
            out.append((guid, name))
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
            name = (
                getattr(chat, "title", None)
                or (chat.get("title") if isinstance(chat, dict) else None)
                or "-"
            )
            if guid:
                out.append((guid, name))
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
    """Send the content to the account's own Saved Messages as a health test.

    In rubpy the account's own chat guid equals its user guid (get_me).
    """
    me = await client.get_me()
    guid = _guid_of(me) or _guid_of(getattr(me, "user", me))
    if not guid:
        raise RuntimeError("could not resolve self guid for saved-messages test")
    await send_content(client, guid, content)
    return guid
