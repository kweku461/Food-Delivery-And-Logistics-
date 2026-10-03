import time

import jwt

from app.config import settings
from app.security import create_access_token
from tests.conftest import H, PW

def H(token):
    return {"Authorization": f"Bearer {token}"}

PW = "password123"

def test_register_returns_token_and_user(client):
    r = client.post("/auth/register", json={"email": "New@Test.com", "password": PW, "full_name": "New User"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@test.com"      # normalised
    assert body["role"] == "CUSTOMER"
    assert "hashed_password" not in body
    login = client.post("/auth/login", json={"email": "new@test.com", "password": PW})
    assert login.status_code == 200 and login.json()["token_type"] == "bearer"


def test_passwords_are_hashed_in_db(client, db):
    from app.models import User
    client.post("/auth/register", json={"email": "h@test.com", "password": PW, "full_name": "Hash Me"})
    u = db.query(User).filter_by(email="h@test.com").one()
    assert u.hashed_password != PW and u.hashed_password.startswith("$2")


def test_duplicate_email_conflict(client):
    body = {"email": "dup@test.com", "password": PW, "full_name": "Dup"}
    assert client.post("/auth/register", json=body).status_code == 201
    r = client.post("/auth/register", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_cannot_self_register_as_admin_or_staff(client):
    for role in ("ADMIN", "STAFF"):
        r = client.post("/auth/register", json={"email": f"{role}@t.com", "password": PW,
                                                "full_name": "Sneaky", "role": role})
        # public registration always creates a CUSTOMER; a requested role is ignored
        assert r.status_code == 201
        assert r.json()["role"] == "CUSTOMER"


def test_register_validation(client):
    r = client.post("/auth/register", json={"email": "not-an-email", "password": "short"})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"email", "password", "full_name"} <= fields


def test_login_success_and_me(client, register):
    u = register("CUSTOMER", email="me@test.com")
    r = client.post("/auth/login", json={"email": "me@test.com", "password": PW})
    assert r.status_code == 200
    me = client.get("/auth/me", headers=H(r.json()["access_token"]))
    assert me.status_code == 200 and me.json()["id"] == u.id


def test_login_wrong_password_and_unknown_user(client, register):
    register("CUSTOMER", email="x@test.com")
    for email, pw in (("x@test.com", "wrongpass123"), ("ghost@test.com", PW)):
        r = client.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_protected_route_requires_token(client):
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_invalid_and_tampered_tokens_rejected(client, register):
    u = register("CUSTOMER")
    assert client.get("/auth/me", headers=H("garbage.token.value")).status_code == 401
    forged = jwt.encode({"sub": str(u.id), "role": "ADMIN", "exp": time.time() + 600}, "wrong-secret-that-is-long-enough-0123456789", "HS256")
    assert client.get("/auth/me", headers=H(forged)).status_code == 401


def test_expired_token_rejected(client, register):
    u = register("CUSTOMER")
    expired = jwt.encode({"sub": str(u.id), "role": "CUSTOMER", "exp": time.time() - 10},
                         settings.secret_key, settings.algorithm)
    r = client.get("/auth/me", headers=H(expired))
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"].lower()


def test_token_role_claim_is_not_trusted(client, register):
    """Role is always read from the DB, so a stale/forged role claim grants nothing."""
    cust = register("CUSTOMER")
    token = create_access_token(cust.id, "ADMIN")
    assert client.get("/users", headers=H(token)).status_code == 403


def test_deactivated_user_locked_out(client, register, admin):
    u = register("CUSTOMER")
    r = client.patch(f"/users/{u.id}", headers=admin.h, json={"is_active": False})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=u.h).status_code == 401
    assert client.post("/auth/login", json={"email": u.email, "password": PW}).status_code == 403


def test_change_password(client, register):
    u = register("CUSTOMER", email="pw@test.com")
    bad = client.post("/auth/change-password", headers=u.h,
                      json={"current_password": "nope12345", "new_password": "NewPassword1"})
    assert bad.status_code == 401
    ok = client.post("/auth/change-password", headers=u.h,
                     json={"current_password": PW, "new_password": "NewPassword1"})
    assert ok.status_code == 204
    assert client.post("/auth/login", json={"email": "pw@test.com", "password": "NewPassword1"}).status_code == 200


def test_unknown_route_uses_error_format(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
