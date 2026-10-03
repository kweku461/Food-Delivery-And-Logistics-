from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_roles
from app.enums import Role
from app.errors import AuthorizationError
from app.models import Order, OrderStatusHistory, User
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.schemas.extra import OrderListFilter
from app.schemas.order import (
    OrderCreate, OrderDetail, OrderFilter, OrderRead, OrderStatusHistoryRead, OrderStatusUpdate,
)
from app.services.access import assert_order_access, order_scope
from app.services.order_service import create_order_from_cart
from app.services.workflow import transition_order
from app.utils import get_or_404

router = APIRouter(prefix="/orders", tags=["Orders"])


order_sort = sort_params(["created_at", "total", "status", "id", "updated_at"])


def _naive_utc(dt: datetime) -> datetime:
    """DB timestamps are naive UTC; normalise any timezone-aware input to match."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.post("", response_model=OrderDetail, status_code=status.HTTP_201_CREATED,
             summary="Checkout: convert the cart into an order (single DB transaction)")
def create_order(data: OrderCreate, user: User = Depends(require_roles(Role.CUSTOMER)),
                 db: Session = Depends(get_db)):
    return create_order_from_cart(db, user, data)


@router.get("", response_model=Page[OrderRead], summary="List orders visible to the caller")
def list_orders(
    filters: Annotated[OrderListFilter, Query()],
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(order_sort),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == Role.CUSTOMER and filters.customer_id not in (None, user.id):
        raise AuthorizationError("Customers can only list their own orders")
    stmt = select(Order).where(order_scope(db, user))
    if filters.customer_id is not None:
        stmt = stmt.where(Order.customer_id == filters.customer_id)
    if filters.status is not None:
        stmt = stmt.where(Order.status == filters.status)
    if filters.restaurant_id is not None:
        stmt = stmt.where(Order.restaurant_id == filters.restaurant_id)
    if filters.branch_id is not None:
        stmt = stmt.where(Order.branch_id == filters.branch_id)
    if filters.date_from:
        stmt = stmt.where(Order.created_at >= _naive_utc(filters.date_from))
    if filters.date_to:
        stmt = stmt.where(Order.created_at <= _naive_utc(filters.date_to))
    return paginate(db, stmt, Order, page, sort)


@router.get("/{order_id}", response_model=OrderDetail)
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    assert_order_access(db, user, order)
    return order


@router.patch("/{order_id}/status", response_model=OrderDetail,
              summary="Move an order through the state machine (role + transition checked)")
def update_order_status(order_id: int, data: OrderStatusUpdate, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    assert_order_access(db, user, order)
    transition_order(db, order, data.status, user, data.note)
    db.refresh(order)
    return order


@router.get("/{order_id}/status-history", response_model=list[OrderStatusHistoryRead])
def order_status_history(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    assert_order_access(db, user, order)
    return list(db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)
                           .order_by(OrderStatusHistory.id)))
