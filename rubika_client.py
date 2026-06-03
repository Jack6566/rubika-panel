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


async def connect_ready(client: Client):
    """Connect AND rebuild the signing material that rubpy's connect() omits.

    connect.py only restores: client.auth, client.guid, client.private_key,
    client.user_agent. But message signing also needs client.key,
    client.decode_auth and client.import_key (see start.py lines ~38). Without
    them you get: 'NoneType' object has no attribute 'sign'.
    """
    await client.connect()

    auth = getattr(client, "auth", None)
    private_key = getattr(client, "private_key", None)

    try:
        if auth is not None and getattr(client, "key", None) in (None, ""):
            client.key = Crypto.passphrase(auth)
    except Exception:
        pass
    try:
        if auth is not None:
            client.decode_auth = Crypto.decode_auth(auth)
    except Exception:
        pass
    try:
        if private_key is not None and getattr(client, "import_key", None) is None:
            ik = _import_key_from_private(private_key)
            if ik is not None:
                client.import_key = ik
    except Exception:
        pass
    return client


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
# In rubpy 7.x, contact/user objects carry their data in `original_update`
# (a dict) or `to_dict`, e.g. {'user_guid':..., 'first_name':..., 'last_online':...}
# --------------------------------------------------------------------------- #
def _data_of(obj):
    """Return the underlying dict of a rubpy object, if any."""
    for attr in ("original_update", "to_dict"):
        v = getattr(obj, attr, None)
        if isinstance(v, dict):
            return v
    if isinstance(obj, dict):
        return obj
    return {}


def _guid_of(obj):
    if obj is None:
        return None
    d = _data_of(obj)
    for key in ("object_guid", "user_guid", "guid"):
        if d.get(key):
            return d[key]
    for attr in ("object_guid", "user_guid", "guid"):
        v = getattr(obj, attr, None)
        if v:
            return v
    user = getattr(obj, "user", None)
    if user is not None and user is not obj:
        return _guid_of(user)
    if isinstance(d.get("user"), dict):
        u = d["user"]
        for key in ("object_guid", "user_guid", "guid"):
            if u.get(key):
                return u[key]
    return None


def _name_of(obj, default="-"):
    d = _data_of(obj)
    first = d.get("first_name") or ""
    last = d.get("last_name") or ""
    name = (str(first) + " " + str(last)).strip()
    if name:
        return name
    for key in ("name", "title", "first_name"):
        if d.get(key):
            return d[key]
    for attr in ("first_name", "name", "title"):
        v = getattr(obj, attr, None)
        if v:
            return v
    return default


def _type_of(obj):
    d = _data_of(obj)
    t = d.get("type")
    if not t and isinstance(d.get("abs_object"), dict):
        t = d["abs_object"].get("type")
    if not t:
        abs_obj = getattr(obj, "abs_object", None) or obj
        t = getattr(abs_obj, "type", None)
        if t is None and isinstance(abs_obj, dict):
            t = abs_obj.get("type")
    return (t or "").lower()


def _last_online_of(u):
    """Return the user's last_online timestamp (int). Bigger = more recent.
    Falls back to 0 when hidden/unknown so those go last.
    """
    d = _data_of(u)
    v = d.get("last_online")
    if v is None:
        ot = d.get("online_time")
        if isinstance(ot, dict):
            v = ot.get("exact_time")
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Recipients: contacts + groups (paginated; Rubika returns ~100 per page)
# --------------------------------------------------------------------------- #
def _next_start_id(result):
    return _get(result, "next_start_id") or _get(result, "next_start_index")


async def get_contacts_full(client: Client) -> list:
    """Return ALL contacts as (guid, name, last_online), paginated."""
    out = []
    seen = set()
    start_id = None
    for _ in range(200):  # hard safety cap (200 * ~100 = 20k contacts)
        result = await client.get_contacts(start_id) if start_id else await client.get_contacts()
        users = getattr(result, "users", None)
        if users is None and isinstance(result, dict):
            users = result.get("users", [])
        for u in users or []:
            guid = _guid_of(u)
            if guid and guid not in seen:
                seen.add(guid)
                out.append((guid, _name_of(u), _last_online_of(u)))
        start_id = _next_start_id(result)
        if not start_id or not users:
            break
    return out


async def get_contacts(client: Client) -> list:
    """Return a list of (guid, name) for ALL contact users (paginated)."""
    return [(g, n) for (g, n, _t) in await get_contacts_full(client)]


