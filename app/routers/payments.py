from datetime import datetime, timezone
from decimal import Decimal

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_manage_restaurant, get_current_user, require_roles
from app.enums import OrderStatus, PaymentMethod, PaymentStatus, RefundStatus, Role
from app.errors import (
    AuthorizationError, BadRequestError, ConflictError, NotFoundError, PaymentFailedError,
)
from app.models import Order, Payment, Refund, User
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.schemas.payment import PaymentCreate, PaymentRead, RefundCreate, RefundDecision, RefundRead
from app.services import gateway
from app.services.access import assert_order_access, order_scope
from app.services.order_service import money
from app.utils import get_or_404

router = APIRouter(tags=["Payments & Refunds"])
payment_sort = sort_params(["created_at", "amount", "status", "id", "paid_at"])
refund_sort = sort_params(["created_at", "amount", "status", "id"])


def _now():
    return datetime.now(timezone.utc)


# ═════════════════════════ payments ═════════════════════════
@router.post("/payments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED,
             summary="Pay for an order (simulated gateway)")
def create_payment(data: PaymentCreate, user: User = Depends(require_roles(Role.CUSTOMER, Role.ADMIN)),
                   db: Session = Depends(get_db)):
    order = get_or_404(db, Order, data.order_id, "Order")
    assert_order_access(db, user, order)
    payment = order.payment
    if payment is None:
        raise NotFoundError("Payment record not found for this order")
    if order.status == OrderStatus.CANCELLED:
        raise ConflictError("Cannot pay for a cancelled order")
    if payment.status != PaymentStatus.PENDING and payment.status != PaymentStatus.FAILED:
        raise ConflictError(f"This order's payment is already {payment.status.value}")

    method = data.method
    result = gateway.charge(method, payment.amount, data.provider_reference)
    payment.method = method
    if method == PaymentMethod.CASH:
        payment.status, payment.failure_reason = PaymentStatus.PENDING, None  # collected on delivery
    elif result.success:
        payment.status, payment.paid_at = PaymentStatus.PAID, _now()
        payment.provider_reference, payment.failure_reason = result.reference, None
    else:
        payment.status, payment.failure_reason = PaymentStatus.FAILED, result.reason
    db.commit()  # the FAILED state must be persisted before we raise

    if payment.status == PaymentStatus.FAILED:
        raise PaymentFailedError(result.reason or "Payment failed",
                                 {"payment_id": payment.id, "order_id": order.id})
    return payment


@router.get("/payments", response_model=Page[PaymentRead])
def list_payments(order_id: int | None = None,
                  status_: PaymentStatus | None = Query(None, alias="status"),
                  method: PaymentMethod | None = None,
                  page: PageParams = Depends(page_params), sort: SortParams = Depends(payment_sort),
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Payment).join(Order, Payment.order_id == Order.id).where(order_scope(db, user))
    if order_id is not None:
        stmt = stmt.where(Payment.order_id == order_id)
    if status_ is not None:
        stmt = stmt.where(Payment.status == status_)
    if method is not None:
        stmt = stmt.where(Payment.method == method)
    return paginate(db, stmt, Payment, page, sort)


@router.get("/payments/{payment_id}", response_model=PaymentRead)
def get_payment(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = get_or_404(db, Payment, payment_id, "Payment")
    assert_order_access(db, user, payment.order)
    return payment


# ═════════════════════════ refunds ═════════════════════════
def _committed_refund_total(db: Session, payment_id: int, statuses) -> Decimal:
    total = db.scalar(select(func.coalesce(func.sum(Refund.amount), 0))
                      .where(Refund.payment_id == payment_id, Refund.status.in_(statuses)))
    return money(total or 0)


@router.post("/refunds", response_model=RefundRead, status_code=status.HTTP_201_CREATED,
             summary="Request a refund on a paid order")
def create_refund(data: RefundCreate, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.CUSTOMER, Role.RESTAURANT_OWNER, Role.ADMIN))):
    payment = get_or_404(db, Payment, data.payment_id, "Payment")
    assert_order_access(db, user, payment.order)
    if payment.status not in (PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED):
        raise ConflictError(f"Only PAID payments can be refunded (this one is {payment.status.value})")
    in_flight = _committed_refund_total(db, payment.id, (RefundStatus.PENDING, RefundStatus.PROCESSED))
    remaining = money(payment.amount - in_flight)
    if data.amount > remaining:
        raise BadRequestError("Refund amount exceeds the refundable balance",
                              {"refundable_balance": str(remaining)})
    refund = Refund(payment_id=payment.id, amount=data.amount, reason=data.reason,
                    requested_by_id=user.id)
    db.add(refund)
    db.commit()
    return refund


@router.get("/refunds", response_model=Page[RefundRead])
def list_refunds(payment_id: int | None = None,
                 status_: RefundStatus | None = Query(None, alias="status"),
                 page: PageParams = Depends(page_params), sort: SortParams = Depends(refund_sort),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = (select(Refund).join(Payment, Refund.payment_id == Payment.id)
            .join(Order, Payment.order_id == Order.id).where(order_scope(db, user)))
    if payment_id is not None:
        stmt = stmt.where(Refund.payment_id == payment_id)
    if status_ is not None:
        stmt = stmt.where(Refund.status == status_)
    return paginate(db, stmt, Refund, page, sort)


@router.get("/refunds/{refund_id}", response_model=RefundRead)
def get_refund(refund_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    refund = get_or_404(db, Refund, refund_id, "Refund")
    assert_order_access(db, user, refund.payment.order)
    return refund


@router.patch("/refunds/{refund_id}/status", response_model=RefundRead,
              summary="Approve (PROCESSED) or REJECT a pending refund — ADMIN / restaurant owner")
def decide_refund(refund_id: int, data: RefundDecision, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    refund = get_or_404(db, Refund, refund_id, "Refund")
    payment = refund.payment
    if not can_manage_restaurant(db, user, payment.order.restaurant_id):
        raise AuthorizationError("This refund does not belong to your restaurant")
    if data.status == RefundStatus.PENDING:
        raise BadRequestError("Decision must be PROCESSED or REJECTED")
    if refund.status != RefundStatus.PENDING:
        raise ConflictError(f"Refund is already {refund.status.value}")

    refund.status, refund.processed_by_id, refund.processed_at = data.status, user.id, _now()
    if data.status == RefundStatus.PROCESSED:
        processed = _committed_refund_total(db, payment.id, (RefundStatus.PROCESSED,)) + refund.amount
        payment.status = (PaymentStatus.REFUNDED if processed >= payment.amount
                          else PaymentStatus.PARTIALLY_REFUNDED)
    db.commit()
    return refund
