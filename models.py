from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional


class SMSPayload(BaseModel):
    """Incoming webhook payload from SMS Forwarder Android app."""
    # Optional because an outbound-message rule may resolve only the recipient
    # field; main.py decides which field identifies the customer.
    sender: Optional[str] = Field(default=None, alias="from")
    message: str
    contact: Optional[str] = None
    sentStamp: Optional[str] = None
    receivedStamp: Optional[str] = None
    deviceName: Optional[str] = None
    sim: Optional[str] = None

    # Counterparty on a forwarded *sent* message. The app names this field
    # differently depending on rule type and version, so accept every spelling
    # we've seen and let main.py take the first one that actually resolved.
    to: Optional[str] = None
    recipient: Optional[str] = None
    destination: Optional[str] = None
    address: Optional[str] = None

    # Hardcoded in the outbound rule's URL as ?direction=sent -- a literal
    # string can't fail to resolve the way a {{token}} can, which makes it the
    # most reliable direction signal available.
    direction: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def _drop_unresolved_tokens(cls, value):
        """Treat a literal "{{to}}" as an absent field.

        When a rule doesn't support a template token, the app forwards the
        token's own text instead of omitting the field. Letting that through
        would key a thread on the string "{{to}}" or stamp a message with a
        bogus timestamp, so unresolved tokens are dropped to None.
        """
        if isinstance(value, str) and "{{" in value and "}}" in value:
            return None
        return value


class LeadExtraction(BaseModel):
    """Structured lead data returned by Claude extraction."""
    lead_type: str
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    property_address: Optional[str] = None
    is_rental_or_sale: Optional[str] = None
    scope_of_work: Optional[str] = None
    availability: Optional[str] = None
    realtor_name: Optional[str] = None
    realtor_phone: Optional[str] = None
    realtor_email: Optional[str] = None
    lockbox_code: Optional[str] = None
    urgency: Optional[str] = None
    additional_notes: Optional[str] = None
    confidence: Optional[str] = None
