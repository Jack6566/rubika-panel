"""
Proxy manager: turn a raw SSH server into a working SOCKS5 proxy and keep it
healthy.
=========================================================================

Flow:
  1. You send SSH details (host:port user:pass) to the Telegram panel.
  2. setup_proxy() SSHes in (paramiko), installs Docker if missing, then runs a
     small SOCKS5 proxy container (serjs/go-socks5-proxy) with user/pass.
  3. health_check tests EACH proxy by routing a request THROUGH it to Rubika's
     UPLOAD server (the one that was giving 503). Only proxies whose upload test
     passes are marked usable. Result: ping (ms) + upload_ok + colour status.
  4. The sender picks healthy proxies round-robin; on failure it fails over to
     the next healthy one (only at send time).

All SSH/proxy specifics live HERE so the rest of the bot stays clean.
Requires: paramiko, requests[socks]  (see requirements.txt)
"""
import time

import db

# Rubika servers we test reachability against (the upload one is the key check).
RUBIKA_UPLOAD = "https://upmessenger490.iranlms.ir/UploadFile.ashx"

# thresholds for colour status (ms)
GREEN_MAX = 700      # <=700ms => green (fast)
YELLOW_MAX = 2000    # <=2000ms => yellow (medium); above => red


def _ssh_connect(p: dict):
    """Open an SSH connection to a proxy server. Returns a paramiko client."""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=p["host"],
        port=int(p.get("ssh_port") or 22),
        username=p["ssh_user"],
        password=p["ssh_pass"],
        timeout=25,
        banner_timeout=25,
        auth_timeout=25,
    )
    return client


def _run(client, cmd, timeout=180):
    """Run a command over SSH, return (exit_code, stdout, stderr)."""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def setup_proxy(proxy_id: int) -> tuple:
    """Install Docker (if needed) and run a SOCKS5 proxy container on the server.

    Returns (ok: bool, message: str).
    """
    p = db.get_proxy(proxy_id)
    if not p:
        return False, "proxy not found"

    port = int(p.get("proxy_port") or 1080)
    puser = p.get("proxy_user") or "rubika"
    ppass = p.get("proxy_pass") or "rubika123"

    try:
        client = _ssh_connect(p)
    except Exception as e:  # noqa: BLE001
        return False, f"SSH failed: {repr(e)[:120]}"

    try:
        # 1) install docker if missing (raw server support)
        code, out, _ = _run(client, "command -v docker || echo NO")
        if "NO" in out:
            _run(client, "curl -fsSL https://get.docker.com | sh", timeout=600)
            _run(client, "systemctl enable --now docker || service docker start || true")

        # verify docker now exists
        code, out, _ = _run(client, "command -v docker || echo NO")
        if "NO" in out:
            return False, "Docker install failed on server"

        # 2) remove any old container with same name, then run a fresh one
        name = f"rubikaproxy_{port}"
        _run(client, f"docker rm -f {name} 2>/dev/null || true")
        run_cmd = (
            f"docker run -d --restart=always --name {name} "
            f"-p {port}:1080 "
            f"-e PROXY_USER={puser} -e PROXY_PASSWORD={ppass} "
            f"serjs/go-socks5-proxy"
        )
        code, out, err = _run(client, run_cmd, timeout=300)
        if code != 0:
            return False, f"docker run failed: {(err or out)[:120]}"

        return True, f"proxy container running on :{port}"
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def proxy_url(p: dict) -> str:
    """Build a socks5 URL string for a proxy row."""
    user = p.get("proxy_user")
    pwd = p.get("proxy_pass")
    host = p["host"]
    port = int(p.get("proxy_port") or 1080)
    if user and pwd:
        return f"socks5://{user}:{pwd}@{host}:{port}"
    return f"socks5://{host}:{port}"


def test_proxy(p: dict) -> tuple:
    """Route a request THROUGH the proxy to Rubika's UPLOAD server.

    Returns (ping_ms, upload_ok, status_colour).
    The upload server is the one that was returning 503 from a bad IP, so this
    is the real test of whether the proxy is usable for sending files.
    """
    import requests
    url = proxy_url(p)
    proxies = {"http": url, "https": url}
    start = time.time()
    upload_ok = False
    try:
        r = requests.get(RUBIKA_UPLOAD, timeout=20, proxies=proxies)
        # any concrete HTTP answer (<500) means the server is reachable through
        # this proxy; 5xx/timeout means not usable.
        upload_ok = r.status_code < 500
    except Exception:  # noqa: BLE001
        upload_ok = False
    ping_ms = int((time.time() - start) * 1000)

    if not upload_ok:
        status = "red"
    elif ping_ms <= GREEN_MAX:
        status = "green"
    elif ping_ms <= YELLOW_MAX:
        status = "yellow"
    else:
        status = "red"
    return ping_ms, upload_ok, status


def health_check_all() -> list:
    """Test every proxy, update the DB, and return the rows with fresh results."""
    results = []
    for p in db.list_proxies():
        ping_ms, upload_ok, status = test_proxy(p)
        db.update_proxy_health(p["id"], status, ping_ms, upload_ok)
        results.append(db.get_proxy(p["id"]))
    return results


# --------------------------------------------------------------------------- #
# Round-robin selection for sending
# --------------------------------------------------------------------------- #
_rr_index = {"i": 0}


def next_healthy_proxy():
    """Return the next healthy proxy (round-robin), or None if none are healthy."""
    healthy = db.healthy_proxies()
    if not healthy:
        return None
    i = _rr_index["i"] % len(healthy)
    _rr_index["i"] = (i + 1) % len(healthy)
    return healthy[i]


def proxy_tuple(p: dict):
    """rubpy/python-socks proxy tuple for Client(proxy=...)."""
    host = p["host"]
    port = int(p.get("proxy_port") or 1080)
    user = p.get("proxy_user")
    pwd = p.get("proxy_pass")
    if user and pwd:
        return ("socks5", host, port, True, user, pwd)
    return ("socks5", host, port)
