from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

from app.schemas.common import Timestamped


class MenuCreate(BaseModel):
    restaurant_id: int
    name: str = Field(max_length=150)
    description: str | None = None


class MenuUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    description: str | None = None
    is_active: bool | None = None


class CategoryCreate(BaseModel):
    menu_id: int
    name: str = Field(max_length=100)
    description: str | None = Field(None, max_length=255)
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=255)
    sort_order: int | None = None


class CategoryRead(Timestamped):
    menu_id: int
    name: str
    description: str | None
    sort_order: int


class MenuRead(Timestamped):
    restaurant_id: int
    name: str
    description: str | None
    is_active: bool


class MenuDetail(MenuRead):
    categories: list[CategoryRead] = []


class VariantCreate(BaseModel):
    product_id: int
    name: str = Field(max_length=100)
    price_modifier: Decimal = Field(Decimal("0.00"), max_digits=10, decimal_places=2)  # may be negative


class VariantUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    price_modifier: Decimal | None = Field(None, max_digits=10, decimal_places=2)
    is_available: bool | None = None


class VariantRead(Timestamped):
    product_id: int
    name: str
    price_modifier: Decimal
    is_available: bool


class ProductCreate(BaseModel):         # restaurant_id is derived from the category's menu
    category_id: int
    name: str = Field(max_length=150)
    description: str | None = None
    base_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    image_url: str | None = Field(None, max_length=500)
    prep_time_minutes: int = Field(15, ge=1, le=240)
    is_available: bool = True


class ProductUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = Field(None, max_length=150)
    description: str | None = None
    base_price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    image_url: str | None = Field(None, max_length=500)
    prep_time_minutes: int | None = Field(None, ge=1, le=240)
    is_available: bool | None = None


class ProductRead(Timestamped):
    category_id: int
    restaurant_id: int
    name: str
    description: str | None
    base_price: Decimal
    image_url: str | None
    prep_time_minutes: int
    is_available: bool


class ProductDetail(ProductRead):
    variants: list[VariantRead] = []


class ProductFilter(BaseModel):
    search: str | None = None
    category: str | None = None         # by name, e.g. ?category=meals
    category_id: int | None = None
    restaurant_id: int | None = None
    min_price: Decimal | None = Field(None, ge=0)
    max_price: Decimal | None = Field(None, ge=0)
    available: bool | None = None

    @model_validator(mode="after")
    def _price_range(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self