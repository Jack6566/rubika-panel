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
from rubpy.crypto import Crypto

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


def normalize_phone(phone: str) -> str:
    """Rubika expects digits with country code, no '+' and no leading 0.
    Examples: '+989121234567' -> '989121234567', '09121234567' -> '989121234567'
    """
    p = "".join(ch for ch in phone if ch.isdigit())
    if p.startswith("0"):
        p = "98" + p[1:]
    return p


# --------------------------------------------------------------------------- #
# Programmatic login (mirrors rubpy's own start.py flow)
#   1) public_key, private_key = Crypto.create_keys()
#   2) send_code(phone_number=...) -> phone_code_hash
#   3) sign_in(phone_code, phone_number, phone_code_hash, public_key)
#   4) result.auth = Crypto.decrypt_RSA_OAEP(private_key, result.auth)
#   5) store auth/key into client, register_device, save session
# --------------------------------------------------------------------------- #
def _get(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v not in (None, ""):
            return v
        if isinstance(obj, dict) and obj.get(n) not in (None, ""):
            return obj.get(n)
    return None


async def start_login(phone: str, pass_key: str = None):
    """Phase 1: connect + request the login code.

    Mirrors rubpy start.py: if the account has 2FA, send_code returns a status
    asking for a pass_key; we expose that so the panel can ask for the password
    and call start_login again WITH the pass_key.

    Returns a dict:
      {client, phone, status, phone_code_hash, hint, public_key, private_key}
    """
    phone = normalize_phone(phone)
    client = ctx_client = Client(name=session_path(phone))
    await client.connect()

    public_key, private_key = Crypto.create_keys()

    if pass_key:
        result = await client.send_code(phone_number=phone, pass_key=pass_key)
    else:
        result = await client.send_code(phone_number=phone)

    status = _get(result, "status") or ""
    phone_code_hash = _get(result, "phone_code_hash")
    hint = _get(result, "hint_pass_key")

    return {
        "client": ctx_client,
        "phone": phone,
        "status": status,
        "phone_code_hash": phone_code_hash,
        "hint": hint,
        "public_key": public_key,
        "private_key": private_key,
    }


def _import_key_from_private(private_key):
    """Build the signing key exactly like rubpy start.py does."""
    try:
        from Crypto.Hash import SHA256  # noqa: F401  (ensure pycryptodome present)
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15
        if private_key is not None:
            return pkcs1_15.new(RSA.import_key(private_key.encode()))
    except Exception:
        pass
    return None


async def finish_login(ctx: dict, code: str):
    """Phase 2: sign in with the code, then replicate start.py EXACTLY:
        result.auth = Crypto.decrypt_RSA_OAEP(private_key, result.auth)
        client.key  = Crypto.passphrase(result.auth)
        client.auth = result.auth
        client.decode_auth = Crypto.decode_auth(client.auth)
        client.import_key  = pkcs1_15.new(RSA.import_key(private_key))
        client.session.insert(auth, guid, user_agent, phone_number, private_key)
        await client.register_device(device_model=client.name)
    """
    client: Client = ctx["client"]
    phone = ctx["phone"]
    private_key = ctx["private_key"]

    result = await client.sign_in(
        phone_code=code,
        phone_number=phone,
        phone_code_hash=ctx["phone_code_hash"],
        public_key=ctx["public_key"],
    )

    status = _get(result, "status") or ""
    if str(status).upper() not in ("OK", ""):
        # Not a success status (e.g. wrong code) -> let caller handle it.
        raise RuntimeError(f"sign_in status: {status}")

    # ---- Replicate start.py's post-sign-in steps precisely ----
    enc_auth = _get(result, "auth")
    decrypted = Crypto.decrypt_RSA_OAEP(private_key, enc_auth)

    client.private_key = private_key
    client.key = Crypto.passphrase(decrypted)
    client.auth = decrypted
    try:
        client.decode_auth = Crypto.decode_auth(client.auth)
    except Exception:
        pass
    ik = _import_key_from_private(private_key)
    if ik is not None:
        client.import_key = ik

    # Persist into the session store (start.py uses client.session.insert).
    try:
        user = _get(result, "user")
        guid = _guid_of(user) or _guid_of(result)
        phone_number = _get(user, "phone") or phone
        user_agent = getattr(client, "user_agent", None)
        client.session.insert(
            auth=client.auth,
            guid=guid,
            user_agent=user_agent,
            phone_number=phone_number,
            private_key=private_key,
        )
    except Exception:
        # Fallback: some versions auto-save; ignore if insert signature differs.
        pass

    # Register device (start.py calls this right after a fresh sign-in).
    try:
        await client.register_device(device_model=getattr(client, "name", "RubikaPanel"))
    except Exception:
        try:
            await client.register_device()
        except Exception:
            pass

    return result


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
