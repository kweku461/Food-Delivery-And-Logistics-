"""Fake payment gateway so the API can be exercised without a real PSP.

`reference` is what the client sends as `provider_reference`:
  CARD -> a card token  (use "tok_fail" or "tok_declined" to simulate a decline)
  MOMO -> the wallet phone number (a number ending in 0000 simulates a failure)
  CASH -> not needed; the driver collects the money on delivery
"""
import uuid
from dataclasses import dataclass

from app.enums import PaymentMethod
from app.errors import BadRequestError


@dataclass
class ChargeResult:
    success: bool
    reference: str | None = None
    reason: str | None = None


def charge(method: PaymentMethod, amount, reference: str | None) -> ChargeResult:
    if method == PaymentMethod.CARD:
        if not reference:
            raise BadRequestError("provider_reference (card token) is required for CARD payments")
        if reference in ("tok_fail", "tok_declined"):
            return ChargeResult(False, reason="Card was declined by the issuer")
        return ChargeResult(True, reference=f"CARD-{uuid.uuid4().hex[:12].upper()}")
    if method == PaymentMethod.MOMO:
        if not reference:
            raise BadRequestError("provider_reference (phone number) is required for MOMO payments")
        if reference.endswith("0000"):
            return ChargeResult(False, reason="Mobile money prompt was rejected or timed out")
        return ChargeResult(True, reference=f"MOMO-{uuid.uuid4().hex[:12].upper()}")
    return ChargeResult(True, reference=None)
