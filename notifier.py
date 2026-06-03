import httpx

from config import NTFY_URL, NTFY_TOPIC, NTFY_TOKEN

# Maps lead_type slug to a human-readable label for the notification title
LEAD_TYPE_LABELS = {
    "new_customer_inquiry": "New Inquiry",
    "realtor_referral": "Realtor Referral",
    "pre_sale_prep": "Pre-Sale Prep",
    "existing_customer": "Existing Customer",
}

# Mirrors the priority mapping in bhs-memory-server/server.js (high for urgent, default otherwise)
URGENCY_TO_PRIORITY = {
    "urgent": "max",
    "high": "high",
    "moderate": "default",
    "low": "low",
}


def _build_body(extraction: dict, has_lockbox: bool) -> str:
    lines = []

    if extraction.get("customer_phone"):
        lines.append(f"☎️ Phone: {extraction['customer_phone']}")

    if extraction.get("property_address"):
        lines.append(f"\U0001f4cd Property: {extraction['property_address']}")

    if extraction.get("is_rental_or_sale"):
        lines.append(f"\U0001f3e0 Type: {extraction['is_rental_or_sale'].title()}")

    if extraction.get("scope_of_work"):
        lines.append(f"\U0001f527 Scope: {extraction['scope_of_work']}")

    if extraction.get("availability"):
        lines.append(f"\U0001f4c5 Available: {extraction['availability']}")

    if extraction.get("urgency"):
        lines.append(f"⚡ Urgency: {extraction['urgency'].title()}")

    if extraction.get("realtor_name"):
        lines.append(f"\U0001f91d Realtor: {extraction['realtor_name']}")
    if extraction.get("realtor_phone"):
        lines.append(f"   Realtor phone: {extraction['realtor_phone']}")
    if extraction.get("realtor_email"):
        lines.append(f"   Realtor email: {extraction['realtor_email']}")

    if extraction.get("additional_notes"):
        lines.append(f"\U0001f464 Notes: {extraction['additional_notes']}")

    if has_lockbox:
        lines.append("\U0001f511 Lockbox: [PRESENT — check app]")

    if extraction.get("confidence"):
        lines.append(f"\U0001f4ca Confidence: {extraction['confidence'].title()}")

    return "\n".join(lines)


async def send_lead_notification(extraction: dict, is_final: bool = False):
    lead_type = extraction.get("lead_type", "new_customer_inquiry")
    label = LEAD_TYPE_LABELS.get(lead_type, lead_type.replace("_", " ").title())
    customer_name = extraction.get("customer_name") or "Unknown"

    prefix = "\U0001f4cb Final Lead —" if is_final else "\U0001f4cb New Lead —"
    title = f"{prefix} {label} — {customer_name}"

    urgency = (extraction.get("urgency") or "moderate").lower()
    priority = URGENCY_TO_PRIORITY.get(urgency, "default")

    # lockbox_code present in the extraction dict means we show the [PRESENT] line —
    # we never put the raw code in the notification body (lock screen risk)
    has_lockbox = bool(extraction.get("lockbox_code"))
    body = _build_body(extraction, has_lockbox)

    headers = {
        "Title": title,
        "Priority": priority,
        "Content-Type": "text/plain",
        "Tags": f"sms,lead,{lead_type}",
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{NTFY_URL}/{NTFY_TOPIC}",
            content=body.encode("utf-8"),
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
