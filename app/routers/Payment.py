from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_roles
from app.enums import PaymentStatus, RefundStatus, Role
from app.errors import AuthorizationError, ConflictError
from app.models import Order, Payment, Refund, User
from app.schemas.payment import (
    PaymentCreate,
    PaymentRead,
    RefundCreate,
    RefundDecision,
    RefundRead,
)
from app.utils import get_or_404


router = APIRouter(tags=["Payments"])


@router.post(
    "/payments",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment for an order",
)
def create_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = get_or_404(db, Order, data.order_id, "Order")

    # Only the customer who owns the order can make its payment
    if order.customer_id != user.id:
        raise AuthorizationError("You can only pay for your own order")

    # An order can have only one payment
    if order.payment is not None:
        raise ConflictError("This order already has a payment")

    # Payment amount comes from the order total
    payment = Payment(
        order_id=order.id,
        method=data.method,
        amount=order.total,
        status=PaymentStatus.PENDING,
        provider_reference=data.provider_reference,
    )

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return payment


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentRead,
    summary="Get a payment",
)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment = get_or_404(db, Payment, payment_id, "Payment")

    if payment.order.customer_id != user.id and user.role != Role.ADMIN:
        raise AuthorizationError("You are not allowed to view this payment")

    return payment


@router.post(
    "/payments/{payment_id}/refunds",
    response_model=RefundRead,
    status_code=status.HTTP_201_CREATED,
    summary="Request a refund",
)
def request_refund(
    payment_id: int,
    data: RefundCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment = get_or_404(db, Payment, payment_id, "Payment")

    if payment.order.customer_id != user.id:
        raise AuthorizationError(
            "You can only request a refund for your own order"
        )

    if data.payment_id != payment.id:
        raise ConflictError("Payment ID does not match the URL")

    if payment.status not in (
        PaymentStatus.PAID,
        PaymentStatus.PARTIALLY_REFUNDED,
    ):
        raise ConflictError("Only paid payments can be refunded")

    processed_amount = sum(
        (
            refund.amount
            for refund in payment.refunds
            if refund.status == RefundStatus.PROCESSED
        ),
        Decimal("0.00"),
    )

    pending_amount = sum(
        (
            refund.amount
            for refund in payment.refunds
            if refund.status == RefundStatus.PENDING
        ),
        Decimal("0.00"),
    )

    remaining_amount = (
        payment.amount - processed_amount - pending_amount
    )

    if data.amount > remaining_amount:
        raise ConflictError(
            "Refund amount cannot exceed the remaining refundable amount"
        )

    refund = Refund(
        payment_id=payment.id,
        amount=data.amount,
        reason=data.reason,
        status=RefundStatus.PENDING,
        requested_by_id=user.id,
    )

    db.add(refund)
    db.commit()
    db.refresh(refund)

    return refund


@router.patch(
    "/refunds/{refund_id}",
    response_model=RefundRead,
    summary="Approve or reject a refund",
)
def decide_refund(
    refund_id: int,
    data: RefundDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(Role.ADMIN)),
):
    refund = get_or_404(db, Refund, refund_id, "Refund")

    if refund.status != RefundStatus.PENDING:
        raise ConflictError("This refund has already been processed")

    refund.status = data.status
    refund.processed_by_id = admin.id
    refund.processed_at = datetime.utcnow()

    payment = refund.payment

    if data.status == RefundStatus.PROCESSED:
        processed_amount = sum(
            (
                r.amount
                for r in payment.refunds
                if r.status == RefundStatus.PROCESSED
            ),
            Decimal("0.00"),
        )

        # Include the refund currently being processed
        processed_amount += refund.amount

        if processed_amount >= payment.amount:
            payment.status = PaymentStatus.REFUNDED
        else:
            payment.status = PaymentStatus.PARTIALLY_REFUNDED

    db.commit()
    db.refresh(refund)

    return refund 