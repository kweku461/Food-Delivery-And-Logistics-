from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, computed_field, model_validator

from app.enums import OrderStatus, PaymentMethod
from app.schemas.common import ORMModel, Timestamped
from app.schemas.delivery import DeliveryRead
from app.schemas.payment import PaymentRead


# ── Cart ──
class CartItemCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    quantity: int = Field(1, ge=1, le=50)
    notes: str | None = Field(None, max_length=255)


class CartItemUpdate(BaseModel):
    quantity: int | None = Field(None, ge=1, le=50)
    notes: str | None = Field(None, max_length=255)


class _ProductBrief(ORMModel):
    id: int
    name: str
    base_price: Decimal
    is_available: bool


class _VariantBrief(ORMModel):
    id: int
    name: str
    price_modifier: Decimal


class CartItemRead(ORMModel):
    id: int
    quantity: int
    notes: str | None
    product: _ProductBrief
    variant: _VariantBrief | None

    @computed_field
    @property
    def unit_price(self) -> Decimal:
        return self.product.base_price + (self.variant.price_modifier if self.variant else Decimal("0"))

    @computed_field
    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class CartRead(ORMModel):
    id: int
    restaurant_id: int | None
    items: list[CartItemRead] = []

    @computed_field
    @property
    def subtotal(self) -> Decimal:
        return sum((i.line_total for i in self.items), Decimal("0"))


# ── Orders ──
class OrderCreate(BaseModel):           # items come from the cart; totals are computed server-side
    branch_id: int
    delivery_address_id: int
    payment_method: PaymentMethod
    notes: str | None = Field(None, max_length=500)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    note: str | None = Field(None, max_length=255)


class OrderItemRead(ORMModel):
    id: int
    product_id: int | None
    variant_id: int | None
    product_name: str
    variant_name: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    notes: str | None


class OrderStatusHistoryRead(ORMModel):
    id: int
    order_id: int
    from_status: OrderStatus | None
    to_status: OrderStatus
    changed_by_id: int | None
    note: str | None
    created_at: datetime


class OrderRead(Timestamped):
    order_number: str
    customer_id: int
    restaurant_id: int
    branch_id: int
    status: OrderStatus
    subtotal: Decimal
    delivery_fee: Decimal
    total: Decimal
    notes: str | None
    delivery_address_text: str


class OrderDetail(OrderRead):
    items: list[OrderItemRead] = []
    payment: PaymentRead | None = None
    delivery: DeliveryRead | None = None


class OrderFilter(BaseModel):
    customer_id: int | None = None
    status: OrderStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    @model_validator(mode="after")
    def _date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        return self