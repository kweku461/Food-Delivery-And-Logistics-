from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.catalog import (
    CategoryCreate,
    ProductCreate,
    ProductFilter,
    VariantCreate,
)


def test_catalog_create_schemas_validate_business_limits():
    assert ProductCreate(
        category_id=1, name="Jollof", base_price="25.50"
    ).base_price == Decimal("25.50")
    assert VariantCreate(
        product_id=1, name="Large", price_modifier="-2.00"
    ).price_modifier == Decimal("-2.00")
    assert CategoryCreate(menu_id=1, name="Meals", sort_order=2).sort_order == 2

    with pytest.raises(ValidationError):
        ProductCreate(category_id=1, name="Jollof", base_price=0)
    with pytest.raises(ValidationError):
        ProductCreate(category_id=1, name="Jollof", base_price=10, prep_time_minutes=0)


def test_product_filter_rejects_inverted_price_range():
    with pytest.raises(ValidationError):
        ProductFilter(min_price=50, max_price=10)
