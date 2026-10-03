import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, configure_sqlite, get_db
from app.enums import Role
from app.main import create_app
from app.models import User
from app.security import hash_password

from app.config import settings

settings.bcrypt_rounds = 4   # fast hashing for tests only
PW = "Password123"


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    event.listen(engine, "connect", lambda dbapi_conn, _: configure_sqlite(dbapi_conn))

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture()
def client(session_factory):
    app = create_app()

    def _get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app, raise_server_exceptions=False)


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class Actor:
    def __init__(self, id, email, token, role):
        self.id, self.email, self.token, self.role = id, email, token, role

    @property
    def h(self):
        return H(self.token)


@pytest.fixture()
def admin(client, session_factory):
    with session_factory() as s:
        u = User(email="admin@test.com", full_name="Admin", hashed_password=hash_password(PW), role=Role.ADMIN)
        s.add(u)
        s.commit()
        uid = u.id
    r = client.post("/auth/login", json={"email": "admin@test.com", "password": PW})
    return Actor(uid, "admin@test.com", r.json()["access_token"], "ADMIN")


@pytest.fixture()
def register(client, admin):
    """Create a user of any role and return a logged-in Actor.

    Customers use the public endpoint; every other role is created by the admin
    (STAFF additionally needs assigning to a branch, DRIVER gets a driver profile).
    """
    def _register(role="CUSTOMER", email=None, **extra):
        email = email or f"{role.lower()}{_register.n}@test.com"
        _register.n += 1
        body = {"email": email, "password": PW, "full_name": f"Test {role.title()}"}
        if role == "CUSTOMER":
            r = client.post("/auth/register", json=body)
        else:
            r = client.post("/users", headers=admin.h, json={**body, "role": role})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        if role == "DRIVER":
            d = client.post("/drivers", headers=admin.h, json={"user_id": uid, **extra})
            assert d.status_code == 201, d.text
        login = client.post("/auth/login", json={"email": email, "password": PW})
        assert login.status_code == 200, login.text
        return Actor(uid, email, login.json()["access_token"], role)

    _register.n = 1
    return _register


@pytest.fixture()
def world(client, register, admin):
    """A fully wired marketplace: owner, restaurant, branch, menu, product, staff, customer, driver."""
    owner = register("RESTAURANT_OWNER")
    customer = register("CUSTOMER")
    driver = register("DRIVER", vehicle_type="Bike", plate_number="GR-123-26")

    r = client.post("/restaurants", headers=owner.h, json={
        "name": "Mama's Kitchen", "cuisine_type": "Ghanaian", "description": "Jollof and more"})
    assert r.status_code == 201, r.text
    rest = r.json()
    b = client.post(f"/restaurants/{rest['id']}/branches", headers=owner.h, json={
        "name": "Kumasi Main", "address_line": "Adum 1", "city": "Kumasi",
        "latitude": 6.6885, "longitude": -1.6244, "delivery_fee": "5.00"}).json()
    menu = client.post("/menus", headers=owner.h, json={"restaurant_id": rest["id"], "name": "Main"}).json()
    cat = client.post("/categories", headers=owner.h, json={"menu_id": menu["id"], "name": "Meals"}).json()
    prod = client.post("/products", headers=owner.h, json={
        "category_id": cat["id"], "name": "Chicken Jollof", "base_price": "40.00",
        "description": "Spicy chicken"}).json()
    var = client.post("/variants", headers=owner.h, json={
        "product_id": prod["id"], "name": "Large", "price_modifier": "10.00"}).json()
    staff = register("STAFF", email="staff@test.com")
    sr = client.post(f"/branches/{b['id']}/staff", headers=owner.h, json={"user_id": staff.id})
    assert sr.status_code == 201, sr.text
    addr = client.post(f"/customers/{customer.id}/addresses", headers=customer.h, json={
        "address_line": "Ayeduase Gate", "city": "Kumasi"}).json()
    drv = client.get("/drivers/me", headers=driver.h).json()
    return dict(admin=admin, owner=owner, customer=customer, driver=driver, staff=staff,
                restaurant=rest, branch=b, menu=menu, category=cat, product=prod, variant=var,
                address=addr, driver_profile=drv)


@pytest.fixture()
def place_order(client, world):
    """Returns a function that fills the cart and checks out; yields the order JSON."""
    def _place(method="CASH", qty=2, variant=True):
        w = world
        body = {"product_id": w["product"]["id"], "quantity": qty}
        if variant:
            body["variant_id"] = w["variant"]["id"]
        assert client.post("/cart/items", headers=w["customer"].h, json=body).status_code == 201
        r = client.post("/orders", headers=w["customer"].h, json={
            "delivery_address_id": w["address"]["id"], "branch_id": w["branch"]["id"], "payment_method": method})
        assert r.status_code == 201, r.text
        return r.json()
    return _place
