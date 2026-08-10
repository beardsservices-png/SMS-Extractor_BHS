import re

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str | None) -> str:
    """Reduce a phone number to the canonical key used to identify a thread.

    SMS Forwarder is not consistent about formatting between its inbound and
    outbound rules -- the same person can arrive as "+18705551234" on a
    received text and "8705551234" or "(870) 555-1234" on Brian's reply to it.
    Both halves of a conversation have to land in one thread, so every number
    is reduced to its 10-digit national form before it is used as a key.

    Non-numeric senders (email-to-SMS gateways, alphanumeric shortcodes) have
    no digits to reduce, so they fall back to their own lowercased text.
    """
    if not raw:
        return ""
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif len(digits) > 11:
        # Longer strings carry a country code we don't serve; the last 10
        # digits still identify the line uniquely within this market.
        digits = digits[-10:]
    return digits or raw.strip().lower()


def same_phone(a: str | None, b: str | None) -> bool:
    """True when two differently-formatted numbers refer to the same line."""
    a_key = normalize_phone(a)
    return bool(a_key) and a_key == normalize_phone(b)
