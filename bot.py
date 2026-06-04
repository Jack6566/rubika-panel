"""
Rubika Userbot Management Panel (controlled from Telegram)
==========================================================

A Telegram glass-button panel that:
  - adds multiple Rubika accounts (login by phone + code, with 2FA support)
  - stores each login session safely in SQLite
  - sends one shared, pre-configured content (text / photo / file + caption)
    to each account's CONTACTS + GROUPS
  - tests every new account by first sending the content to its Saved Messages
  - processes sends one account at a time (sequential queue)
  - asks for confirmation before sending and can be stopped mid-way
  - STOPS IMMEDIATELY on the first failed send
  - reports every event to a Telegram log group (English, styled cards)

Panel text is Persian. Log cards are English. Only allowed admins can use it.
"""
import asyncio
import os
from datetime import datetime

from telethon import TelegramClient, events, Button

import config
import db
import rubika_client as rb
import proxy_manager as pm

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "data", "media")
os.makedirs(MEDIA_DIR, exist_ok=True)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# The Telegram panel bot
bot = TelegramClient("data/panel_bot", config.API_ID, config.API_HASH)

# Per-user conversation state, e.g. {"step": "await_phone"}
state: dict = {}
# Rubika login clients mid-flow (waiting for code / password), keyed by admin id
pending: dict = {}
# Sequential send queue + stop flags
send_queue: "asyncio.Queue" = asyncio.Queue()
stop_flags: dict = {}


# --------------------------------------------------------------------------- #
# Log styling (clean, left-aligned, no decorative stars)
# --------------------------------------------------------------------------- #
LINE = "━━━━━━━━━━━━━━━━"


def card(title: str, rows: list) -> str:
    body = "\n".join(rows)
    return f"{title}\n{LINE}\n{body}"


def is_owner(event) -> bool:
    return event.sender_id in config.ALLOWED_IDS


async def log(text: str):
    """Send a report to the Telegram log group (never crash the bot)."""
    try:
        await bot.send_message(config.LOG_GROUP_ID, text)
    except Exception as e:  # noqa: BLE001
        print(f"[log error] {e}")


# --------------------------------------------------------------------------- #
# Panel menus (Persian)
# --------------------------------------------------------------------------- #
def main_menu():
    return [
        [Button.inline("➕ افزودن اکانت", b"add_account"),
         Button.inline("👥 اکانت‌ها", b"manage_accounts")],
        [Button.inline("⚙️ تنظیم محتوا", b"set_content"),
         Button.inline("🔌 پروکسی‌ها", b"proxies")],
        [Button.inline("💾 بکاپ", b"backup")],
    ]


def proxy_status_emoji(p):
    return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(p.get("status"), "⚪")


WELCOME = (
    "╭───────────────────╮\n"
    "     🤖  پنل روبیکا\n"
    "╰───────────────────╯\n"
    "به پنل مدیریت خوش اومدی 👋\n"
    "یکی از گزینه‌های زیر را انتخاب کن:"
)


def content_summary(content: dict) -> str:
    ct = content.get("content_type")
    if not ct:
        return "هنوز محتوایی تنظیم نشده ❌"
    if ct == "text":
        return f"📝 متن:\n{content.get('content_text') or ''}"
    label = "🖼 عکس" if ct == "photo" else "📎 فایل"
    cap = content.get("content_text")
    return f"{label}" + (f"\n📝 کپشن: {cap}" if cap else " (بدون کپشن)")


# --------------------------------------------------------------------------- #
# /start and navigation
# --------------------------------------------------------------------------- #
@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    if not is_owner(event):
        await event.respond("⛔ شما به این ربات دسترسی ندارید.")
        return
    state.pop(event.sender_id, None)
    await event.respond(WELCOME, buttons=main_menu())


@bot.on(events.CallbackQuery(data=b"home"))
async def home_cb(event):
    if not is_owner(event):
        return
    state.pop(event.sender_id, None)
    await event.edit(WELCOME, buttons=main_menu())


@bot.on(events.CallbackQuery(data=b"cancel"))
async def cancel_cb(event):
    if not is_owner(event):
        return
    p = pending.pop(event.sender_id, None)
    if p:
        try:
            await p["client"].disconnect()
        except Exception:  # noqa: BLE001
            pass
    state.pop(event.sender_id, None)
    await event.edit("لغو شد. منوی اصلی:", buttons=main_menu())


