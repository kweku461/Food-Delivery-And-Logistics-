from sqlalchemy.exc import OperationalError

from app.database import get_db


# ───────── customer profile & addresses ─────────
def test_first_address_becomes_default_and_default_is_exclusive(client, register):
    c = register("CUSTOMER")
    mk = lambda line, **kw: client.post(f"/customers/{c.id}/addresses", headers=c.h, json={"address_line": line, "city": "Kumasi", **kw}).json()
    a1 = mk("First Road")
    assert a1["is_default"] is True
    a2 = mk("Second Road", is_default=True)
    items = client.get(f"/customers/{c.id}/addresses", headers=c.h).json()["items"]
    assert {i["id"]: i["is_default"] for i in items} == {a1["id"]: False, a2["id"]: True}
    client.patch(f"/customers/{c.id}/addresses/{a1['id']}", headers=c.h, json={"is_default": True, "label": "Office"})
    items = client.get(f"/customers/{c.id}/addresses", headers=c.h).json()["items"]
    assert sum(i["is_default"] for i in items) == 1 and [i for i in items if i["is_default"]][0]["label"] == "Office"


def test_address_access_control(client, register, admin):
    a, b = register("CUSTOMER"), register("CUSTOMER")
    addr = client.post(f"/customers/{a.id}/addresses", headers=a.h, json={"address_line": "A Road", "city": "Accra"}).json()
    assert client.get(f"/customers/{a.id}/addresses", headers=b.h).status_code == 403
    assert client.post(f"/customers/{a.id}/addresses", headers=b.h, json={"address_line": "Hack Rd", "city": "X Y"}).status_code == 403
    assert client.delete(f"/customers/{a.id}/addresses/{addr['id']}", headers=b.h).status_code == 403
    assert client.get(f"/customers/{a.id}/addresses", headers=admin.h).status_code == 200
    # address id belonging to someone else through my own URL
    mine = client.post(f"/customers/{b.id}/addresses", headers=b.h, json={"address_line": "B Road", "city": "Accra"}).json()
    assert client.patch(f"/customers/{b.id}/addresses/{addr['id']}", headers=b.h, json={"label": "x"}).status_code == 404
    assert client.delete(f"/customers/{b.id}/addresses/{mine['id']}", headers=b.h).status_code == 204
    assert client.get(f"/customers/{a.id}/addresses").status_code == 401


def test_address_validation(client, register):
    c = register("CUSTOMER")
    bad = client.post(f"/customers/{c.id}/addresses", headers=c.h, json={"address_line": "ok road", "city": "Accra", "latitude": 200})
    assert bad.status_code == 422
    assert client.post(f"/customers/{c.id}/addresses", headers=c.h, json={"city": "Accra"}).status_code == 422


def test_customer_profile(client, register, world):
    c = register("CUSTOMER")
    p = client.get(f"/customers/{c.id}/profile", headers=c.h).json()
    assert p["user_id"] == c.id and p["loyalty_points"] == 0
    up = client.patch(f"/customers/{c.id}/profile", headers=c.h, json={"dietary_preferences": "vegetarian", "preferred_payment_method": "MOMO"})
    assert up.status_code == 200 and up.json()["preferred_payment_method"] == "MOMO"
    assert client.patch(f"/customers/{c.id}/profile", headers=c.h, json={"preferred_payment_method": "BITCOIN"}).status_code == 422
    assert client.get(f"/customers/{world['owner'].id}/profile", headers=world["owner"].h).status_code == 404  # owners have no profile
    assert client.get(f"/customers/{c.id}/profile", headers=world["driver"].h).status_code == 403


# ───────── centralised error handling ─────────
def test_every_error_uses_same_envelope(client, world):
    responses = [
        client.get("/auth/me"),                                               # 401
        client.get("/users", headers=world["customer"].h),                    # 403
        client.get("/products/999999"),                                       # 404
        client.post("/auth/register", json={}),                               # 422
        client.post("/auth/register", json={"email": world["customer"].email, "password": "Password123", "full_name": "Dup"}),  # 409
        client.get("/restaurants", params={"sort_by": "nope"}),               # 422
        client.delete("/auth/me"),                                            # 405
    ]
    assert [r.status_code for r in responses] == [401, 403, 404, 422, 409, 422, 405]
    for r in responses:
        body = r.json()
        assert set(body) == {"error"} and {"code", "message", "details"} == set(body["error"])
        assert body["error"]["code"] and body["error"]["message"]


def test_validation_error_details_name_fields(client, world):
    r = client.post("/products", headers=world["owner"].h, json={"category_id": "abc", "base_price": -5})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"category_id", "base_price"} <= fields


def test_malformed_json_body(client, world):
    r = client.post("/products", headers={**world["owner"].h, "Content-Type": "application/json"}, content="{bad json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_integrity_error_becomes_409(client):
    """A DB-level constraint violation that slips past app checks is still a clean 409."""
    from sqlalchemy.exc import IntegrityError

    @client.app.get("/_boom/integrity")
    def boom():
        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed: restaurants.name"))

    r = client.get("/_boom/integrity")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INTEGRITY_ERROR" and "UNIQUE" not in r.text


def test_unhandled_exception_returns_generic_500(client):
    @client.app.get("/_boom/unhandled")
    def boom():
        raise ZeroDivisionError("secret internals")

    r = client.get("/_boom/unhandled")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR" and "secret" not in r.text


def test_database_failure_returns_500_envelope_without_leaking(client, session_factory):
    app = client.app

    def broken_db():
        class Broken:
            def scalar(self, *a, **k):
                raise OperationalError("SELECT secret_table", {}, Exception("connection lost: host=db.internal"))

            def close(self):
                pass
        yield Broken()

    app.dependency_overrides[get_db] = broken_db
    r = client.post("/auth/login", json={"email": "a@b.com", "password": "Password123"})
    assert r.status_code == 500
    body = r.json()["error"]
    assert body["code"] == "DATABASE_ERROR" and "secret_table" not in r.text and "db.internal" not in r.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_openapi_documents_bearer_auth_and_endpoint_count(client):
    spec = client.get("/openapi.json").json()
    ops = [m for p in spec["paths"].values() for m in p if m in ("get", "post", "put", "patch", "delete")]
    assert len(ops) >= 60
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert client.get("/docs").status_code == 200
