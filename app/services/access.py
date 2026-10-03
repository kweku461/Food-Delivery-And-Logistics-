"""Row-level visibility: which orders (and therefore payments, refunds, deliveries) a user may see."""
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.deps import staff_branch_ids
from app.enums import Role
from app.errors import AuthorizationError
from app.models import Delivery, Driver, Order, Restaurant, User


def order_scope(db: Session, user: User):
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.CUSTOMER:
        return Order.customer_id == user.id
    if user.role == Role.RESTAURANT_OWNER:
        return Order.restaurant_id.in_(select(Restaurant.id).where(Restaurant.owner_id == user.id))
    if user.role == Role.STAFF:
        return Order.branch_id.in_(staff_branch_ids(db, user) or [-1])
    if user.role == Role.DRIVER:
        return Order.id.in_(
            select(Delivery.order_id).join(Driver, Delivery.driver_id == Driver.id)
            .where(Driver.user_id == user.id)
        )
    return Order.id == -1


def assert_order_access(db: Session, user: User, order: Order) -> None:
    ok = db.scalar(select(Order.id).where(Order.id == order.id, order_scope(db, user)))
    if ok is None:
        raise AuthorizationError("You do not have access to this order")
