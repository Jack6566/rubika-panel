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
# Log styling (Style 3 - luxury minimal, English)
# --------------------------------------------------------------------------- #
TOP = "✦ ━━━━━━━━━━━━━━━━ ✦"


def card(title: str, rows: list) -> str:
    body = "\n".join(rows)
    return f"{TOP}\n      {title}\n{TOP}\n\n{body}"


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
        [Button.inline("➕ افزودن اکانت", b"add_account")],
        [Button.inline("👥 مدیریت اکانت‌ها", b"manage_accounts")],
        [Button.inline("⚙️ تنظیم محتوای ارسالی", b"set_content")],
        [Button.inline("💾 بکاپ گرفتن", b"backup")],
    ]


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
    await event.respond(
        "👋 خوش آمدی به پنل مدیریت اکانت‌های روبیکا.\nیکی از گزینه‌ها را انتخاب کن:",
        buttons=main_menu(),
    )


@bot.on(events.CallbackQuery(data=b"home"))
async def home_cb(event):
    if not is_owner(event):
        return
    state.pop(event.sender_id, None)
    await event.edit("👋 منوی اصلی:", buttons=main_menu())


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
        f"👤 اکانت: {acc['name'] or '-'}\n"
        f"📱 شماره: {acc['phone']}\n"
        f"🆔 آیدی: {acc['user_id']}\n"
        f"📅 افزوده‌شده: {acc['added_at']}\n"
        f"وضعیت: {status}"
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
    if not content.get("content_type"):
        await event.answer("اول محتوای ارسالی را تنظیم کن.", alert=True)
        return
    await event.edit(
        f"🚀 شروع ارسال با اکانت {acc['phone']}؟\n\n"
        f"محتوایی که ارسال می‌شود:\n{content_summary(content)}\n\n"
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


async def handle_phone(event):
    phone = event.raw_text.strip()
    await event.respond("⏳ در حال اتصال به روبیکا و ارسال کد ...")
    try:
        ctx = await rb.start_login(phone)
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ خطا در ارسال کد: {e}\nدوباره شماره را بفرست یا لغو کن.")
        return
    if not ctx.get("phone_code_hash"):
        try:
            await ctx["client"].disconnect()
        except Exception:  # noqa: BLE001
            pass
        await event.respond("❌ روبیکا کد نفرستاد (hash خالی). شماره را درست بفرست یا کمی بعد دوباره امتحان کن.")
        return
    pending[event.sender_id] = ctx
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
        result = await rb.finish_login(ctx, code)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "pass" in msg or "2fa" in msg or "two" in msg:
            state[event.sender_id] = {"step": "await_password"}
            await event.respond(
                "🔐 این اکانت رمز دومرحله‌ای دارد. رمز را بفرست.",
                buttons=[[Button.inline("🔙 لغو", b"cancel")]],
            )
            return
        await event.respond(f"❌ کد اشتباه یا خطا: {e}\nدوباره کد را بفرست یا لغو کن.")
        return

    # Some versions signal 2FA need via the result status instead of raising.
    try:
        if await rb.needs_password(result):
            state[event.sender_id] = {"step": "await_password"}
            await event.respond(
                "🔐 این اکانت رمز دومرحله‌ای دارد. رمز را بفرست.",
                buttons=[[Button.inline("🔙 لغو", b"cancel")]],
            )
            return
    except Exception:  # noqa: BLE001
        pass

    await complete_account(event)


async def handle_password(event):
    ctx = pending.get(event.sender_id)
    if not ctx:
        state.pop(event.sender_id, None)
        return
    password = event.raw_text.strip()
    try:
        # Retry sign-in including the 2FA password.
        await ctx["client"].sign_in(
            phone_code=ctx.get("last_code", ""),
            phone_number=ctx["phone"],
            phone_code_hash=ctx["phone_code_hash"],
            public_key=ctx["public_key"],
            password=password,
        )
    except Exception as e:  # noqa: BLE001
        await event.respond(f"❌ رمز اشتباه یا خطا: {e}\nدوباره رمز را بفرست.")
        return
    await complete_account(event)


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
    content = db.get_content()
    if not content.get("content_type"):
        await log("⚠️ Broadcast cancelled: no content configured.")
        return

    client = rb.open_client(acc["phone"])
    try:
        await client.connect()

        # --- Health test: send to Saved Messages first ---
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

        contacts, groups = await rb.get_recipients(client)
        recipients = [g for g, _ in contacts] + [g for g, _ in groups]
        total = len(recipients)

        type_label = {"text": "Text", "photo": "Photo", "file": "File"}.get(
            content["content_type"], "Content"
        )
        caption = content.get("content_text")
        is_media = content["content_type"] != "text"

        # Log: broadcast started
        await log(card("BROADCAST STARTED 🚀", [
            f"👤 Account   {acc['name'] or '-'}",
            f"📱 Phone     {acc['phone']}",
            f"🕒 Started   {now()}",
            f"📦 Content   {type_label}" + (" + caption" if caption and is_media else ""),
            "━━━━━━━━━━━━━━━━━━",
            f"📇 Contacts  {len(contacts)}",
            f"👥 Groups    {len(groups)}",
            f"🎯 Targets   {total}",
        ]))

        success = 0
        fail = 0
        stopped_reason = None
        started = datetime.now()
        for guid in recipients:
            if stop_flags.get(account_id):
                stopped_reason = "manual"
                break
            try:
                await rb.send_content(client, guid, content)
                success += 1
            except Exception:  # noqa: BLE001
                fail += 1
                stopped_reason = "error"
                break  # STOP IMMEDIATELY on first failure
            await asyncio.sleep(config.SEND_DELAY)

        duration = int((datetime.now() - started).total_seconds())
        rate = f"{(success / total * 100):.0f}%" if total else "0%"
        suffix = ""
        if stopped_reason == "manual":
            suffix = " (manually stopped)"
        elif stopped_reason == "error":
            suffix = " (stopped on failure)"

        await log(card(f"BROADCAST FINISHED 🏁{suffix}", [
            f"🟢 Status    {'Completed' if not stopped_reason else 'Stopped'}",
            f"👤 Account   {acc['name'] or '-'} · {acc['phone']}",
            "━━━━━━━━━━━━━━━━━━",
            f"✅ {success}   ❌ {fail}   🎯 {total}",
            f"📊 Success rate: {rate}",
            f"⏱ Duration: {duration}s",
            f"🕒 {now()}",
        ]))
    except Exception as e:  # noqa: BLE001
        await log(card("BROADCAST ERROR ❌", [
            f"📱 Phone : {acc['phone']}",
            f"💥 Error : {e}",
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

    me = await bot.get_me()
    print(f"Rubika panel bot is running as @{me.username}. Press Ctrl+C to stop.")
    try:
        await log(card("PANEL ONLINE 🤖", [f"🕒 {now()}"]))
    except Exception:  # noqa: BLE001
        pass

    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
