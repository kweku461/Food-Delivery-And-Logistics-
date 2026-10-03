"""Cart endpoints (brief #9-#12): one live cart per customer, CUSTOMER role only."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.cart import CartService
from app.database import get_db
from app.deps import require_roles
from app.enums import Role
from app.models import User
from app.schemas.order import CartItemCreate, CartItemUpdate, CartRead

router = APIRouter(prefix="/cart", tags=["Cart"])


@router.get("", response_model=CartRead,
            summary="Get my current cart (an empty one is created on first view)")
def get_my_cart(db: Session = Depends(get_db),
                user: User = Depends(require_roles(Role.CUSTOMER))):
    cart = CartService(db).get_or_create(user.id)
    db.commit()
    return cart


@router.post("/items", response_model=CartRead, status_code=status.HTTP_201_CREATED,
             summary="Add an item to my cart (same product + variant are merged)")
def add_cart_item(data: CartItemCreate, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.CUSTOMER))):
    cart = CartService(db).add_item(user.id, data)
    db.commit()
    return cart


@router.patch("/items/{item_id}", response_model=CartRead,
              summary="Update quantity / notes of one of my cart items")
def update_cart_item(item_id: int, data: CartItemUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_roles(Role.CUSTOMER))):
    cart = CartService(db).update_item(user.id, item_id, data)
    db.commit()
    return cart


@router.delete("/items/{item_id}", response_model=CartRead,
               summary="Remove one item from my cart")
def remove_cart_item(item_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_roles(Role.CUSTOMER))):
    cart = CartService(db).remove_item(user.id, item_id)
    db.commit()
    return cart


@router.delete("", response_model=CartRead, summary="Empty my cart")
def clear_cart(db: Session = Depends(get_db),
               user: User = Depends(require_roles(Role.CUSTOMER))):
    cart = CartService(db).clear(user.id)
    db.commit()
    return cart
