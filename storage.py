import sqlite3
import json
import logging
from datetime import datetime, timezone

from config import DATABASE_PATH
from phones import normalize_phone

log = logging.getLogger("bhs-sms")


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
    _migrate_phone_keys(conn)
    conn.close()


def _migrate_phone_keys(conn: sqlite3.Connection):
    """Re-key existing rows to normalized phone numbers, merging duplicates.

    Rows written before normalization are keyed on whatever format the phone
    happened to send ("+18705551234"), which no longer matches the key a new
    message computes. Two rows can also collapse onto the same key, so their
    threads are merged in timestamp order rather than one being dropped.
    Idempotent: after the first run nothing needs rewriting and this is a
    single cheap SELECT.
    """
    rows = [dict(r) for r in conn.execute("SELECT * FROM sms_leads").fetchall()]
    if not any(r["phone"] != normalize_phone(r["phone"]) for r in rows):
        return

    merged: dict[str, dict] = {}
    for row in rows:
        key = normalize_phone(row["phone"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = {**row, "phone": key}
            continue

        # Newest row wins for the fields that represent "current state"; the
        # threads themselves are concatenated and re-sorted so no text is lost.
        newer, older = (
            (row, existing)
            if (row["last_message"] or "") >= (existing["last_message"] or "")
            else (existing, row)
        )
        thread = json.loads(existing["thread_json"] or "[]") + json.loads(row["thread_json"] or "[]")
        thread.sort(key=lambda m: m.get("ts") or 0)
        merged[key] = {
            **newer,
            "phone": key,
            "thread_json": json.dumps(thread),
            "first_contact": min(
                filter(None, [existing["first_contact"], row["first_contact"]]),
                default=newer["first_contact"],
            ),
            "last_message": newer["last_message"],
            "last_extraction_json": newer["last_extraction_json"] or older["last_extraction_json"],
            "lockbox_code": newer["lockbox_code"] or older["lockbox_code"],
            "ntfy_sent_count": (existing["ntfy_sent_count"] or 0) + (row["ntfy_sent_count"] or 0),
            "status": "active" if "active" in (existing["status"], row["status"]) else newer["status"],
        }

    conn.execute("DELETE FROM sms_leads")
    conn.executemany(
        """INSERT INTO sms_leads
               (phone, first_contact, last_message, thread_json,
                last_extraction_json, lockbox_code, ntfy_sent_count, status)
           VALUES (:phone, :first_contact, :last_message, :thread_json,
                   :last_extraction_json, :lockbox_code, :ntfy_sent_count, :status)""",
        list(merged.values()),
    )
    conn.commit()
    log.info(f"[migrate] normalized phone keys: {len(rows)} rows -> {len(merged)} threads")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_thread(phone: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM sms_leads WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_message(
    phone: str,
    message: str,
    sent_ts: int | None,
    contact: str | None = None,
    role: str = "customer",
):
    now = _now()
    new_msg = {"role": role, "text": message, "ts": sent_ts or 0}
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
