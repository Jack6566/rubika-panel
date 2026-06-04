"""SQLite storage: Rubika accounts (with login sessions) and the shared content.

Everything lives in one file (data/data.db) so a single backup keeps all accounts safe.
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "data.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            phone     TEXT UNIQUE,
            name      TEXT,
            user_id   TEXT,
            session   TEXT,
            added_at  TEXT,
            status    TEXT DEFAULT 'active'
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            content_type TEXT,
            content_text TEXT,
            media_path   TEXT
        )
        """
    )
    c.execute(
        "INSERT OR IGNORE INTO settings (id, content_type, content_text, media_path) "
        "VALUES (1, NULL, NULL, NULL)"
    )
    # Proxy servers (SSH details + last health-check result). The bot SSHes in,
    # installs a Docker SOCKS5 proxy, then routes Rubika traffic through them.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS proxies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            host        TEXT,
            ssh_port    INTEGER DEFAULT 22,
            ssh_user    TEXT,
            ssh_pass    TEXT,
            proxy_port  INTEGER DEFAULT 1080,
            proxy_user  TEXT,
            proxy_pass  TEXT,
            status      TEXT DEFAULT 'unknown',
            ping_ms     INTEGER DEFAULT 0,
            upload_ok   INTEGER DEFAULT 0,
            added_at    TEXT,
            checked_at  TEXT
        )
        """
    )
    # Per-account broadcast progress, so a stopped/crashed run can resume from
    # where it left off (stores the guids already sent to).
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS progress (
            account_id INTEGER PRIMARY KEY,
            sent_guids TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


# ---------- broadcast progress (resume) ----------

def get_sent_guids(account_id: int) -> set:
    conn = _conn()
    row = conn.execute("SELECT sent_guids FROM progress WHERE account_id = ?",
                       (account_id,)).fetchone()
    conn.close()
    if row and row["sent_guids"]:
        return set(row["sent_guids"].split(","))
    return set()


def save_sent_guids(account_id: int, guids: set):
    conn = _conn()
    conn.execute(
        """
        INSERT INTO progress (account_id, sent_guids, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            sent_guids=excluded.sent_guids, updated_at=excluded.updated_at
        """,
        (account_id, ",".join(guids), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def clear_progress(account_id: int):
    conn = _conn()
    conn.execute("DELETE FROM progress WHERE account_id = ?", (account_id,))
    conn.commit()
    conn.close()


# ---------- accounts ----------

def add_account(phone: str, name: str, user_id: str, session: str) -> int:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO accounts (phone, name, user_id, session, added_at, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        ON CONFLICT(phone) DO UPDATE SET
            name=excluded.name,
            user_id=excluded.user_id,
            session=excluded.session,
            status='active'
        """,
        (phone, name, user_id, session, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    row = c.execute("SELECT id FROM accounts WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return row["id"]


def list_accounts() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_account(account_id: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_account(account_id: int):
    conn = _conn()
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()


def set_status(account_id: int, status: str):
    conn = _conn()
    conn.execute("UPDATE accounts SET status = ? WHERE id = ?", (status, account_id))
    conn.commit()
    conn.close()


# ---------- shared content ----------

def set_content(content_type: str, content_text, media_path):
    conn = _conn()
    conn.execute(
        "UPDATE settings SET content_type = ?, content_text = ?, media_path = ? WHERE id = 1",
        (content_type, content_text, media_path),
    )
    conn.commit()
    conn.close()


def get_content() -> dict:
    conn = _conn()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {"content_type": None, "content_text": None, "media_path": None}



# --------------------------------------------------------------------------- #
# Proxy management
# --------------------------------------------------------------------------- #
def add_proxy(host, ssh_port, ssh_user, ssh_pass, proxy_port, proxy_user, proxy_pass) -> int:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO proxies (host, ssh_port, ssh_user, ssh_pass, proxy_port,
                             proxy_user, proxy_pass, status, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', ?)
        """,
        (host, ssh_port, ssh_user, ssh_pass, proxy_port, proxy_user, proxy_pass,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    pid = c.lastrowid
    conn.close()
    return pid


def list_proxies() -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM proxies ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_proxy(proxy_id: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_proxy(proxy_id: int):
    conn = _conn()
    conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
    conn.commit()
    conn.close()


def update_proxy_health(proxy_id: int, status: str, ping_ms: int, upload_ok: bool):
    conn = _conn()
    conn.execute(
        "UPDATE proxies SET status = ?, ping_ms = ?, upload_ok = ?, checked_at = ? WHERE id = ?",
        (status, ping_ms, 1 if upload_ok else 0,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy_id),
    )
    conn.commit()
    conn.close()


def healthy_proxies() -> list:
    """Proxies that passed the upload test, ordered by ping (fastest first)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM proxies WHERE upload_ok = 1 AND status != 'red' ORDER BY ping_ms"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