# --------------------------------------------------------------------------- #
# Add account
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(data=b"add_account"))
async def add_account_cb(event):
    if not is_owner(event):
        return
    state[event.sender_id] = {"step": "await_phone"}
    await event.edit(
        "📱 شماره اکانت روبیکا را بفرست.\n"
        "مثال: `09123456789` یا `989123456789`",
        buttons=[[Button.inline("🔙 لغو", b"cancel")]],
    )


# --------------------------------------------------------------------------- #
# Manage accounts
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(data=b"manage_accounts"))
async def manage_cb(event):
    if not is_owner(event):
        return
    accounts = db.list_accounts()
    if not accounts:
        await event.edit(
            "هیچ اکانتی اضافه نشده. اول یک اکانت اضافه کن.",
            buttons=[[Button.inline("➕ افزودن اکانت", b"add_account")],
                     [Button.inline("🔙 بازگشت", b"home")]],
        )
        return
    buttons = []
    for i, acc in enumerate(accounts, start=1):
        mark = "" if acc["status"] == "active" else " ⚠️"
        buttons.append([Button.inline(f"{i}- {acc['phone']}{mark}", f"acc_{acc['id']}".encode())])
    buttons.append([Button.inline("🔙 بازگشت", b"home")])
    await event.edit("📋 اکانت‌های شما:", buttons=buttons)


@bot.on(events.CallbackQuery(pattern=b"acc_(\\d+)"))
async def account_menu_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    acc = db.get_account(account_id)
    if not acc:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    status = "فعال ✅" if acc["status"] == "active" else "غیرفعال ⚠️"
    text = (
        "╭───── 👤 اکانت ─────╮\n"
        f"  نام    : {acc['name'] or '-'}\n"
        f"  شماره  : {acc['phone']}\n"
        f"  آیدی   : {acc['user_id']}\n"
        f"  افزوده : {acc['added_at']}\n"
        f"  وضعیت  : {status}\n"
        "╰────────────────────╯"
    )
    buttons = [
        [Button.inline("🚀 شروع ارسال", f"send_{account_id}".encode())],
        [Button.inline("🗑 حذف اکانت", f"del_{account_id}".encode())],
        [Button.inline("🔙 بازگشت", b"manage_accounts")],
    ]
    await event.edit(text, buttons=buttons)


