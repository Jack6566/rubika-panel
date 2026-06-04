"""
Diagnostic test (run on the server):  python test_send.py 989131827256

It checks THREE things, clearly, so we know exactly what works:
  1) send a SMALL text file (.txt) directly to 3 contacts
  2) send a SMALL zip file directly to 3 contacts
  3) download the marked file from Saved Messages and send it to 3 contacts

This tells us: do other files send fine? is it only the apk/zip? or nothing?
"""
import asyncio
import sys
import zipfile

import rubika_client as rb
import config


async def try_send(client, guid, path, name, caption=""):
    try:
        await rb.send_document_direct(client, guid, path, caption, file_name=name)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, repr(e)


async def amain():
    if len(sys.argv) < 2:
        print("usage: python test_send.py <phone>")
        return
    phone = sys.argv[1]

    # build two small local test files
    txt_path = "/tmp/hello.txt"
    with open(txt_path, "w") as f:
        f.write("hello from test\n")
    zip_path = "/tmp/hello.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(txt_path, "hello.txt")

    client = rb.open_client(phone)
    await rb.connect_ready(client)

    contacts, groups = await rb.get_recipients(client)
    targets = [g for g, _ in contacts[:3]]
    print(f"targets: {len(targets)}")

    print("\n--- TEST 1: small .txt ---")
    ok = 0
    for t in targets:
        s, err = await try_send(client, t, txt_path, "hello.txt", "تست txt")
        print("OK" if s else f"FAIL {err}")
        ok += 1 if s else 0
        await asyncio.sleep(1)
    print(f"TXT result: {ok}/{len(targets)}")

    print("\n--- TEST 2: small .zip ---")
    ok = 0
    for t in targets:
        s, err = await try_send(client, t, zip_path, "hello.zip", "تست zip")
        print("OK" if s else f"FAIL {err}")
        ok += 1 if s else 0
        await asyncio.sleep(1)
    print(f"ZIP result: {ok}/{len(targets)}")

    print("\n--- TEST 3: the marked file from Saved Messages ---")
    path, caption, name = await rb.download_marked_file(client, config.FORWARD_MARKER)
    print(f"marked file: name={name} path={path}")
    if path:
        ok = 0
        for t in targets:
            s, err = await try_send(client, t, path, name, caption or "")
            print("OK" if s else f"FAIL {err}")
            ok += 1 if s else 0
            await asyncio.sleep(1)
        print(f"MARKED result: {ok}/{len(targets)}")
    else:
        print("marked file not found/downloaded")

    await client.disconnect()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(amain())
