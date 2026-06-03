from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class SMSPayload(BaseModel):
    """Incoming webhook payload from SMS Forwarder Android app."""
    sender: str = Field(alias="from")
    message: str
    contact: Optional[str] = None
    sentStamp: Optional[int] = None
    receivedStamp: Optional[int] = None
    deviceName: Optional[str] = None
    sim: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


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
