"""Cart operations.

The service owns cart invariants while callers own the SQLAlchemy session
transaction. This keeps the cart usable from HTTP handlers and checkout code.

Errors extend the centralized AppError hierarchy (app.errors), so the API's
registered handlers translate them into the standard error envelope.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.errors import AppError
from app.models import Cart, CartItem, Product, ProductVariant
from app.schemas.order import CartItemCreate, CartItemUpdate, CartRead

# keep in sync with the quantity cap on the shared cart schemas (app.schemas.order)
MAX_QUANTITY_PER_ITEM = 50


class CartError(AppError):
    """Base error for an invalid cart operation (400)."""

    status_code, code = 400, "BAD_REQUEST"


class CartNotFoundError(CartError):
    status_code, code = 404, "NOT_FOUND"


class CartItemNotFoundError(CartError):
    status_code, code = 404, "NOT_FOUND"


class ProductNotFoundError(CartError):
    status_code, code = 404, "NOT_FOUND"


class ProductNotAvailableError(CartError):
    status_code, code = 409, "CONFLICT"


class CartRestaurantConflictError(CartError):
    status_code, code = 409, "CONFLICT"


class InvalidVariantError(CartError):
    status_code, code = 400, "BAD_REQUEST"


class QuantityLimitError(CartError):
    status_code, code = 400, "BAD_REQUEST"


class CartService:
    """Create and mutate one customer's cart."""

    def __init__(self, db: Session):
        self.db = db

    def get_or_create(self, customer_id: int) -> Cart:
        cart = self._load(customer_id)
        if cart is None:
            cart = Cart(customer_id=customer_id)
            self.db.add(cart)
            self.db.flush()
        return cart

    def get(self, customer_id: int) -> Cart:
        cart = self._load(customer_id)
        if cart is None:
            raise CartNotFoundError("Cart not found")
        return cart

    def add_item(self, customer_id: int, item: CartItemCreate) -> Cart:
        cart = self.get_or_create(customer_id)
        product = self._get_product(item.product_id)
        variant = self._get_variant(item.variant_id, product)
        self._check_restaurant(cart, product)

        existing = next(
            (
                cart_item
                for cart_item in cart.items
                if cart_item.product_id == product.id and cart_item.variant_id == item.variant_id
            ),
            None,
        )
        if existing is None:
            cart.items.append(
                CartItem(
                    product_id=product.id,
                    variant_id=variant.id if variant else None,
                    quantity=item.quantity,
                    notes=item.notes,
                    product=product,
                    variant=variant,
                )
            )
        else:
            if existing.quantity + item.quantity > MAX_QUANTITY_PER_ITEM:
                raise QuantityLimitError(
                    f"A single cart item is limited to {MAX_QUANTITY_PER_ITEM} units"
                )
            existing.quantity += item.quantity
            if item.notes is not None:
                existing.notes = item.notes
        self.db.flush()
        return cart

    def update_item(self, customer_id: int, item_id: int, update: CartItemUpdate) -> Cart:
        cart = self.get(customer_id)
        cart_item = self._find_item(cart, item_id)
        for field, value in update.model_dump(exclude_unset=True).items():
            setattr(cart_item, field, value)
        self.db.flush()
        return cart

    def remove_item(self, customer_id: int, item_id: int) -> Cart:
        cart = self.get(customer_id)
        cart.items.remove(self._find_item(cart, item_id))
        self.db.flush()
        return cart

    def clear(self, customer_id: int) -> Cart:
        cart = self.get(customer_id)
        cart.items.clear()
        cart.restaurant_id = None
        self.db.flush()
        return cart

    @staticmethod
    def to_read(cart: Cart) -> CartRead:
        """Serialize a cart with the shared cart schemas (prices are computed there)."""
        return CartRead.model_validate(cart)

    def _load(self, customer_id: int) -> Cart | None:
        return self.db.scalar(
            select(Cart)
            .options(
                selectinload(Cart.items).selectinload(CartItem.product),
                selectinload(Cart.items).selectinload(CartItem.variant),
            )
            .where(Cart.customer_id == customer_id)
        )

    def _get_product(self, product_id: int) -> Product:
        product = self.db.get(Product, product_id)
        if product is None:
            raise ProductNotFoundError("Product not found")
        if not product.is_available:
            raise ProductNotAvailableError("Product is unavailable")
        return product

    def _get_variant(self, variant_id: int | None, product: Product) -> ProductVariant | None:
        if variant_id is None:
            return None
        variant = self.db.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id or not variant.is_available:
            raise InvalidVariantError("Variant does not belong to the selected product")
        return variant

    @staticmethod
    def _check_restaurant(cart: Cart, product: Product) -> None:
        if cart.restaurant_id is not None and cart.restaurant_id != product.restaurant_id:
            raise CartRestaurantConflictError("A cart may only contain products from one restaurant")
        if cart.restaurant_id is None:
            cart.restaurant_id = product.restaurant_id

    @staticmethod
    def _find_item(cart: Cart, item_id: int) -> CartItem:
        for item in cart.items:
            if item.id == item_id:
                return item
        raise CartItemNotFoundError("Cart item not found")
