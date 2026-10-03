"""Order + delivery state machines and the rules that keep them in sync.

Order:     PENDING -> CONFIRMED -> PREPARING -> READY_FOR_PICKUP -> OUT_FOR_DELIVERY -> DELIVERED
           (PENDING/CONFIRMED/PREPARING/READY_FOR_PICKUP can also go to CANCELLED)
Delivery:  UNASSIGNED -> ASSIGNED -> PICKED_UP -> (IN_TRANSIT) -> DELIVERED
           (PICKED_UP/IN_TRANSIT -> FAILED -> ASSIGNED again for a re-dispatch)

The two machines are linked: a driver picking up moves the order to OUT_FOR_DELIVERY,
and a completed delivery moves the order to DELIVERED. Every change writes a history row.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.deps import can_manage_restaurant, staff_branch_ids
from app.enums import (
    DeliveryStatus as DS, OrderStatus as OS, PaymentMethod, PaymentStatus, Role,
)
from app.errors import (
    AuthorizationError, BadRequestError, ConflictError, InvalidTransitionError,
)
from app.models import (
    Delivery, DeliveryStatusHistory, Driver, Order, OrderStatusHistory, User,
)

ORDER_TRANSITIONS: dict[OS, set[OS]] = {
    OS.PENDING: {OS.CONFIRMED, OS.CANCELLED},
    OS.CONFIRMED: {OS.PREPARING, OS.CANCELLED},
    OS.PREPARING: {OS.READY_FOR_PICKUP, OS.CANCELLED},
    OS.READY_FOR_PICKUP: {OS.OUT_FOR_DELIVERY, OS.CANCELLED},
    OS.OUT_FOR_DELIVERY: {OS.DELIVERED},
    OS.DELIVERED: set(),
    OS.CANCELLED: set(),
}

DELIVERY_TRANSITIONS: dict[DS, set[DS]] = {
    DS.UNASSIGNED: {DS.ASSIGNED, DS.CANCELLED},
    DS.ASSIGNED: {DS.PICKED_UP, DS.CANCELLED},
    DS.PICKED_UP: {DS.IN_TRANSIT, DS.DELIVERED, DS.FAILED},
    DS.IN_TRANSIT: {DS.DELIVERED, DS.FAILED},
    DS.FAILED: {DS.ASSIGNED},
    DS.DELIVERED: set(),
    DS.CANCELLED: set(),
}

RESTAURANT_TARGETS = {OS.CONFIRMED, OS.PREPARING, OS.READY_FOR_PICKUP, OS.CANCELLED}
DRIVER_TARGETS = {OS.OUT_FOR_DELIVERY, OS.DELIVERED}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check(current, new, table, label: str) -> None:
    if new not in table[current]:
        raise InvalidTransitionError(
            f"Cannot move {label} from {current.value} to {new.value}",
            {"current": current.value, "requested": new.value,
             "allowed": sorted(s.value for s in table[current])},
        )


# ───────────────────────── low-level writers ─────────────────────────
def _set_order_status(db: Session, order: Order, new: OS, actor: User | None, note: str | None) -> None:
    _check(order.status, new, ORDER_TRANSITIONS, "order")
    db.add(OrderStatusHistory(
        order_id=order.id, from_status=order.status, to_status=new,
        changed_by_id=actor.id if actor else None, note=note,
    ))
    order.status = new


def _set_delivery_status(db: Session, delivery: Delivery, new: DS, actor: User | None, note: str | None) -> None:
    _check(delivery.status, new, DELIVERY_TRANSITIONS, "delivery")
    db.add(DeliveryStatusHistory(
        delivery_id=delivery.id, from_status=delivery.status, to_status=new,
        changed_by_id=actor.id if actor else None, note=note,
    ))
    delivery.status = new


def _release_driver(delivery: Delivery) -> None:
    if delivery.driver is not None:
        delivery.driver.is_available = True


# ───────────────────────── order transitions ─────────────────────────
def _authorize_order_change(db: Session, actor: User, order: Order, new: OS) -> None:
    role = actor.role
    if role == Role.ADMIN:
        return
    if role == Role.CUSTOMER:
        if order.customer_id != actor.id:
            raise AuthorizationError("This is not your order")
        if new != OS.CANCELLED:
            raise AuthorizationError("Customers may only cancel their own orders")
        if order.status != OS.PENDING:
            raise AuthorizationError("Customers can only cancel orders that are still PENDING")
        return
    if role == Role.RESTAURANT_OWNER:
        if not can_manage_restaurant(db, actor, order.restaurant_id):
            raise AuthorizationError("This order does not belong to your restaurant")
    elif role == Role.STAFF:
        if order.branch_id not in staff_branch_ids(db, actor):
            raise AuthorizationError("This order does not belong to your branch")
    elif role == Role.DRIVER:
        d = order.delivery
        if new not in DRIVER_TARGETS or d is None or d.driver is None or d.driver.user_id != actor.id:
            raise AuthorizationError("Drivers may only progress deliveries assigned to them")
        return
    if new not in RESTAURANT_TARGETS and role in (Role.RESTAURANT_OWNER, Role.STAFF):
        raise AuthorizationError(
            f"Role {role.value} cannot set an order to {new.value}",
            {"allowed": sorted(s.value for s in RESTAURANT_TARGETS)},
        )


def transition_order(db: Session, order: Order, new: OS, actor: User, note: str | None = None) -> Order:
    _authorize_order_change(db, actor, order, new)
    _check(order.status, new, ORDER_TRANSITIONS, "order")

    if new in DRIVER_TARGETS:
        delivery = order.delivery
        if delivery is None or delivery.driver_id is None:
            raise ConflictError("Assign a driver before dispatching this order")
        target = DS.PICKED_UP if new == OS.OUT_FOR_DELIVERY else DS.DELIVERED
        update_delivery_status(db, delivery, target, actor, note)
        return order

    if new == OS.CONFIRMED:
        p = order.payment
        if p is None or (p.status != PaymentStatus.PAID and p.method != PaymentMethod.CASH):
            raise ConflictError("Order cannot be confirmed until payment is completed")

    _set_order_status(db, order, new, actor, note)

    if new == OS.CANCELLED and order.delivery is not None:
        d = order.delivery
        _release_driver(d)
        if d.status not in (DS.CANCELLED, DS.DELIVERED):
            # FAILED has no CANCELLED edge in the driver flow, so record it directly
            if d.status == DS.FAILED:
                db.add(DeliveryStatusHistory(delivery_id=d.id, from_status=d.status,
                                             to_status=DS.CANCELLED, changed_by_id=actor.id,
                                             note="Order cancelled"))
                d.status = DS.CANCELLED
            else:
                _set_delivery_status(db, d, DS.CANCELLED, actor, "Order cancelled")
    db.commit()
    return order


# ───────────────────────── delivery transitions ─────────────────────────
def update_delivery_status(db: Session, delivery: Delivery, new: DS, actor: User, note: str | None = None) -> Delivery:
    is_admin = actor.role == Role.ADMIN
    is_assigned_driver = (
        actor.role == Role.DRIVER and delivery.driver is not None and delivery.driver.user_id == actor.id
    )
    if not (is_admin or is_assigned_driver):
        raise AuthorizationError("Only the assigned driver or an admin can update this delivery")
    if new == DS.ASSIGNED:
        raise BadRequestError("Use POST /deliveries/{id}/assign-driver to assign a driver")
    if new == DS.CANCELLED and not is_admin:
        raise AuthorizationError("Only an admin can cancel a delivery")
    _check(delivery.status, new, DELIVERY_TRANSITIONS, "delivery")

    order = delivery.order
    if new == DS.PICKED_UP:
        if order.status != OS.READY_FOR_PICKUP:
            raise ConflictError(
                f"Order is {order.status.value}; it must be READY_FOR_PICKUP before pickup"
            )
        _set_order_status(db, order, OS.OUT_FOR_DELIVERY, actor, note or "Picked up by driver")
        delivery.picked_up_at = _now()
    elif new == DS.DELIVERED:
        if order.status != OS.OUT_FOR_DELIVERY:
            raise ConflictError(f"Order is {order.status.value}; it is not out for delivery")
        _set_order_status(db, order, OS.DELIVERED, actor, note or "Delivered to customer")
        delivery.delivered_at = _now()
        p = order.payment
        if p is not None and p.method == PaymentMethod.CASH and p.status == PaymentStatus.PENDING:
            p.status, p.paid_at = PaymentStatus.PAID, _now()  # cash collected by driver
        _release_driver(delivery)
    elif new in (DS.FAILED, DS.CANCELLED):
        _release_driver(delivery)

    _set_delivery_status(db, delivery, new, actor, note)
    db.commit()
    return delivery


def assign_driver(db: Session, delivery: Delivery, driver: Driver, actor: User) -> Delivery:
    order = delivery.order
    if order.status in (OS.DELIVERED, OS.CANCELLED):
        raise ConflictError(f"Cannot assign a driver to a {order.status.value} order")
    if delivery.status not in (DS.UNASSIGNED, DS.ASSIGNED, DS.FAILED):
        raise ConflictError(f"Cannot assign a driver while delivery is {delivery.status.value}")
    if not driver.user.is_active:
        raise ConflictError("Driver account is deactivated")
    same_driver = delivery.driver_id == driver.id
    if not driver.is_available and not same_driver:
        raise ConflictError("Driver is not available")
    if delivery.driver is not None and not same_driver:
        _release_driver(delivery)

    delivery.driver = driver
    delivery.assigned_at = _now()
    driver.is_available = False
    db.add(DeliveryStatusHistory(
        delivery_id=delivery.id, from_status=delivery.status, to_status=DS.ASSIGNED,
        changed_by_id=actor.id, note=f"Assigned to driver #{driver.id}",
    ))
    delivery.status = DS.ASSIGNED
    db.commit()
    return delivery
