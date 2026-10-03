from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.enums import Role
from app.errors import AuthorizationError, NotFoundError
from app.models import Address, CustomerProfile, User
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.schemas.user import (
    AddressCreate, AddressRead, AddressUpdate, CustomerProfileRead, CustomerProfileUpdate,
)
from app.utils import get_or_404

router = APIRouter(prefix="/customers", tags=["Customers"])
address_sort = sort_params(["created_at", "city", "label", "id"])


def _customer_for(db: Session, user: User, customer_id: int) -> User:
    """Customers reach only their own data; admins reach everyone's."""
    if user.role != Role.ADMIN and user.id != customer_id:
        raise AuthorizationError("You can only access your own customer data")
    target = get_or_404(db, User, customer_id, "Customer")
    if target.role != Role.CUSTOMER:
        raise NotFoundError("Customer not found")
    return target


@router.get("/{customer_id}/profile", response_model=CustomerProfileRead)
def get_profile(customer_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = _customer_for(db, user, customer_id)
    if target.profile is None:
        target.profile = CustomerProfile(user_id=target.id)
        db.commit()
    return target.profile


@router.patch("/{customer_id}/profile", response_model=CustomerProfileRead)
def update_profile(customer_id: int, data: CustomerProfileUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    target = _customer_for(db, user, customer_id)
    if target.profile is None:
        target.profile = CustomerProfile(user_id=target.id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(target.profile, k, v)
    db.commit()
    return target.profile


def _unset_defaults(db: Session, user_id: int, keep_id: int | None = None) -> None:
    stmt = update(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
    if keep_id is not None:
        stmt = stmt.where(Address.id != keep_id)
    db.execute(stmt.values(is_default=False))


@router.get("/{customer_id}/addresses", response_model=Page[AddressRead])
def list_addresses(customer_id: int, page: PageParams = Depends(page_params),
                   sort: SortParams = Depends(address_sort), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _customer_for(db, user, customer_id)
    stmt = select(Address).where(Address.user_id == customer_id)
    return paginate(db, stmt, Address, page, sort)


@router.post("/{customer_id}/addresses", response_model=AddressRead, status_code=status.HTTP_201_CREATED)
def create_address(customer_id: int, data: AddressCreate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _customer_for(db, user, customer_id)
    first = db.scalar(select(Address.id).where(Address.user_id == customer_id)) is None
    addr = Address(user_id=customer_id, **data.model_dump())
    if first:
        addr.is_default = True
    db.add(addr)
    db.flush()
    if addr.is_default:
        _unset_defaults(db, customer_id, keep_id=addr.id)
    db.commit()
    return addr


def _address(db: Session, customer_id: int, address_id: int) -> Address:
    addr = get_or_404(db, Address, address_id, "Address")
    if addr.user_id != customer_id:
        raise NotFoundError("Address not found")
    return addr


@router.patch("/{customer_id}/addresses/{address_id}", response_model=AddressRead)
def update_address(customer_id: int, address_id: int, data: AddressUpdate,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _customer_for(db, user, customer_id)
    addr = _address(db, customer_id, address_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(addr, k, v)
    if addr.is_default:
        _unset_defaults(db, customer_id, keep_id=addr.id)
    db.commit()
    return addr


@router.delete("/{customer_id}/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(customer_id: int, address_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _customer_for(db, user, customer_id)
    db.delete(_address(db, customer_id, address_id))
    db.commit()