# --------------------------------------------------------------------------- #
# Delete account (with confirmation)
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(pattern=b"del_(\\d+)"))
async def delete_confirm_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    await event.edit(
        "آیا از حذف این اکانت مطمئنی؟",
        buttons=[
            [Button.inline("✅ بله، حذف کن", f"delyes_{account_id}".encode())],
            [Button.inline("🔙 خیر", f"acc_{account_id}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(pattern=b"delyes_(\\d+)"))
async def delete_do_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    acc = db.get_account(account_id)
    db.delete_account(account_id)
    if acc:
        await log(card("ACCOUNT REMOVED 🗑", [
            f"📱 Phone : {acc['phone']}",
            f"🕒 Time  : {now()}",
        ]))
    await event.edit("اکانت حذف شد. ✅", buttons=[[Button.inline("🔙 بازگشت", b"manage_accounts")]])


# --------------------------------------------------------------------------- #
# Set shared content
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(data=b"set_content"))
async def set_content_cb(event):
    if not is_owner(event):
        return
    current = content_summary(db.get_content())
    state[event.sender_id] = {"step": "await_content"}
    await event.edit(
        "⚙️ محتوای فعلی:\n\n"
        f"{current}\n\n"
        "حالا محتوای جدید را بفرست (متن، یا عکس/فایل با کپشن دلخواه). "
        "همین یک‌بار ست می‌شود و ذخیره می‌ماند.",
        buttons=[[Button.inline("🔙 لغو", b"cancel")]],
    )


# --------------------------------------------------------------------------- #
# Start sending (confirmation)
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(pattern=b"send_(\\d+)"))
async def send_confirm_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    acc = db.get_account(account_id)
    if not acc:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    content = db.get_content()
    if not config.FORWARD_MARKER and not content.get("content_type"):
        await event.answer("اول محتوای ارسالی را تنظیم کن (یا FORWARD_MARKER را ست کن).", alert=True)
        return
    if config.FORWARD_MARKER:
        what = f"📎 فوروارد پیام نشان‌دار از Saved Messages\n🔖 مارکر: {config.FORWARD_MARKER}"
    else:
        what = content_summary(content)
    await event.edit(
        f"🚀 شروع ارسال با اکانت {acc['phone']}؟\n\n"
        f"محتوایی که ارسال می‌شود:\n{what}\n\n"
        "گیرنده‌ها: مخاطبین + گروه‌ها\nمطمئنی؟",
        buttons=[
            [Button.inline("✅ بله، شروع کن", f"go_{account_id}".encode())],
            [Button.inline("🔙 خیر", f"acc_{account_id}".encode())],
        ],
    )


@bot.on(events.CallbackQuery(pattern=b"go_(\\d+)"))
async def send_go_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    stop_flags[account_id] = False
    await send_queue.put(account_id)
    await event.edit(
        "✅ در صف ارسال قرار گرفت. گزارش‌ها در گروه لاگ می‌آید.",
        buttons=[
            [Button.inline("⏹ توقف این ارسال", f"stop_{account_id}".encode())],
            [Button.inline("🔙 بازگشت", b"manage_accounts")],
        ],
    )


@bot.on(events.CallbackQuery(pattern=b"stop_(\\d+)"))
async def stop_cb(event):
    if not is_owner(event):
        return
    account_id = int(event.pattern_match.group(1))
    stop_flags[account_id] = True
    await event.answer("درخواست توقف ثبت شد. بعد از پیام جاری متوقف می‌شود.", alert=True)


# --------------------------------------------------------------------------- #
# Proxies (SSH servers turned into SOCKS5 via Docker)
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(data=b"proxies"))
async def proxies_cb(event):
    if not is_owner(event):
        return
    items = db.list_proxies()
    rows = []
    for p in items:
        emoji = proxy_status_emoji(p)
        up = "آپلود ✅" if p.get("upload_ok") else "آپلود ❌"
        ping = f"{p.get('ping_ms', 0)}ms"
        rows.append([Button.inline(f"{emoji} {p['host']} — {ping} — {up}",
                                   f"px_{p['id']}".encode())])
    rows.append([Button.inline("➕ افزودن سرور پروکسی", b"px_add")])
    rows.append([Button.inline("🔄 چک سلامت همه", b"px_check")])
    rows.append([Button.inline("🔙 بازگشت", b"home")])
    head = "🔌 سرورهای پروکسی\n" + LINE + (f"\nتعداد: {len(items)}" if items else "\nهنوز سروری اضافه نشده.")
    await event.edit(head, buttons=rows)


@bot.on(events.CallbackQuery(data=b"px_add"))
async def px_add_cb(event):
    if not is_owner(event):
        return
    state[event.sender_id] = {"step": "await_proxy"}
    await event.edit(
        "➕ افزودن سرور پروکسی\n" + LINE + "\n"
        "اطلاعات SSH سرور (خام) را در یک خط بفرست، با این قالب:\n\n"
        "`host:port:user:pass`\n\n"
        "مثال: `1.2.3.4:22:root:mypassword`\n"
        "(پورت پروکسی پیش‌فرض 1080 است؛ ربات خودش Docker و پروکسی را نصب می‌کند.)",
        buttons=[[Button.inline("🔙 لغو", b"proxies")]],
    )


@bot.on(events.CallbackQuery(data=b"px_check"))
async def px_check_cb(event):
    if not is_owner(event):
        return
    items = db.list_proxies()
    if not items:
        await event.answer("سروری برای چک نیست.", alert=True)
        return
    await event.answer("در حال چک سلامت همه پروکسی‌ها ...")
    await run_health_check()
    await proxies_cb(event)


@bot.on(events.CallbackQuery(pattern=b"px_(\\d+)"))
async def px_detail_cb(event):
    if not is_owner(event):
        return
    pid = int(event.pattern_match.group(1))
    p = db.get_proxy(pid)
    if not p:
        await event.answer("یافت نشد.", alert=True)
        return
    emoji = proxy_status_emoji(p)
    text = (
        "🔌 سرور پروکسی\n" + LINE + "\n"
        f"آی‌پی    : {p['host']}\n"
        f"پورت پروکسی: {p['proxy_port']}\n"
        f"وضعیت    : {emoji} {p.get('status')}\n"
        f"پینگ     : {p.get('ping_ms',0)}ms\n"
        f"آپلود    : {'✅' if p.get('upload_ok') else '❌'}\n"
        f"آخرین چک : {p.get('checked_at') or '-'}"
    )
    await event.edit(text, buttons=[
        [Button.inline("🔧 نصب/نصب مجدد پروکسی", f"pxsetup_{pid}".encode())],
        [Button.inline("🧪 تست این سرور", f"pxtest_{pid}".encode())],
        [Button.inline("🗑 حذف", f"pxdel_{pid}".encode())],
        [Button.inline("🔙 بازگشت", b"proxies")],
    ])


@bot.on(events.CallbackQuery(pattern=b"pxsetup_(\\d+)"))
async def px_setup_cb(event):
    if not is_owner(event):
        return
    pid = int(event.pattern_match.group(1))
    await event.edit("🔧 در حال نصب Docker و پروکسی روی سرور ... (ممکن است چند دقیقه طول بکشد)")
    ok, msg = await asyncio.to_thread(pm.setup_proxy, pid)
    p = db.get_proxy(pid)
    if ok:
        # after install, test it right away
        ping_ms, upload_ok, status = await asyncio.to_thread(pm.test_proxy, p)
        db.update_proxy_health(pid, status, ping_ms, upload_ok)
        await log(card("PROXY ADDED 🔌", [
            f"🖥 {p['host']}",
            f"وضعیت: {proxy_status_emoji(db.get_proxy(pid))} {status} — {ping_ms}ms",
            f"آپلود: {'✅' if upload_ok else '❌'}",
            f"🕒 {now()}",
        ]))
        await event.edit(f"✅ نصب شد: {msg}\nوضعیت: {status} — {ping_ms}ms — آپلود {'✅' if upload_ok else '❌'}",
                         buttons=[[Button.inline("🔙 بازگشت", b"proxies")]])
    else:
        await event.edit(f"❌ نصب ناموفق: {msg}",
                         buttons=[[Button.inline("🔙 بازگشت", f"px_{pid}".encode())]])


@bot.on(events.CallbackQuery(pattern=b"pxtest_(\\d+)"))
async def px_test_cb(event):
    if not is_owner(event):
        return
    pid = int(event.pattern_match.group(1))
    p = db.get_proxy(pid)
    if not p:
        await event.answer("یافت نشد.", alert=True)
        return
    await event.answer("در حال تست ...")
    ping_ms, upload_ok, status = await asyncio.to_thread(pm.test_proxy, p)
    db.update_proxy_health(pid, status, ping_ms, upload_ok)
    await px_detail_cb(event)


@bot.on(events.CallbackQuery(pattern=b"pxdel_(\\d+)"))
async def px_del_cb(event):
    if not is_owner(event):
        return
    pid = int(event.pattern_match.group(1))
    db.delete_proxy(pid)
    await event.edit("سرور پروکسی حذف شد. ✅",
                     buttons=[[Button.inline("🔙 بازگشت", b"proxies")]])


async def run_health_check():
    """Test all proxies and post a status card to the log group."""
    results = await asyncio.to_thread(pm.health_check_all)
    if not results:
        return
    rows = []
    for p in results:
        emoji = proxy_status_emoji(p)
        up = "✅" if p.get("upload_ok") else "❌"
        rows.append(f"{emoji} {p['host']} — {p.get('ping_ms',0)}ms — آپلود {up}")
    rows.append(f"🕒 {now()}")
    await log(card("🔌 وضعیت پروکسی‌ها", rows))


async def health_check_loop():
    """Background task: health-check every 30 minutes."""
    while True:
        await asyncio.sleep(1800)  # 30 min
        try:
            if db.list_proxies():
                await run_health_check()
        except Exception as e:  # noqa: BLE001
            print(f"[health_check_loop] {e}")


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #
@bot.on(events.CallbackQuery(data=b"backup"))
async def backup_cb(event):
    if not is_owner(event):
        return
    if os.path.exists(db.DB_PATH):
        await bot.send_file(event.sender_id, db.DB_PATH, caption=f"💾 Database backup • {now()}")
        await event.answer("بکاپ ارسال شد.")
    else:
        await event.answer("هنوز دیتابیسی وجود ندارد.", alert=True)


# --------------------------------------------------------------------------- #
# Message router for the active conversation step
# --------------------------------------------------------------------------- #
@bot.on(events.NewMessage)
async def message_router(event):
    if not is_owner(event):
        return
    if event.raw_text.startswith("/start"):
        return

    st = state.get(event.sender_id)
    if not st:
        return
    step = st.get("step")

    if step == "await_phone":
        await handle_phone(event)
    elif step == "await_code":
        await handle_code(event)
    elif step == "await_password":
        await handle_password(event)
    elif step == "await_content":
        await handle_content(event)
    elif step == "await_proxy":
        await handle_proxy(event)


async def handle_proxy(event):
    """Parse 'host:port:user:pass' and add a proxy server, then install it."""
    state.pop(event.sender_id, None)
    raw = event.raw_text.strip()
    parts = raw.split(":")
    if len(parts) < 4:
        await event.respond(
            "❌ قالب اشتباه است. باید این‌طور باشد:\n`host:port:user:pass`",
            buttons=[[Button.inline("🔙 بازگشت", b"proxies")]],
        )
        return
    host = parts[0].strip()
    try:
        ssh_port = int(parts[1].strip())
    except ValueError:
        ssh_port = 22
    ssh_user = parts[2].strip()
    ssh_pass = ":".join(parts[3:]).strip()  # password may contain ':'

    # default proxy settings (auto-generated credentials)
    proxy_port = 1080
    proxy_user = "rubika"
    proxy_pass = "rubika" + str(abs(hash(host)) % 100000)

    pid = db.add_proxy(host, ssh_port, ssh_user, ssh_pass, proxy_port, proxy_user, proxy_pass)
    await event.respond(
        f"✅ سرور `{host}` ثبت شد.\n🔧 در حال نصب Docker و پروکسی ... (چند دقیقه صبر کن)",
    )
    ok, msg = await asyncio.to_thread(pm.setup_proxy, pid)
    if not ok:
        await event.respond(f"❌ نصب ناموفق: {msg}",
                            buttons=[[Button.inline("🔙 پروکسی‌ها", b"proxies")]])
        return
    # test right after install (the key upload test)
    p = db.get_proxy(pid)
    ping_ms, upload_ok, status = await asyncio.to_thread(pm.test_proxy, p)
    db.update_proxy_health(pid, status, ping_ms, upload_ok)
    await log(card("PROXY ADDED 🔌", [
        f"🖥 {host}",
        f"وضعیت: {proxy_status_emoji(db.get_proxy(pid))} {status} — {ping_ms}ms",
        f"آپلود: {'✅' if upload_ok else '❌'}",
        f"🕒 {now()}",
    ]))
    verdict = "✅ آماده و سالم" if upload_ok else "⚠️ نصب شد ولی آپلود روبیکا جواب نداد"
    await event.respond(
        f"{verdict}\n🖥 {host}\nوضعیت: {status} — {ping_ms}ms — آپلود {'✅' if upload_ok else '❌'}",
        buttons=[[Button.inline("🔙 پروکسی‌ها", b"proxies")]],
    )


async def handle_phone(event):
    phone = event.raw_text.strip()
    await event.respond("⏳ در حال اتصال به روبیکا و ارسال کد ...")
    try:
        ctx = await rb.start_login(phone)
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ خطا در ارسال کد: {e}\nدوباره شماره را بفرست یا لغو کن.")
        return

    pending[event.sender_id] = ctx
    status = str(ctx.get("status") or "").upper()

    # If the account has 2FA, send_code asks for the password first.
    if "PASS" in status:
        hint = ctx.get("hint") or ""
        state[event.sender_id] = {"step": "await_password"}
        await event.respond(
            "🔐 این اکانت رمز دومرحله‌ای دارد." + (f"\nراهنما: {hint}" if hint else "") +
            "\nرمز را بفرست.",
            buttons=[[Button.inline("🔙 لغو", b"cancel")]],
        )
        return

    if not ctx.get("phone_code_hash"):
        try:
            await ctx["client"].disconnect()
        except Exception:  # noqa: BLE001
            pass
        pending.pop(event.sender_id, None)
        await event.respond(
            f"❌ روبیکا کد نفرستاد (status: {status or 'نامشخص'}). "
            "شماره را درست بفرست یا کمی بعد دوباره امتحان کن."
        )
        return

    state[event.sender_id] = {"step": "await_code"}
    await event.respond(
        "📩 کد ورود در اپ روبیکا برایت آمد.\n"
        "کد را بفرست (با فاصله یا بدون فاصله، هر دو قبول است).",
        buttons=[[Button.inline("🔙 لغو", b"cancel")]],
    )


async def handle_code(event):
    ctx = pending.get(event.sender_id)
    if not ctx:
        state.pop(event.sender_id, None)
        return
    code = "".join(ch for ch in event.raw_text if ch.isdigit())
    try:
        await rb.finish_login(ctx, code)
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ کد اشتباه یا خطا: {e}\nدوباره کد را بفرست یا لغو کن.")
        return
    await complete_account(event)


async def handle_password(event):
    ctx = pending.get(event.sender_id)
    if not ctx:
        state.pop(event.sender_id, None)
        return
    password = event.raw_text.strip()
    # In rubpy, the 2FA password is supplied to send_code(pass_key=...),
    # which then returns a fresh phone_code_hash. We restart the code phase.
    try:
        new_ctx = await rb.start_login(ctx["phone"], pass_key=password)
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ رمز اشتباه یا خطا: {e}\nدوباره رمز را بفرست.")
        return
    status = str(new_ctx.get("status") or "").upper()
    if status not in ("OK", "SEND_PASS_KEY", ""):
        await event.respond(f"❌ رمز پذیرفته نشد (status: {status}). دوباره رمز را بفرست.")
        return
    pending[event.sender_id] = new_ctx
    state[event.sender_id] = {"step": "await_code"}
    await event.respond(
        "🔓 رمز پذیرفته شد. حالا کد ورود که در اپ روبیکا آمد را بفرست.",
        buttons=[[Button.inline("🔙 لغو", b"cancel")]],
    )


async def complete_account(event):
    ctx = pending.pop(event.sender_id, None)
    state.pop(event.sender_id, None)
    if not ctx:
        return
    client = ctx["client"]
    phone = ctx["phone"]
    try:
        me = await client.get_me()
        guid = rb._guid_of(me) or "-"
        first = rb._name_of(me)
        contacts, groups = await rb.get_recipients(client)
        account_id = db.add_account(phone, first, str(guid), rb.session_path(phone))

        await log(card("NEW ACCOUNT ➕", [
            f"📱 Phone : {phone}",
            f"🕒 Time  : {now()}",
        ]))
        await log(card("LOGIN SUCCESS ✅", [
            f"📱 Phone : {phone}",
            f"🕒 Time  : {now()}",
        ]))
        await log(card("ACCOUNT STATUS 📇", [
            f"👤 Name      {first}",
            f"📱 Phone     {phone}",
            f"🆔 ID        {guid}",
            "━━━━━━━━━━━━━━━━━━",
            f"📇 Contacts  {len(contacts)}",
            f"👥 Groups    {len(groups)}",
        ]))

        await event.respond(
            "✅ اکانت با موفقیت اضافه شد!\n"
            f"👤 {first} | 📱 {phone}\n"
            f"📇 مخاطبین: {len(contacts)} | 👥 گروه‌ها: {len(groups)}\n\n"
            "میتوانی همین حالا ارسال را شروع کنی 👇",
            buttons=[
                [Button.inline("🚀 شروع ارسال", f"send_{account_id}".encode())],
                [Button.inline("🏠 منوی اصلی", b"home")],
            ],
        )
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ خطا بعد از ورود: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def handle_content(event):
    msg = event.message
    if msg.photo:
        path = await msg.download_media(file=MEDIA_DIR)
        db.set_content("photo", msg.text or None, path)
        await event.respond("✅ عکس به‌عنوان محتوای ارسالی ذخیره شد.", buttons=main_menu())
    elif msg.document:
        path = await msg.download_media(file=MEDIA_DIR)
        db.set_content("file", msg.text or None, path)
        await event.respond("✅ فایل به‌عنوان محتوای ارسالی ذخیره شد.", buttons=main_menu())
    elif msg.text:
        db.set_content("text", msg.text, None)
        await event.respond("✅ متن به‌عنوان محتوای ارسالی ذخیره شد.", buttons=main_menu())
    else:
        await event.respond("❌ این نوع محتوا پشتیبانی نمی‌شود. متن، عکس یا فایل بفرست.")
        return
    state.pop(event.sender_id, None)


# --------------------------------------------------------------------------- #
# Sending worker (sequential, stops on first failure)
# --------------------------------------------------------------------------- #
async def do_send(account_id: int):
    acc = db.get_account(account_id)
    if not acc:
        return

    use_forward = bool(config.FORWARD_MARKER)
    content = db.get_content()
    if not use_forward and not content.get("content_type"):
        await log("⚠️ Broadcast cancelled: no content configured.")
        return

    # initialise everything the except/finally blocks may touch, so an early
    # error (e.g. in connect_ready) can't cause a second NameError crash.
    success = 0
    fail = 0
    total = 0
    sent_set = set()
    error_detail = ""

    # Pick a healthy proxy (round-robin) to route Rubika traffic through an
    # Iranian server. If none configured/healthy, connect directly.
    chosen_proxy = pm.next_healthy_proxy()
    proxy_str = pm.proxy_url(chosen_proxy) if chosen_proxy else None
    proxy_label = chosen_proxy["host"] if chosen_proxy else "بدون پروکسی (مستقیم)"

    client = rb.open_client(acc["phone"], proxy_str=proxy_str)
    try:
        # connect_ready rebuilds the signing keys (fixes 'NoneType has no sign')
        await rb.connect_ready(client)

        # --- Resolve what we will send ---
        # In forward mode we DON'T forward (Rubika rate-limits forwards hard).
        # Instead we DOWNLOAD the marked file ONCE and send it DIRECTLY with
        # send_document to each recipient (direct sends are not rate-limited
        # the way forwards are).
        marked_path = None
        marked_caption = ""
        marked_name = None
        if use_forward:
            try:
                marked_path, marked_caption, marked_name = await rb.download_marked_file(
                    client, config.FORWARD_MARKER
                )
            except Exception as e:  # noqa: BLE001
                await log(card("TEST FAILED ⚠️", [
                    f"📱 Phone : {acc['phone']}",
                    f"💥 Error : {e}",
                    "Could not read Saved Messages.",
                ]))
                return
            if not marked_path:
                await log(card("MARKER NOT FOUND ⚠️", [
                    f"📱 Phone : {acc['phone']}",
                    f"🔖 Marker: {config.FORWARD_MARKER}",
                    "یک فایل در Saved Messages بگذار که کپشنش به مارکر ختم شود.",
                ]))
                return
        else:
            # Health test: send to Saved Messages first
            try:
                await rb.send_to_saved(client, content)
            except Exception as e:  # noqa: BLE001
                db.set_status(account_id, "dead")
                await log(card("TEST FAILED ⚠️", [
                    f"📱 Phone : {acc['phone']}",
                    f"💥 Error : {e}",
                    "Account may be logged out or banned.",
                ]))
                return

        recipients, rstats = await rb.get_ordered_recipients(client)
        total = len(recipients)
        n_contacts = rstats["contacts"]
        n_groups = rstats["groups"]
        n_with_chat = rstats["with_chat"]
        n_no_target = rstats["no_target"]

        caption = marked_caption if use_forward else content.get("content_text")

        # Log: broadcast started
        await log(card("BROADCAST STARTED 🚀", [
            f"👤 Account : {acc['name'] or '-'}",
            f"📱 Phone : {acc['phone']}",
            f"🔌 Proxy : {proxy_label}",
            f"🕒 Started : {now()}",
            LINE,
            f"📇 Contacts : {n_contacts}  (💬 with chat: {n_with_chat})",
            f"👥 Groups : {n_groups}",
            f"🎯 Targets : {total}",
        ]))

        started = datetime.now()

        # Resume support: skip recipients already sent to in a previous run.
        already = db.get_sent_guids(account_id)
        sent_set = set(already)
        stopped_reason = None
        if already:
            await log(card("RESUME ▶️", [
                f"📱 {acc['phone']}",
                f"قبلاً ارسال‌شده: {len(already)} — ادامه از همان‌جا",
                f"🕒 {now()}",
            ]))

        # Per-send timeout so a stuck upload (e.g. 502 from Rubika media server)
        # can NEVER hang the whole broadcast.
        SEND_TIMEOUT = 60

        async def _send_to_guid(guid):
            if use_forward:
                await rb.send_document_direct(client, guid, marked_path, marked_caption,
                                              file_name=marked_name)
            else:
                await rb.send_content(client, guid, content)

        async def _send_guarded(guid):
            return await asyncio.wait_for(_send_to_guid(guid), timeout=SEND_TIMEOUT)

        # ---- STEP 1: probe the first 5 NEW recipients ----
        probe_targets = [g for g in recipients if g not in sent_set][:5]
        probe_ok = 0
        for guid in probe_targets:
            if stop_flags.get(account_id):
                stopped_reason = "manual"
                break
            try:
                await _send_guarded(guid)
                success += 1
                probe_ok += 1
                sent_set.add(guid)
            except Exception as e:  # noqa: BLE001
                fail += 1
                error_detail = repr(e)[:120]
        db.save_sent_guids(account_id, sent_set)

        # If the probe didn't succeed at all -> account/file is being blocked.
        if stopped_reason != "manual" and probe_ok == 0:
            await log(card("TEST FAILED ⚠️", [
                f"📱 {acc['phone']}",
                f"✅ تست ۵ نفر اول: {probe_ok}/{len(probe_targets)}",
                f"💥 {error_detail or 'روبیکا ارسال را قبول نکرد'}",
                f"🕒 {now()}",
            ]))
            stopped_reason = "test_failed"

        # ---- STEP 2: probe ok -> send to the rest ----
        if stopped_reason is None:
            await log(card("TEST OK ✅ — ادامه ارسال", [
                f"📱 {acc['phone']}",
                f"✅ تست: {probe_ok}/{len(probe_targets)}  → ارسال به بقیه",
                f"🕒 {now()}",
            ]))
            rest = [g for g in recipients if g not in sent_set]
            since_save = 0
            for guid in rest:
                if stop_flags.get(account_id):
                    stopped_reason = "manual"
                    break
                try:
                    await _send_guarded(guid)
                    success += 1
                    sent_set.add(guid)
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    error_detail = repr(e)[:120]
                since_save += 1
                # summary progress log every 20, and save progress for resume
                if since_save >= 20:
                    db.save_sent_guids(account_id, sent_set)
                    await log(card("PROGRESS 📤", [
                        f"📱 {acc['phone']}",
                        f"✅ {success}   ❌ {fail}   🎯 {total}",
                        f"🕒 {now()}",
                    ]))
                    since_save = 0
                await asyncio.sleep(config.SEND_DELAY)
            db.save_sent_guids(account_id, sent_set)

        # broadcast finished or stopped -> clear progress if fully done
        if stopped_reason is None:
            db.clear_progress(account_id)

        duration = int((datetime.now() - started).total_seconds())
        rate = f"{(success / total * 100):.0f}%" if total else "0%"
        finished_rows = [
            f"🟢 Status : {'Completed' if not stopped_reason else 'Stopped'}",
            f"👤 Account : {acc['name'] or '-'} · {acc['phone']}",
            LINE,
            f"✅ {success}   ❌ {fail}   🎯 {total}",
            f"📊 Success rate : {rate}",
            f"⏱ Duration : {duration}s",
        ]
        if stopped_reason and error_detail:
            finished_rows.append(f"💥 آخرین خطا: {error_detail}")
        if n_no_target:
            finished_rows.append(f"⚠️ No target : {n_no_target}")
        finished_rows.append(f"🕒 {now()}")

        title = "BROADCAST FINISHED 🏁"
        if stopped_reason == "manual":
            title = "BROADCAST STOPPED 🛑"
        elif stopped_reason == "test_failed":
            title = "BROADCAST STOPPED ⚠️"
        await log(card(title, finished_rows))
    except Exception as e:  # noqa: BLE001
        # Always report, even on an unexpected crash, with how far we got.
        try:
            db.save_sent_guids(account_id, sent_set)
        except Exception:  # noqa: BLE001
            pass
        await log(card("BROADCAST ERROR ❌", [
            f"📱 Phone : {acc['phone']}",
            f"✅ ارسال‌شده تا اینجا: {success} / {total}",
            f"💥 Error : {repr(e)[:150]}",
            f"🕒 {now()}",
        ]))
    finally:
        stop_flags.pop(account_id, None)
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def send_worker():
    """Process the send queue one account at a time (sequential)."""
    while True:
        account_id = await send_queue.get()
        try:
            await do_send(account_id)
        except Exception as e:  # noqa: BLE001
            print(f"[send_worker error] {e}")
        finally:
            send_queue.task_done()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def main():
    problems = config.validate()
    if problems:
        print("Configuration problems found in .env:")
        for p in problems:
            print(f"  - {p}")
        print("Fix the .env file and run again.")
        return

    db.init()
    await bot.start(bot_token=config.BOT_TOKEN)

    asyncio.create_task(send_worker())
    asyncio.create_task(health_check_loop())

    me = await bot.get_me()
    print(f"Rubika panel bot is running as @{me.username}. Press Ctrl+C to stop.")
    try:
        await log(card("PANEL ONLINE 🤖", [f"🕒 {now()}"]))
    except Exception:  # noqa: BLE001
        pass

    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