async def get_chats_split(client: Client):
    """One paginated pass over chats. Returns (group_guids, user_chat_guids).

    - group_guids: list of (guid, name) for groups the account is in
    - user_chat_guids: set of guids of USER chats we already have a private
      conversation with (so we can prioritise them when sending)
    """
    groups = []
    seen_g = set()
    seen_u = set()
    user_chats = []  # ORDERED list of user-chat guids, newest activity first
    start_id = None
    for _ in range(200):
        result = await client.get_chats(start_id) if start_id else await client.get_chats()
        chats = getattr(result, "chats", None)
        if chats is None and isinstance(result, dict):
            chats = result.get("chats", [])
        for chat in chats or []:
            ctype = _type_of(chat)
            guid = _guid_of(chat)
            if not guid:
                continue
            if ctype == "group":
                if guid not in seen_g:
                    seen_g.add(guid)
                    groups.append((guid, _name_of(chat)))
            elif ctype == "user":
                if guid not in seen_u:
                    seen_u.add(guid)
                    user_chats.append(guid)
        start_id = _next_start_id(result)
        if not start_id or not chats:
            break
    # Rubika returns chats ordered by most recent activity first, so the
    # order of `user_chats` already reflects "recently active first".
    return groups, user_chats


async def get_groups(client: Client) -> list:
    """Return a list of (guid, name) for ALL groups the account is in (paginated)."""
    groups, _ = await get_chats_split(client)
    return groups


async def get_recipients(client: Client):
    """Return (contacts, groups) lists of (guid, name)."""
    contacts = await get_contacts(client)
    groups = await get_groups(client)
    return contacts, groups


async def get_ordered_recipients(client: Client):
    """Build the prioritised recipient list.

    Order:
      1) contacts we ALREADY have a private chat with, MOST RECENTLY ACTIVE
         FIRST (Rubika returns chats newest-first)
      2) the remaining contacts, ordered by presence: Online, then last-seen
         recently, then the rest
      3) groups
    Returns (ordered_guids, stats).
    """
    contacts = await get_contacts_full(client)  # (guid, name, last_online)
    groups, user_chats = await get_chats_split(client)

    last_online_by_guid = {}
    no_target = 0
    for guid, _name, last_online in contacts:
        if not guid:
            no_target += 1
            continue
        last_online_by_guid[guid] = last_online

    # 1) contacts that have a chat, in recent-activity order
    with_chat = [g for g in user_chats if g in last_online_by_guid]
    with_chat_set = set(with_chat)
    # 2) the rest of the contacts, ordered by last_online DESC (most recently
    #    online first -> "آنلاین/اخیراً دیده‌شده" بالاتر)
    rest = [g for g in last_online_by_guid if g not in with_chat_set]
    rest.sort(key=lambda g: last_online_by_guid.get(g, 0), reverse=True)

    ordered = with_chat + rest + [g for g, _ in groups]
    stats = {
        "contacts": len(contacts),
        "groups": len(groups),
        "with_chat": len(with_chat),
        "no_target": no_target,
    }
    return ordered, stats


# --------------------------------------------------------------------------- #
# Find a marked message in Saved Messages (for forward-based sending)
#   You put a file in your Rubika Saved Messages and end its caption with a
#   marker like [کد135]. The bot finds that message and forwards it to everyone.
# --------------------------------------------------------------------------- #
def _msg_id_of(msg):
    return (
        _get(msg, "message_id")
        or _get(msg, "id")
        or (str(_get(msg, "message_id")) if _get(msg, "message_id") else None)
    )


def _msg_text_of(msg):
    return (
        _get(msg, "text")
        or _get(msg, "caption")
        or ""
    )


async def find_marked_message(client: Client, marker: str):
    """Search the account's Saved Messages for a message whose text/caption
    contains `marker`. Returns (saved_guid, message_id) or (saved_guid, None).
    """
    me = await client.get_me()
    saved_guid = _guid_of(me)
    if not saved_guid:
        raise RuntimeError("could not resolve Saved Messages guid")

    max_id = None
    for _ in range(50):  # scan up to ~50 pages of recent saved messages
        try:
            if max_id:
                result = await client.get_messages(saved_guid, max_id, "20")
            else:
                result = await client.get_messages(saved_guid, "0", "20")
        except Exception:
            break
        messages = getattr(result, "messages", None)
        if messages is None and isinstance(result, dict):
            messages = result.get("messages", [])
        if not messages:
            break
        for msg in messages:
            if marker in _msg_text_of(msg):
                return saved_guid, _msg_id_of(msg)
        # paginate older
        last = messages[-1]
        max_id = _msg_id_of(last)
        if not max_id:
            break
    return saved_guid, None


async def forward_message(client: Client, from_guid: str, to_guid: str, message_id):
    """Forward one already-uploaded message to a single recipient."""
    ids = message_id if isinstance(message_id, list) else [message_id]
    await client.forward_messages(from_guid, to_guid, ids)


# --------------------------------------------------------------------------- #
# Sending (direct send for text/photo/file)
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

