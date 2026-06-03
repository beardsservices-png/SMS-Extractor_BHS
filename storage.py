import sqlite3
import json
from datetime import datetime, timezone

from config import DATABASE_PATH


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sms_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            first_contact TIMESTAMP,
            last_message TIMESTAMP,
            thread_json TEXT,
            last_extraction_json TEXT,
            lockbox_code TEXT,
            ntfy_sent_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_thread(phone: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM sms_leads WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_message(phone: str, message: str, sent_ts: int | None, contact: str | None = None):
    now = _now()
    new_msg = {"role": "customer", "text": message, "ts": sent_ts or 0}
    if contact:
        new_msg["contact"] = contact
    conn = _conn()
    row = conn.execute("SELECT thread_json FROM sms_leads WHERE phone = ?", (phone,)).fetchone()
    if row:
        thread = json.loads(row["thread_json"] or "[]")
        thread.append(new_msg)
        conn.execute(
            "UPDATE sms_leads SET thread_json = ?, last_message = ?, status = 'active' WHERE phone = ?",
            (json.dumps(thread), now, phone),
        )
    else:
        thread = [new_msg]
        conn.execute(
            """INSERT INTO sms_leads (phone, first_contact, last_message, thread_json, status)
               VALUES (?, ?, ?, ?, 'active')""",
            (phone, now, now, json.dumps(thread)),
        )
    conn.commit()
    conn.close()


def save_extraction(phone: str, extraction: dict, lockbox_code: str | None):
    conn = _conn()
    if lockbox_code:
        conn.execute(
            "UPDATE sms_leads SET last_extraction_json = ?, lockbox_code = ? WHERE phone = ?",
            (json.dumps(extraction), lockbox_code, phone),
        )
    else:
        conn.execute(
            "UPDATE sms_leads SET last_extraction_json = ? WHERE phone = ?",
            (json.dumps(extraction), phone),
        )
    conn.commit()
    conn.close()


def increment_ntfy_count(phone: str):
    conn = _conn()
    conn.execute(
        "UPDATE sms_leads SET ntfy_sent_count = ntfy_sent_count + 1 WHERE phone = ?",
        (phone,),
    )
    conn.commit()
    conn.close()


def mark_complete(phone: str):
    conn = _conn()
    conn.execute("UPDATE sms_leads SET status = 'complete' WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()


def get_active_threads() -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM sms_leads WHERE status = 'active'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lockbox(phone: str) -> str | None:
    conn = _conn()
    row = conn.execute(
        "SELECT lockbox_code FROM sms_leads WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    return row["lockbox_code"] if row else None
