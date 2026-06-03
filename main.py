import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import config
from extractor import extract_lead, has_new_information, hash_phone
from models import SMSPayload
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
    task = asyncio.create_task(_ttl_checker())
    yield
    task.cancel()


app = FastAPI(title="BHS SMS Lead Extractor", lifespan=lifespan)


def _check_token(token: str):
    if config.WEBHOOK_SECRET and token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")


# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── SMS WEBHOOK ──────────────────────────────────────────────────────────────

@app.post("/sms")
async def receive_sms(
    payload: SMSPayload,
    token: str = Query(default=""),
):
    _check_token(token)

    phone = payload.sender
    phone_hash = hash_phone(phone)
    log.info(f"[sms] received from={phone_hash} ts={payload.sentStamp}")

    upsert_message(phone, payload.message, payload.sentStamp, payload.contact)

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
