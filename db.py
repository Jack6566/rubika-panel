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
