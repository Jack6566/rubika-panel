"""
Interactive Rubika login (run this on the server).
==================================================

Why a separate script?
  rubpy 7.x logs in with an encrypted handshake (RSA public_key etc.). The most
  robust way to do that is to let rubpy run its OWN interactive login, which
  generates the keys and saves the session file itself. After that, the panel
  (bot.py) just opens the saved session and sends messages.

Usage on the server (inside the venv):
    python login.py

It will:
  1. ask for the phone number,
  2. trigger Rubika to send the code to your Rubika app,
  3. ask you to paste the code (rubpy handles the public_key internally),
  4. save the session, read contacts/groups counts,
  5. register the account into the panel's database,
  6. report to the Telegram log group (best-effort).

Then start/restart the panel and the account will appear in "مدیریت اکانت‌ها".
"""
import asyncio

from rubpy import Client

import db
import rubika_client as rb


async def amain():
    db.init()

    phone = input("Rubika phone number (e.g. 989123456789 or 0912...): ").strip()
    if not phone:
        print("No phone entered. Aborting.")
        return

    print("\nConnecting to Rubika and requesting the login code ...")
    print("Check your Rubika app; it will receive a login code shortly.\n")

    ctx = await rb.start_login(phone)
    if not ctx.get("phone_code_hash"):
        try:
            await ctx["client"].disconnect()
        except Exception:
            pass
        print("Rubika did not return a code hash. Check the number and try again.")
        return

    code = input("Enter the login code from your Rubika app: ").strip()
    await rb.finish_login(ctx, code)

    client = ctx["client"]
    me = await client.get_me()
    guid = rb._guid_of(me) or "-"
    name = rb._name_of(me)
    contacts, groups = await rb.get_recipients(client)
    try:
        await client.disconnect()
    except Exception:
        pass

    account_id = db.add_account(phone, name, str(guid), rb.session_path(rb.normalize_phone(phone)))

    print("\n========================================")
    print("  LOGIN SUCCESSFUL")
    print("========================================")
    print(f"  Name     : {name}")
    print(f"  Phone    : {phone}")
    print(f"  GUID     : {guid}")
    print(f"  Contacts : {len(contacts)}")
    print(f"  Groups   : {len(groups)}")
    print(f"  DB id    : {account_id}")
    print("========================================")
    print("\nNow (re)start the panel:  systemctl restart rubika-panel")
    print("The account will appear under 'مدیریت اکانت‌ها' in the Telegram bot.")

    # Best-effort: report to the Telegram log group via the panel bot token.
    try:
        await _notify_telegram(phone, name, guid, len(contacts), len(groups))
    except Exception as e:  # noqa: BLE001
        print(f"(log group notify skipped: {e})")


async def _notify_telegram(phone, name, guid, n_contacts, n_groups):
    """Send a styled login card to the Telegram log group (best-effort)."""
    import config
    from telethon import TelegramClient

    if not (config.BOT_TOKEN and config.LOG_GROUP_ID):
        return
    TOP = "✦ ━━━━━━━━━━━━━━━━ ✦"
    text = (
        f"{TOP}\n      ACCOUNT ADDED ✅\n{TOP}\n\n"
        f"👤 Name      {name}\n"
        f"📱 Phone     {phone}\n"
        f"🆔 ID        {guid}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📇 Contacts  {n_contacts}\n"
        f"👥 Groups    {n_groups}"
    )
    tg = TelegramClient("data/login_notify", config.API_ID, config.API_HASH)
    await tg.start(bot_token=config.BOT_TOKEN)
    try:
        await tg.send_message(config.LOG_GROUP_ID, text)
    finally:
        await tg.disconnect()


if __name__ == "__main__":
    asyncio.run(amain())
