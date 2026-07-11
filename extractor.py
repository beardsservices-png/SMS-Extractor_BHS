import json
import hashlib
import logging

import anthropic
import httpx

from config import ANTHROPIC_API_KEY

log = logging.getLogger("bhs-sms")

# Railway's container network has flaky/unreachable IPv6 egress, which causes
# httpx to try an IPv6 route first, fail, and only fall back to IPv4 after a
# delay (surfacing as repeated "Connection error" from the Anthropic SDK).
# local_address="0.0.0.0" tells httpcore to connect using an AF_INET (IPv4)
# address outright, skipping the failing IPv6 attempt entirely.
_HTTP_CLIENT = httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    timeout=httpx.Timeout(30.0, connect=10.0),
)
_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


def log_client_config():
    """Log the active Anthropic HTTP client config so IPv4/IPv6 issues are visible at startup."""
    log.info(
        "[anthropic] client ready: transport=AsyncHTTPTransport local_address=0.0.0.0 "
        "(IPv4 egress forced) timeout=30s connect_timeout=10s"
    )

SYSTEM_PROMPT = """You are a lead extraction assistant for Beard's Home Services (BHS), a solo handyman and general contracting business owned by Brian Beard in Mountain Home, Arkansas (Baxter County area). Brian does residential and light commercial work — carpentry, decks, fencing, roofing, concrete, remodeling, painting, and general repairs.

Your job is to analyze an SMS conversation thread and extract structured lead information.

LEAD TYPES:
- new_customer_inquiry: First contact, no prior relationship
- realtor_referral: Contact from or on behalf of a real estate agent
- pre_sale_prep: Property being listed, wants work done before sale
- existing_customer: Returning client who mentions prior work with Brian
- vendor_or_other: Not a customer lead — vendor, wrong number, spam, personal text, solicitation

INSTRUCTIONS:
1. Return ONLY valid JSON — no prose, no markdown, no code fences, no preamble
2. Extract only what is clearly stated in the conversation — do not guess or infer
3. Use null for any field not explicitly mentioned
4. If the conversation is not a customer lead, return exactly: {"lead_type": "vendor_or_other"}

For customer leads, return this exact schema:
{
  "lead_type": "<type>",
  "customer_name": "<name or null>",
  "customer_phone": "<phone or null>",
  "property_address": "<full address or null>",
  "is_rental_or_sale": "<rental|sale|owner-occupied|null>",
  "scope_of_work": "<clear description of work needed or null>",
  "availability": "<when they can be reached or when they want work done or null>",
  "realtor_name": "<name or null>",
  "realtor_phone": "<phone or null>",
  "realtor_email": "<email or null>",
  "lockbox_code": "<code or null>",
  "urgency": "<low|moderate|high|urgent>",
  "additional_notes": "<any other relevant info or null>",
  "confidence": "<low|medium|high>"
}

URGENCY GUIDE:
- urgent: emergency, active damage, same-day need, "ASAP", "right now", flooding, etc.
- high: this week, "need it done soon", time-sensitive, before an event or deadline
- moderate: has a general timeline but flexible, "before winter", "next month", "before listing"
- low: no timeline stated, "whenever you have time", exploratory inquiry

CONFIDENCE GUIDE:
- high: clear name, address, and scope of work all present
- medium: some key fields missing but enough context to follow up
- low: very vague inquiry, minimal information to work with

Always include realtor fields when lead_type is realtor_referral or pre_sale_prep.
Always include lockbox_code if a code is mentioned — even if it appears as a short number in context.
"""

MEANINGFUL_FIELDS = {
    "customer_name",
    "property_address",
    "scope_of_work",
    "availability",
    "lockbox_code",
    "urgency",
    "realtor_name",
    "realtor_phone",
}


def format_thread(thread: list) -> str:
    lines = []
    for msg in thread:
        contact = f" ({msg['contact']})" if msg.get("contact") else ""
        lines.append(f"[SMS from {msg['role']}{contact}]: {msg['text']}")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    """Strip a ```json ... ``` or ``` ... ``` wrapper if the model added one despite instructions not to."""
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


async def extract_lead(thread: list) -> dict:
    thread_text = format_thread(thread)
    response = await _client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Extract lead information from this SMS conversation:\n\n{thread_text}",
            }
        ],
    )
    raw = _strip_code_fence(response.content[0].text.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # TEMPORARY: log what actually came back so we can see why parsing failed —
        # remove once the cause of intermittent non-JSON responses is confirmed.
        log.error(
            f"[extract] non-JSON response: stop_reason={response.stop_reason} "
            f"content_blocks={len(response.content)} raw={raw!r}"
        )
        raise


def has_new_information(previous: dict | None, current: dict) -> bool:
    """Return True if the current extraction contains meaningful new fields vs previous."""
    if previous is None:
        return any(current.get(f) is not None for f in MEANINGFUL_FIELDS)

    for field in MEANINGFUL_FIELDS:
        prev_val = previous.get(field)
        curr_val = current.get(field)
        if prev_val is None and curr_val is not None:
            return True
        if prev_val and curr_val and str(prev_val).strip() != str(curr_val).strip():
            return True

    return False


def hash_phone(phone: str) -> str:
    """Return a short non-reversible hash of a phone number for safe logging."""
    return hashlib.sha256(phone.encode()).hexdigest()[:12]
