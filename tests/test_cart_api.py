"""API-level tests for the cart endpoints.

Self-contained on purpose: the app is assembled here with only the cart router
and the team's centralized error handlers, over an in-memory SQLite database.
Nothing here depends on tests/conftest.py or on a main.py (Henry's Phase 4).
"""
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cart import CartService
from app.database import Base, get_db
from app.enums import Role
from app.errors import register_error_handlers
from app.models import Category, Menu, Product, ProductVariant, Restaurant, User
from app.routers.cart import router as cart_router
from app.schemas.order import CartItemCreate
from app.security import create_access_token


def money(value) -> Decimal:
    return Decimal(str(value))


def bearer(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def client(db):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(cart_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def catalog(db):
    customer = User(email="customer@example.com", hashed_password="x", full_name="Customer")
    other = User(email="other@example.com", hashed_password="x", full_name="Other")
    staff_user = User(email="staff@example.com", hashed_password="x", full_name="Staff",
                      role=Role.STAFF)
    owner = User(email="owner@example.com", hashed_password="x", full_name="Owner")
    first = Restaurant(owner=owner, name="First Restaurant")
    second = Restaurant(owner=owner, name="Second Restaurant")
    db.add_all([customer, other, staff_user, owner, first, second])
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
        "customer": customer,
        "other": other,
        "staff": staff_user,
        "product": product,
        "variant": variant,
        "other_product": other_product,
    }


def test_cart_requires_authentication(client):
    r = client.get("/cart")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_only_customers_have_a_cart(client, catalog):
    r = client.get("/cart", headers=bearer(catalog["staff"]))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_get_cart_creates_an_empty_cart(client, catalog):
    r = client.get("/cart", headers=bearer(catalog["customer"]))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["restaurant_id"] is None
    assert money(body["subtotal"]) == 0


def test_add_item_roundtrip(client, catalog):
    headers = bearer(catalog["customer"])
    r = client.post("/cart/items", headers=headers,
                    json={"product_id": catalog["product"].id, "quantity": 2})
    assert r.status_code == 201
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 2
    assert money(body["items"][0]["unit_price"]) == Decimal("12.50")
    assert money(body["subtotal"]) == Decimal("25.00")
    assert body["restaurant_id"] == catalog["product"].restaurant_id


def test_same_product_and_variant_merge(client, catalog):
    headers = bearer(catalog["customer"])
    product_id, variant_id = catalog["product"].id, catalog["variant"].id
    client.post("/cart/items", headers=headers,
                json={"product_id": product_id, "variant_id": variant_id, "quantity": 2})
    r = client.post("/cart/items", headers=headers,
                    json={"product_id": product_id, "variant_id": variant_id, "quantity": 1})
    assert r.status_code == 201
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 3
    assert money(body["subtotal"]) == Decimal("43.50")


def test_second_restaurant_conflicts(client, catalog):
    headers = bearer(catalog["customer"])
    client.post("/cart/items", headers=headers,
                json={"product_id": catalog["product"].id})
    r = client.post("/cart/items", headers=headers,
                    json={"product_id": catalog["other_product"].id})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_variant_of_other_product_is_rejected(client, catalog):
    r = client.post("/cart/items", headers=bearer(catalog["customer"]),
                    json={"product_id": catalog["other_product"].id,
                          "variant_id": catalog["variant"].id})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_unknown_product_is_not_found(client, catalog):
    r = client.post("/cart/items", headers=bearer(catalog["customer"]),
                    json={"product_id": 9999})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_update_cart_item(client, catalog):
    headers = bearer(catalog["customer"])
    cart = client.post("/cart/items", headers=headers,
                       json={"product_id": catalog["product"].id, "quantity": 1}).json()
    item_id = cart["items"][0]["id"]

    r = client.patch(f"/cart/items/{item_id}", headers=headers, json={"quantity": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["quantity"] == 4
    assert money(body["subtotal"]) == Decimal("50.00")


def test_update_rejects_invalid_quantity(client, catalog):
    headers = bearer(catalog["customer"])
    cart = client.post("/cart/items", headers=headers,
                       json={"product_id": catalog["product"].id}).json()
    r = client.patch(f"/cart/items/{cart['items'][0]['id']}", headers=headers,
                     json={"quantity": 0})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_customer_cannot_touch_another_customers_item(client, db, catalog):
    mine = CartService(db).add_item(catalog["customer"].id,
                                    CartItemCreate(product_id=catalog["product"].id))
    theirs = CartService(db).add_item(catalog["other"].id,
                                      CartItemCreate(product_id=catalog["product"].id))
    db.commit()
    headers = bearer(catalog["customer"])

    assert client.patch(f"/cart/items/{theirs.items[0].id}", headers=headers,
                        json={"quantity": 9}).status_code == 404
    assert client.delete(f"/cart/items/{theirs.items[0].id}",
                         headers=headers).status_code == 404
    assert client.get("/cart", headers=headers).json()["items"][0]["id"] == mine.items[0].id


def test_remove_cart_item(client, catalog):
    headers = bearer(catalog["customer"])
    cart = client.post("/cart/items", headers=headers,
                       json={"product_id": catalog["product"].id}).json()
    item_id = cart["items"][0]["id"]

    r = client.delete(f"/cart/items/{item_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_clear_cart(client, catalog):
    headers = bearer(catalog["customer"])
    client.post("/cart/items", headers=headers,
                json={"product_id": catalog["product"].id})

    r = client.delete("/cart", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["restaurant_id"] is None


def test_validation_errors_use_the_central_envelope(client, catalog):
    r = client.post("/cart/items", headers=bearer(catalog["customer"]), json={})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {"product_id"} <= {detail["field"] for detail in error["details"]}
