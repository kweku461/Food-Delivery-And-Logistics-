from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_roles
from app.enums import Role
from app.errors import BadRequestError, ConflictError, NotFoundError
from app.models import Cart, CartItem, Product, ProductVariant, User
from app.schemas.order import CartItemCreate, CartItemUpdate, CartRead
from app.utils import get_or_404

router = APIRouter(prefix="/cart", tags=["Cart"])
customer_only = require_roles(Role.CUSTOMER)
MAX_QTY = 50


def _get_cart(db: Session, user: User) -> Cart:
    if user.cart is None:
        user.cart = Cart(customer_id=user.id)
        db.flush()
    return user.cart


def _cart_out(cart: Cart) -> CartRead:
    return CartRead.model_validate(cart)


def _reset_if_empty(cart: Cart) -> None:
    if not cart.items:
        cart.restaurant_id = None


@router.get("", response_model=CartRead, summary="Current cart")
def get_cart(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = _get_cart(db, user)
    db.commit()
    return _cart_out(cart)


@router.post("/items", response_model=CartRead, status_code=status.HTTP_201_CREATED,
             summary="Add an item (same product+variant increases quantity)")
def add_item(data: CartItemCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    product = get_or_404(db, Product, data.product_id, "Product")
    if not product.is_available:
        raise ConflictError("This product is currently unavailable")
    if not product.restaurant.is_active or not product.category.menu.is_active:
        raise ConflictError("This restaurant is not accepting orders")
    if data.variant_id is not None:
        variant = get_or_404(db, ProductVariant, data.variant_id, "Variant")
        if variant.product_id != product.id:
            raise BadRequestError("Variant does not belong to this product")
        if not variant.is_available:
            raise ConflictError("This variant is currently unavailable")

    cart = _get_cart(db, user)
    if cart.items and cart.restaurant_id != product.restaurant_id:
        raise ConflictError(
            "Your cart contains items from another restaurant. Clear it (DELETE /cart) to start a new order."
        )
    existing = next((i for i in cart.items
                     if i.product_id == data.product_id and i.variant_id == data.variant_id), None)
    if existing:
        if existing.quantity + data.quantity > MAX_QTY:
            raise BadRequestError(f"Maximum quantity per item is {MAX_QTY}")
        existing.quantity += data.quantity
        if data.notes is not None:
            existing.notes = data.notes
    else:
        cart.items.append(CartItem(product_id=data.product_id, variant_id=data.variant_id,
                                   quantity=data.quantity, notes=data.notes))
    cart.restaurant_id = product.restaurant_id
    db.commit()
    db.refresh(cart)
    return _cart_out(cart)


def _owned_item(db: Session, user: User, item_id: int) -> CartItem:
    item = db.get(CartItem, item_id)
    if item is None or user.cart is None or item.cart_id != user.cart.id:
        raise NotFoundError("Cart item not found")
    return item


@router.patch("/items/{item_id}", response_model=CartRead)
def update_item(item_id: int, data: CartItemUpdate, user: User = Depends(customer_only),
                db: Session = Depends(get_db)):
    item = _owned_item(db, user, item_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "quantity" and v is None:
            continue
        setattr(item, k, v)
    db.commit()
    db.refresh(user.cart)
    return _cart_out(user.cart)


@router.delete("/items/{item_id}", response_model=CartRead, summary="Remove an item; returns the updated cart")
def remove_item(item_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    item = _owned_item(db, user, item_id)
    cart = user.cart
    cart.items.remove(item)
    db.flush()
    _reset_if_empty(cart)
    db.commit()
    db.refresh(cart)
    return _cart_out(cart)


@router.delete("", response_model=CartRead, summary="Empty the cart")
def clear_cart(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = _get_cart(db, user)
    cart.items.clear()
    cart.restaurant_id = None
    db.commit()
    db.refresh(cart)
    return _cart_out(cart)
