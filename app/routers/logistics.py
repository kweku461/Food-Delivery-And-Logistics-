"""Drivers and deliveries."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_manage_restaurant, get_current_user, require_roles
from app.enums import DeliveryStatus, Role
from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.models import Delivery, DeliveryStatusHistory, Driver, Order, User
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.schemas.extra import DeliveryListFilter
from app.schemas.delivery import (
    AssignDriver, DeliveryFilter, DeliveryRead, DeliveryStatusHistoryRead, DeliveryStatusUpdate,
    DriverCreate, DriverRead, DriverUpdate,
)
from app.services.access import order_scope
from app.services.workflow import assign_driver, update_delivery_status
from app.deps import staff_branch_ids
from app.utils import get_or_404, like_pattern

router = APIRouter(tags=["Drivers & Deliveries"])
DISPATCHERS = (Role.ADMIN, Role.RESTAURANT_OWNER, Role.STAFF)
driver_sort = sort_params(["created_at", "id", "is_available"])
delivery_sort = sort_params(["created_at", "status", "id", "assigned_at", "delivered_at"])


# ═════════════════════════ drivers ═════════════════════════
@router.post("/drivers", response_model=DriverRead, status_code=status.HTTP_201_CREATED,
             summary="Create a driver profile for a DRIVER user (ADMIN)")
def create_driver(data: DriverCreate, db: Session = Depends(get_db),
                  _: User = Depends(require_roles(Role.ADMIN))):
    target = get_or_404(db, User, data.user_id, "User")
    if target.role != Role.DRIVER:
        raise ConflictError("User must have the DRIVER role")
    if target.driver is not None:
        raise ConflictError("This user already has a driver profile")
    driver = Driver(**data.model_dump())
    db.add(driver)
    db.commit()
    return driver


@router.get("/drivers", response_model=Page[DriverRead])
def list_drivers(is_available: bool | None = None, search: str | None = None,
                 page: PageParams = Depends(page_params), sort: SortParams = Depends(driver_sort),
                 db: Session = Depends(get_db), _: User = Depends(require_roles(*DISPATCHERS))):
    stmt = select(Driver).join(User, Driver.user_id == User.id).where(User.is_active.is_(True))
    if is_available is not None:
        stmt = stmt.where(Driver.is_available == is_available)
    if search:
        pat = like_pattern(search)
        stmt = stmt.where(User.full_name.ilike(pat, escape="\\") | Driver.plate_number.ilike(pat, escape="\\"))
    return paginate(db, stmt, Driver, page, sort)


@router.get("/drivers/me", response_model=DriverRead)
def my_driver_profile(user: User = Depends(require_roles(Role.DRIVER))):
    if user.driver is None:
        raise NotFoundError("Driver profile not found")
    return user.driver


@router.get("/drivers/{driver_id}", response_model=DriverRead)
def get_driver(driver_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    driver = get_or_404(db, Driver, driver_id, "Driver")
    if user.role not in DISPATCHERS and driver.user_id != user.id:
        raise AuthorizationError("You can only view your own driver profile")
    return driver


@router.patch("/drivers/{driver_id}", response_model=DriverRead,
              summary="Update a driver (self or ADMIN): vehicle, availability, GPS position")
def update_driver(driver_id: int, data: DriverUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.ADMIN, Role.DRIVER))):
    driver = get_or_404(db, Driver, driver_id, "Driver")
    if user.role != Role.ADMIN and driver.user_id != user.id:
        raise AuthorizationError("You can only update your own driver profile")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_available") is True and any(
            d.status in (DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP, DeliveryStatus.IN_TRANSIT)
            for d in driver.deliveries):
        raise ConflictError("Finish your active delivery before going available")
    for k, v in changes.items():
        setattr(driver, k, v)
    db.commit()
    return driver


# ═════════════════════════ deliveries ═════════════════════════
def _delivery_for(db: Session, user: User, delivery_id: int) -> Delivery:
    delivery = get_or_404(db, Delivery, delivery_id, "Delivery")
    visible = db.scalar(select(Order.id).where(Order.id == delivery.order_id, order_scope(db, user)))
    if visible is None:
        raise AuthorizationError("You do not have access to this delivery")
    return delivery


@router.get("/deliveries", response_model=Page[DeliveryRead])
def list_deliveries(filters: Annotated[DeliveryListFilter, Query()],
                    page: PageParams = Depends(page_params), sort: SortParams = Depends(delivery_sort),
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Delivery).join(Order, Delivery.order_id == Order.id).where(order_scope(db, user))
    if filters.status is not None:
        stmt = stmt.where(Delivery.status == filters.status)
    if filters.driver_id is not None:
        stmt = stmt.where(Delivery.driver_id == filters.driver_id)
    if filters.order_id is not None:
        stmt = stmt.where(Delivery.order_id == filters.order_id)
    return paginate(db, stmt, Delivery, page, sort)


@router.get("/deliveries/{delivery_id}", response_model=DeliveryRead)
def get_delivery(delivery_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _delivery_for(db, user, delivery_id)


@router.post("/deliveries/{delivery_id}/assign-driver", response_model=DeliveryRead,
             summary="Dispatch: assign an available driver (ADMIN / owner / branch staff)")
def assign(delivery_id: int, data: AssignDriver, user: User = Depends(require_roles(*DISPATCHERS)),
           db: Session = Depends(get_db)):
    delivery = _delivery_for(db, user, delivery_id)
    order = delivery.order
    if user.role == Role.RESTAURANT_OWNER and not can_manage_restaurant(db, user, order.restaurant_id):
        raise AuthorizationError("This delivery does not belong to your restaurant")
    if user.role == Role.STAFF and order.branch_id not in staff_branch_ids(db, user):
        raise AuthorizationError("This delivery does not belong to your branch")
    driver = get_or_404(db, Driver, data.driver_id, "Driver")
    return assign_driver(db, delivery, driver, user)


@router.patch("/deliveries/{delivery_id}/status", response_model=DeliveryRead,
              summary="Driver progress update (assigned driver or ADMIN); syncs the order status")
def update_status(delivery_id: int, data: DeliveryStatusUpdate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    delivery = get_or_404(db, Delivery, delivery_id, "Delivery")
    return update_delivery_status(db, delivery, data.status, user, data.note)


@router.get("/deliveries/{delivery_id}/status-history", response_model=list[DeliveryStatusHistoryRead])
def delivery_status_history(delivery_id: int, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    delivery = _delivery_for(db, user, delivery_id)
    return list(db.scalars(select(DeliveryStatusHistory)
                           .where(DeliveryStatusHistory.delivery_id == delivery.id)
                           .order_by(DeliveryStatusHistory.id)))
