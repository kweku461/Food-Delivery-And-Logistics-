from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import User
from app.schemas.catalog import ProductCreate, ProductFilter
from app.schemas.order import OrderFilter
from app.schemas.restaurant import RestaurantFilter
from app.schemas.user import LoginRequest, UserRead, UserRegister


def test_register_lowercases_email():
    u = UserRegister(email="Kofi@Example.COM", password="password1", full_name="Kofi")
    assert u.email == "kofi@example.com"


def test_login_lowercases_email():
    assert LoginRequest(email="Kofi@Example.COM", password="x").email == "kofi@example.com"


def test_register_rejects_short_password():
    with pytest.raises(ValidationError):
        UserRegister(email="a@b.com", password="short", full_name="A")


def test_register_rejects_bad_email():
    with pytest.raises(ValidationError):
        UserRegister(email="not-an-email", password="password1", full_name="A")


def test_register_has_no_role_field():
    assert "role" not in UserRegister.model_fields


def test_user_read_never_exposes_password():
    fields = UserRead.model_fields
    assert "password" not in fields and "hashed_password" not in fields


def test_user_read_from_model_instance(db):
    user = User(email="a@b.com", hashed_password="secret", full_name="A B")
    db.add(user)
    db.flush()
    out = UserRead.model_validate(user)
    assert out.email == "a@b.com"
    assert "secret" not in out.model_dump_json()


def test_product_rejects_non_positive_price():
    with pytest.raises(ValidationError):
        ProductCreate(category_id=1, name="Jollof", base_price=Decimal("0"))
    with pytest.raises(ValidationError):
        ProductCreate(category_id=1, name="Jollof", base_price=Decimal("-5"))


def test_product_filter_price_range():
    with pytest.raises(ValidationError):
        ProductFilter(min_price=100, max_price=20)
    assert ProductFilter(min_price=20, max_price=100).min_price == 20


def test_restaurant_filter_geo_all_or_none():
    assert RestaurantFilter().radius_km is None
    assert RestaurantFilter(latitude=5.6, longitude=-0.19, radius_km=5).radius_km == 5
    with pytest.raises(ValidationError):
        RestaurantFilter(radius_km=5)
    with pytest.raises(ValidationError):
        RestaurantFilter(latitude=5.6, longitude=-0.19)


def test_restaurant_filter_rejects_out_of_range_coordinates():
    with pytest.raises(ValidationError):
        RestaurantFilter(latitude=95, longitude=0, radius_km=5)


def test_order_filter_date_range():
    from datetime import datetime
    with pytest.raises(ValidationError):
        OrderFilter(date_from=datetime(2026, 10, 2), date_to=datetime(2026, 10, 1))