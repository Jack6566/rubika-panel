"""
Inspect helper: prints the fields of the first few chats so we can find the
"last activity / time" field to order recipients by recent activity.

Run on the server (inside venv), passing the account phone (digits only):
    python inspect_chat.py 989395430422
"""
import asyncio
import sys

import rubika_client as rb


async def amain():
    if len(sys.argv) < 2:
        print("Usage: python inspect_chat.py <phone_digits>  e.g. 989395430422")
        return
    phone = sys.argv[1]
    client = rb.open_client(phone)
    # connect_ready rebuilds key/import_key so signed requests work
    await rb.connect_ready(client)
    try:
        result = await client.get_chats()
        chats = getattr(result, "chats", None)
        if chats is None and isinstance(result, dict):
            chats = result.get("chats", [])
        chats = chats or []
        print(f"--- got {len(chats)} chats (showing up to 3) ---")
        for i, ch in enumerate(chats[:3]):
            print(f"\n=== chat #{i} ===")
            attrs = [a for a in dir(ch) if not a.startswith("_")]
            print("ATTRS:", attrs)
            for a in attrs:
                try:
                    v = getattr(ch, a)
                    if not callable(v):
                        print(f"  {a} = {v!r}")
                except Exception as e:  # noqa: BLE001
                    print(f"  {a} -> error {e}")
        # also show the top-level result attrs (for next_start_id, timestamps)
        print("\n--- result attrs ---")
        print([a for a in dir(result) if not a.startswith("_")])
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(amain())
