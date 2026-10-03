from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cart import (
    CartItemNotFoundError,
    CartNotFoundError,
    CartRestaurantConflictError,
    CartService,
    InvalidVariantError,
    ProductNotAvailableError,
    ProductNotFoundError,
    QuantityLimitError,
)
from app.database import Base
from app.models import Category, Menu, Product, ProductVariant, Restaurant, User
from app.schemas.order import CartItemCreate, CartItemUpdate


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def catalog(db):
    customer = User(email="customer@example.com", hashed_password="x", full_name="Customer")
    other_customer = User(email="other@example.com", hashed_password="x", full_name="Other")
    owner = User(email="owner@example.com", hashed_password="x", full_name="Owner")
    first = Restaurant(owner=owner, name="First Restaurant")
    second = Restaurant(owner=owner, name="Second Restaurant")
    db.add_all([customer, other_customer, owner, first, second])
    db.flush()
    menu = Menu(restaurant_id=first.id, name="Lunch")
    category = Category(menu=menu, name="Meals")
    product = Product(
        category=category,
        restaurant_id=first.id,
        name="Rice",
        base_price=Decimal("12.50"),
    )
    variant = ProductVariant(product=product, name="Large", price_modifier=Decimal("2.00"))
    other_menu = Menu(restaurant_id=second.id, name="Dinner")
    other_category = Category(menu=other_menu, name="Meals")
    other_product = Product(
        category=other_category,
        restaurant_id=second.id,
        name="Soup",
        base_price=Decimal("8.00"),
    )
    db.add_all([menu, category, product, variant, other_menu, other_category, other_product])
    db.commit()
    return {
        "customer_id": customer.id,
        "other_customer_id": other_customer.id,
        "product": product,
        "variant": variant,
        "other_product": other_product,
    }


def test_add_item_merges_same_variant_and_calculates_subtotal(db, catalog):
    service = CartService(db)

    service.add_item(
        catalog["customer_id"],
        CartItemCreate(product_id=catalog["product"].id, variant_id=catalog["variant"].id,
                       quantity=2),
    )
    cart = service.add_item(
        catalog["customer_id"],
        CartItemCreate(product_id=catalog["product"].id, variant_id=catalog["variant"].id,
                       quantity=1),
    )

    result = service.to_read(cart)
    assert len(result.items) == 1
    assert result.items[0].quantity == 3
    assert result.items[0].unit_price == Decimal("14.50")
    assert result.items[0].line_total == Decimal("43.50")
    assert result.subtotal == Decimal("43.50")


def test_add_item_without_variant_uses_base_price(db, catalog):
    service = CartService(db)

    cart = service.add_item(catalog["customer_id"],
                            CartItemCreate(product_id=catalog["product"].id))

    result = service.to_read(cart)
    assert result.items[0].variant is None
    assert result.items[0].unit_price == Decimal("12.50")
    assert result.subtotal == Decimal("12.50")


def test_cart_rejects_second_restaurant_and_wrong_variant(db, catalog):
    service = CartService(db)
    service.add_item(catalog["customer_id"],
                     CartItemCreate(product_id=catalog["product"].id))

    with pytest.raises(CartRestaurantConflictError):
        service.add_item(catalog["customer_id"],
                         CartItemCreate(product_id=catalog["other_product"].id))
    with pytest.raises(InvalidVariantError):
        service.add_item(catalog["customer_id"],
                         CartItemCreate(product_id=catalog["other_product"].id,
                                        variant_id=catalog["variant"].id))


def test_unknown_product_and_unavailable_product(db, catalog):
    service = CartService(db)
    with pytest.raises(ProductNotFoundError):
        service.add_item(catalog["customer_id"], CartItemCreate(product_id=9999))

    catalog["product"].is_available = False
    db.flush()
    with pytest.raises(ProductNotAvailableError):
        service.add_item(catalog["customer_id"],
                         CartItemCreate(product_id=catalog["product"].id))


def test_update_item_is_partial(db, catalog):
    service = CartService(db)
    cart = service.add_item(catalog["customer_id"],
                            CartItemCreate(product_id=catalog["product"].id,
                                           quantity=1, notes="extra spicy"))
    item_id = cart.items[0].id

    cart = service.update_item(catalog["customer_id"], item_id, CartItemUpdate(quantity=5))
    assert cart.items[0].quantity == 5
    assert cart.items[0].notes == "extra spicy"

    cart = service.update_item(catalog["customer_id"], item_id, CartItemUpdate(notes=None))
    assert cart.items[0].quantity == 5
    assert cart.items[0].notes is None


def test_update_and_remove_without_cart_raise_cart_not_found(db, catalog):
    service = CartService(db)
    with pytest.raises(CartNotFoundError):
        service.update_item(catalog["customer_id"], 1, CartItemUpdate(quantity=1))
    with pytest.raises(CartNotFoundError):
        service.remove_item(catalog["customer_id"], 1)


def test_foreign_cart_item_is_not_accessible(db, catalog):
    service = CartService(db)
    mine = service.add_item(catalog["customer_id"],
                            CartItemCreate(product_id=catalog["product"].id))
    theirs = service.add_item(catalog["other_customer_id"],
                              CartItemCreate(product_id=catalog["product"].id))
    foreign_item_id = theirs.items[0].id

    with pytest.raises(CartItemNotFoundError):
        service.update_item(catalog["customer_id"], foreign_item_id, CartItemUpdate(quantity=2))
    with pytest.raises(CartItemNotFoundError):
        service.remove_item(catalog["customer_id"], foreign_item_id)
    assert [item.id for item in mine.items] == [mine.items[0].id]


def test_merged_quantity_is_capped(db, catalog):
    service = CartService(db)
    service.add_item(catalog["customer_id"],
                     CartItemCreate(product_id=catalog["product"].id, quantity=50))

    with pytest.raises(QuantityLimitError):
        service.add_item(catalog["customer_id"],
                         CartItemCreate(product_id=catalog["product"].id, quantity=1))
    cart = service.get(catalog["customer_id"])
    assert cart.items[0].quantity == 50


def test_clear_resets_restaurant_lock(db, catalog):
    service = CartService(db)
    service.add_item(catalog["customer_id"],
                     CartItemCreate(product_id=catalog["product"].id))

    cart = service.clear(catalog["customer_id"])

    assert cart.items == []
    assert cart.restaurant_id is None
