import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import config
from extractor import (
    extract_lead,
    has_customer_message,
    has_new_information,
    hash_phone,
    log_client_config,
)
from models import SMSPayload
from phones import normalize_phone, same_phone
from notifier import send_lead_notification
from storage import (
    get_active_threads,
    get_lockbox,
    get_thread,
    increment_ntfy_count,
    init_db,
    mark_complete,
    save_extraction,
    upsert_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bhs-sms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log_client_config()
    task = asyncio.create_task(_ttl_checker())
    yield
    task.cancel()


app = FastAPI(title="BHS SMS Lead Extractor", lifespan=lifespan)


def _check_token(token: str):
    if config.WEBHOOK_SECRET and token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")


# ─── DIRECTION ────────────────────────────────────────────────────────────────

# Substrings the app (or a hardcoded ?direction= value) may use to describe
# which way a message went. Checked as substrings so "sent"/"outgoing"/"OUT"
# all land the same way.
_OUTBOUND_HINTS = ("sent", "out")
_INBOUND_HINTS = ("receiv", "incoming", "inbox", "in")


def _resolve_direction(payload: SMSPayload) -> str:
    """Decide whether a forwarded message was sent by Brian or received by him.

    Preference order matters: an explicit ?direction= in the rule's URL is a
    literal string and always resolves, whereas matching against OWNER_PHONE
    depends on that variable being set and on the app populating "from" with
    Brian's own number on sent messages.
    """
    declared = (payload.direction or "").strip().lower()
    if declared:
        if any(hint in declared for hint in _OUTBOUND_HINTS):
            return "outbound"
        if any(hint in declared for hint in _INBOUND_HINTS):
            return "inbound"

    if config.OWNER_PHONE and same_phone(payload.sender, config.OWNER_PHONE):
        return "outbound"

    return "inbound"


def _resolve_counterparty(payload: SMSPayload, direction: str) -> str | None:
    """Return the customer's number — the number a thread is keyed on.

    On a received message that's the sender. On one Brian sent it's the
    recipient, which the app may report under any of several field names, or
    (in apps that reuse "from" for the other party on sent messages) under
    "from" itself. Brian's own number is skipped wherever it shows up, since
    keying his thread on his own number would merge every conversation.
    """
    if direction == "inbound":
        return payload.sender

    candidates = (
        payload.to,
        payload.recipient,
        payload.destination,
        payload.address,
        payload.sender,
    )
    for candidate in candidates:
        if not candidate:
            continue
        if config.OWNER_PHONE and same_phone(candidate, config.OWNER_PHONE):
            continue
        return candidate
    return None


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── SMS WEBHOOK ──────────────────────────────────────────────────────────────

@app.post("/sms")
async def receive_sms(
    request: Request,
    token: str = Query(default=""),
):
    _check_token(token)

    # TEMPORARY: log the raw body to see the exact field names SMS Forwarder
    # sends, since sentStamp/receivedStamp are coming through as None. Remove
    # once the field mapping in models.SMSPayload is confirmed correct.
    raw_body = await request.body()
    log.info(f"[sms] raw body: {raw_body.decode('utf-8', errors='replace')}")

    # SMS Forwarder has been sent both as JSON and as x-www-form-urlencoded
    # while we were dialing in its config on the phone — accept either so a
    # body-type toggle on the app side doesn't turn into a silent 422.
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        data = dict(form)
    elif raw_body:
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            log.error(f"[sms] unparseable body (content-type={content_type!r})")
            data = {}
    else:
        data = {}

    # The app's JSON/form Body-field templating has proven unreliable (tokens
    # coming through unresolved regardless of syntax) — URL query-string
    # substitution tends to be a simpler, more reliably-supported mechanism in
    # these apps, so accept from/message/contact/receivedStamp/sentStamp there
    # too and let them take priority over anything (unresolved) in the body.
    query_data = {k: v for k, v in request.query_params.items() if k != "token"}
    if query_data:
        log.info(f"[sms] query params: {query_data}")
        data = {**data, **query_data}

    try:
        payload = SMSPayload.model_validate(data)
    except Exception as e:
        log.error(f"[sms] payload validation failed: {e}")
        return JSONResponse({"ok": True, "extracted": False})

    direction = _resolve_direction(payload)
    counterparty = _resolve_counterparty(payload, direction)
    if not counterparty:
        log.error(
            f"[sms] {direction} message with no usable counterparty number; "
            f"fields present: {sorted(data)}"
        )
        return JSONResponse({"ok": True, "extracted": False})

    phone = normalize_phone(counterparty)
    phone_hash = hash_phone(phone)
    role = "brian" if direction == "outbound" else "customer"
    log.info(f"[sms] {direction} with={phone_hash} ts={payload.sentStamp}")

    upsert_message(phone, payload.message, payload.sentStamp, payload.contact, role=role)

    if direction == "outbound":
        # Brian wrote this one, so there is nothing to tell him about it — a
        # card here would just be his own text read back to him, plus an API
        # call. It's stored so the next inbound message extracts against the
        # full two-sided conversation, where his questions give the customer's
        # short answers their meaning.
        log.info(f"[sms] outbound reply stored for {phone_hash}, extraction skipped")
        return JSONResponse({"ok": True, "extracted": False, "direction": "outbound"})

    thread_record = get_thread(phone)
    thread = json.loads(thread_record["thread_json"])
    prev_raw = thread_record.get("last_extraction_json")
    prev_extraction = json.loads(prev_raw) if prev_raw else None

    try:
        extraction = await extract_lead(thread)
    except Exception as e:
        log.error(f"[extract] error for {phone_hash}: {e}")
        return JSONResponse({"ok": True, "extracted": False})

    if extraction.get("lead_type") == "vendor_or_other":
        log.info(f"[extract] vendor_or_other suppressed for {phone_hash}")
        return JSONResponse({"ok": True, "extracted": False, "suppressed": True})

    lockbox = extraction.pop("lockbox_code", None)
    save_extraction(phone, extraction, lockbox)

    field_count = sum(1 for v in extraction.values() if v is not None)
    log.info(
        f"[extract] {phone_hash}: fields={field_count} lead_type={extraction.get('lead_type')}"
    )

    if has_new_information(prev_extraction, extraction):
        notify_payload = {**extraction, "lockbox_code": lockbox}
        try:
            await send_lead_notification(notify_payload)
            increment_ntfy_count(phone)
            log.info(f"[notify] sent for {phone_hash}")
        except Exception as e:
            log.error(f"[notify] error for {phone_hash}: {e}")

    return JSONResponse(
        {"ok": True, "extracted": True, "lead_type": extraction.get("lead_type")}
    )


# ─── MANUAL TRIGGER ───────────────────────────────────────────────────────────

@app.post("/sms/extract/{phone_number}")
async def manual_extract(
    phone_number: str,
    token: str = Query(default=""),
):
    _check_token(token)

    # Normalized so a bookmarked URL works whether it was saved with the
    # number as "+18705551234", "8705551234" or "(870) 555-1234".
    phone_number = normalize_phone(phone_number)

    thread_record = get_thread(phone_number)
    if not thread_record:
        raise HTTPException(status_code=404, detail="No thread found for this number")

    thread = json.loads(thread_record["thread_json"])
    phone_hash = hash_phone(phone_number)

    try:
        extraction = await extract_lead(thread)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    if extraction.get("lead_type") == "vendor_or_other":
        log.info(f"[manual] vendor_or_other suppressed for {phone_hash}")
        return JSONResponse({"ok": True, "suppressed": True})

    lockbox = extraction.pop("lockbox_code", None)
    save_extraction(phone_number, extraction, lockbox)

    notify_payload = {**extraction, "lockbox_code": lockbox}
    await send_lead_notification(notify_payload, is_final=True)
    increment_ntfy_count(phone_number)

    log.info(f"[manual] extraction sent for {phone_hash}")
    return JSONResponse({"ok": True, "lead_type": extraction.get("lead_type")})


# ─── LOCKBOX RETRIEVAL ────────────────────────────────────────────────────────

@app.get("/sms/lockbox/{phone_number}")
async def get_lockbox_code(
    phone_number: str,
    token: str = Query(default=""),
):
    _check_token(token)

    phone_number = normalize_phone(phone_number)
    code = get_lockbox(phone_number)
    if not code:
        raise HTTPException(status_code=404, detail="No lockbox code on file for this number")

    return JSONResponse({"phone": phone_number, "lockbox_code": code})


# ─── TTL BACKGROUND TASK ──────────────────────────────────────────────────────

async def _ttl_checker():
    """Every 15 minutes: expire threads silent for THREAD_TTL_HOURS, fire final card."""
    while True:
        await asyncio.sleep(15 * 60)
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=config.THREAD_TTL_HOURS)
            cutoff_str = cutoff.isoformat()
            threads = get_active_threads()

            for t in threads:
                last_msg = t.get("last_message")
                if not last_msg or last_msg >= cutoff_str:
                    continue

                phone = t["phone"]
                phone_hash = hash_phone(phone)
                log.info(f"[ttl] thread expired for {phone_hash}, running final extraction")

                try:
                    thread = json.loads(t["thread_json"])

                    # Brian texting a number first (a callback, a supplier)
                    # opens a thread with nothing in it but his own messages.
                    # There's no lead there to extract, so close it quietly.
                    if not has_customer_message(thread):
                        mark_complete(phone)
                        log.info(f"[ttl] closed outbound-only thread for {phone_hash}")
                        continue

                    extraction = await extract_lead(thread)

                    if extraction.get("lead_type") != "vendor_or_other":
                        lockbox = extraction.pop("lockbox_code", None)
                        save_extraction(phone, extraction, lockbox)
                        notify_payload = {**extraction, "lockbox_code": lockbox}
                        await send_lead_notification(notify_payload, is_final=True)
                        increment_ntfy_count(phone)

                    mark_complete(phone)
                    log.info(f"[ttl] completed thread for {phone_hash}")
                except Exception as e:
                    log.error(f"[ttl] error processing {phone_hash}: {e}")

        except Exception as e:
            log.error(f"[ttl] checker loop error: {e}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
