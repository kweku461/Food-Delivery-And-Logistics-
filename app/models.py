"""SQLAlchemy models — 20 entities.

Relationship cheat-sheet
  one-to-one  : User-CustomerProfile, User-Driver, User-Cart, Order-Payment, Order-Delivery
  one-to-many : User-Address, Restaurant-Branch, Restaurant-Menu, Menu-Category,
                Category-Product, Product-ProductVariant, Cart-CartItem, Order-OrderItem,
                Order-OrderStatusHistory, Payment-Refund, Delivery-DeliveryStatusHistory, ...
  many-to-many: User <-> RestaurantBranch through RestaurantStaff (association object with
                its own columns: position, is_active)
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import (
    DeliveryStatus, OrderStatus, PaymentMethod, PaymentStatus, RefundStatus, Role,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(e):
    return Enum(e, native_enum=False, length=32, validate_strings=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ───────────────────────── Customer & identity ─────────────────────────
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.CUSTOMER, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    profile: Mapped["CustomerProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    driver: Mapped["Driver | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    cart: Mapped["Cart | None"] = relationship(
        back_populates="customer", uselist=False, cascade="all, delete-orphan"
    )
    owned_restaurants: Mapped[list["Restaurant"]] = relationship(back_populates="owner")
    staff_assignments: Mapped[list["RestaurantStaff"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # many-to-many convenience view (read only; write through RestaurantStaff)
    branches: Mapped[list["RestaurantBranch"]] = relationship(
        secondary="restaurant_staff", viewonly=True
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class CustomerProfile(TimestampMixin, Base):
    __tablename__ = "customer_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime)
    dietary_preferences: Mapped[str | None] = mapped_column(String(255))
    preferred_payment_method: Mapped[PaymentMethod | None] = mapped_column(_enum(PaymentMethod))
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="profile")


class Address(TimestampMixin, Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(50), default="Home")
    address_line: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), index=True)
    landmark: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="addresses")


# ───────────────────────── Restaurant side ─────────────────────────
class Restaurant(TimestampMixin, Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cuisine_type: Mapped[str | None] = mapped_column(String(100), index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    owner: Mapped[User] = relationship(back_populates="owned_restaurants")
    branches: Mapped[list["RestaurantBranch"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    menus: Mapped[list["Menu"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(back_populates="restaurant")


class RestaurantBranch(TimestampMixin, Base):
    __tablename__ = "restaurant_branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    address_line: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(30))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="branches")
    staff: Mapped[list["RestaurantStaff"]] = relationship(
        back_populates="branch", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="branch")


class RestaurantStaff(TimestampMixin, Base):
    """Association object for the User <-> RestaurantBranch many-to-many."""

    __tablename__ = "restaurant_staff"
    __table_args__ = (UniqueConstraint("user_id", "branch_id", name="uq_staff_user_branch"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("restaurant_branches.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[str] = mapped_column(String(100), default="Staff")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="staff_assignments")
    branch: Mapped[RestaurantBranch] = relationship(back_populates="staff")


class Menu(TimestampMixin, Base):
    __tablename__ = "menus"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="menus")
    categories: Mapped[list["Category"]] = relationship(
        back_populates="menu", cascade="all, delete-orphan", order_by="Category.sort_order"
    )


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("menu_id", "name", name="uq_category_menu_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    menu: Mapped[Menu] = relationship(back_populates="categories")
    products: Mapped[list["Product"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    # denormalised from category -> menu -> restaurant so product search stays one join
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), index=True)
    image_url: Mapped[str | None] = mapped_column(String(500))
    prep_time_minutes: Mapped[int] = mapped_column(Integer, default=15)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    category: Mapped[Category] = relationship(back_populates="products")
    restaurant: Mapped[Restaurant] = relationship(back_populates="products")
    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(TimestampMixin, Base):
    __tablename__ = "product_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    price_modifier: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    product: Mapped[Product] = relationship(back_populates="variants")


# ───────────────────────── Ordering ─────────────────────────
class Cart(TimestampMixin, Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # a cart may only hold items from one restaurant at a time
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id"))

    customer: Mapped[User] = relationship(back_populates="cart")
    restaurant: Mapped[Restaurant | None] = relationship()
    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", order_by="CartItem.id"
    )


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL")
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(String(255))

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
    variant: Mapped[ProductVariant | None] = relationship()


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("restaurant_branches.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus), default=OrderStatus.PENDING, index=True
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), index=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    # address is snapshotted so later edits to the address book don't rewrite history
    delivery_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("addresses.id", ondelete="SET NULL")
    )
    delivery_address_text: Mapped[str] = mapped_column(String(500))

    customer: Mapped[User] = relationship(back_populates="orders")
    restaurant: Mapped[Restaurant] = relationship()
    branch: Mapped[RestaurantBranch] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.id"
    )
    payment: Mapped["Payment | None"] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )
    delivery: Mapped["Delivery | None"] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )


class OrderItem(Base):
    """Snapshot of what was bought (name & price at purchase time)."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL")
    )
    product_name: Mapped[str] = mapped_column(String(150))
    variant_name: Mapped[str | None] = mapped_column(String(100))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(String(255))

    order: Mapped[Order] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(_enum(OrderStatus))
    to_status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus))
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="status_history")


# ───────────────────────── Payments ─────────────────────────
class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    method: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod))
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus), default=PaymentStatus.PENDING, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)

    order: Mapped[Order] = relationship(back_populates="payment")
    refunds: Mapped[list["Refund"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", order_by="Refund.id"
    )


class Refund(TimestampMixin, Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[RefundStatus] = mapped_column(
        _enum(RefundStatus), default=RefundStatus.PENDING, index=True
    )
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    processed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


# ───────────────────────── Delivery ─────────────────────────
class Driver(TimestampMixin, Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(50))
    plate_number: Mapped[str | None] = mapped_column(String(30))
    license_number: Mapped[str | None] = mapped_column(String(50))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    current_latitude: Mapped[float | None] = mapped_column(Float)
    current_longitude: Mapped[float | None] = mapped_column(Float)

    user: Mapped[User] = relationship(back_populates="driver")
    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="driver")


class Delivery(TimestampMixin, Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), index=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus), default=DeliveryStatus.UNASSIGNED, index=True
    )
    pickup_address: Mapped[str] = mapped_column(String(500))
    dropoff_address: Mapped[str] = mapped_column(String(500))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)

    order: Mapped[Order] = relationship(back_populates="delivery")
    driver: Mapped[Driver | None] = relationship(back_populates="deliveries")
    status_history: Mapped[list["DeliveryStatusHistory"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan", order_by="DeliveryStatusHistory.id"
    )


class DeliveryStatusHistory(Base):
    __tablename__ = "delivery_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[DeliveryStatus | None] = mapped_column(_enum(DeliveryStatus))
    to_status: Mapped[DeliveryStatus] = mapped_column(_enum(DeliveryStatus))
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    delivery: Mapped[Delivery] = relationship(back_populates="status_history")


ALL_MODELS = [
    User, CustomerProfile, Address, Restaurant, RestaurantBranch, RestaurantStaff, Menu,
    Category, Product, ProductVariant, Cart, CartItem, Order, OrderItem, OrderStatusHistory,
    Payment, Refund, Driver, Delivery, DeliveryStatusHistory,
]
