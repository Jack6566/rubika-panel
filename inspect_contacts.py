"""
Inspect helper: prints the fields of the first few CONTACTS so we can find the
online / last-seen status field, to order recipients by it.

Run on the server (inside venv):
    python inspect_contacts.py 989395430422
"""
import asyncio
import sys

import rubika_client as rb


async def amain():
    if len(sys.argv) < 2:
        print("Usage: python inspect_contacts.py <phone_digits>")
        return
    phone = sys.argv[1]
    client = rb.open_client(phone)
    await rb.connect_ready(client)
    try:
        result = await client.get_contacts()
        users = getattr(result, "users", None)
        if users is None and isinstance(result, dict):
            users = result.get("users", [])
        users = users or []
        print(f"--- got {len(users)} contacts (showing up to 3) ---")
        for i, u in enumerate(users[:3]):
            print(f"\n=== contact #{i} ===")
            attrs = [a for a in dir(u) if not a.startswith("_")]
            print("ATTRS:", attrs)
            for a in attrs:
                try:
                    v = getattr(u, a)
                    if not callable(v):
                        print(f"  {a} = {v!r}")
                except Exception as e:  # noqa: BLE001
                    print(f"  {a} -> error {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(amain())
