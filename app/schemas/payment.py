from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from app.enums import PaymentMethod, PaymentStatus, RefundStatus
from app.schemas.common import Timestamped


class PaymentCreate(BaseModel):         # amount is taken from order.total, never from the client
    order_id: int
    method: PaymentMethod
    provider_reference: str | None = Field(None, max_length=100)


class RefundCreate(BaseModel):
    payment_id: int
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    reason: str = Field(min_length=3, max_length=500)


class RefundDecision(BaseModel):        # admin/owner approves or rejects
    status: RefundStatus


class RefundRead(Timestamped):
    payment_id: int
    amount: Decimal
    reason: str
    status: RefundStatus
    requested_by_id: int
    processed_by_id: int | None
    processed_at: datetime | None


class PaymentRead(Timestamped):
    order_id: int
    method: PaymentMethod
    status: PaymentStatus
    amount: Decimal
    provider_reference: str | None
    failure_reason: str | None
    paid_at: datetime | None
    refunds: list[RefundRead] = []