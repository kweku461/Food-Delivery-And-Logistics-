from decimal import Decimal

import pytest

from app.models import Cart, CartItem, Delivery, Order, OrderItem, OrderStatusHistory, Payment
from tests.conftest import H


def patch_status(client, actor, order_id, status, **extra):
    return client.patch(f"/orders/{order_id}/status", headers=actor.h, json={"status": status, **extra})


# ───────── checkout ─────────
def test_checkout_creates_order_items_payment_delivery_and_clears_cart(client, world, place_order, db):
    order = place_order(method="MOMO", qty=2)
    # (40 base + 10 large) * 2 = 100 ; + 5 delivery fee
    assert order["status"] == "PENDING"
    assert (order["subtotal"], order["delivery_fee"], order["total"]) == ("100.00", "5.00", "105.00")
    assert order["items"][0]["product_name"] == "Chicken Jollof" and order["items"][0]["variant_name"] == "Large"
    assert order["items"][0]["unit_price"] == "50.00" and order["items"][0]["line_total"] == "100.00"
    assert order["payment"]["status"] == "PENDING" and order["payment"]["method"] == "MOMO"
    assert order["payment"]["amount"] == "105.00"
    assert order["delivery"]["id"] is not None and order["order_number"].startswith("ORD-")
    assert "Ayeduase Gate" in order["delivery_address_text"]

    cart = client.get("/cart", headers=world["customer"].h).json()
    assert cart["items"] == [] and cart["restaurant_id"] is None
    hist = client.get(f"/orders/{order['id']}/status-history", headers=world["customer"].h).json()
    assert [(h["from_status"], h["to_status"]) for h in hist] == [(None, "PENDING")]


def test_order_keeps_price_snapshot_after_menu_changes(client, world, place_order):
    order = place_order(qty=1)
    client.patch(f"/products/{world['product']['id']}", headers=world["owner"].h, json={"base_price": "999.00", "name": "Renamed"})
    again = client.get(f"/orders/{order['id']}", headers=world["customer"].h).json()
    assert again["total"] == "55.00" and again["items"][0]["product_name"] == "Chicken Jollof"


def test_checkout_empty_cart(client, world):
    r = client.post("/orders", headers=world["customer"].h, json={
        "delivery_address_id": world["address"]["id"], "branch_id": world["branch"]["id"], "payment_method": "CASH"})
    assert r.status_code == 400 and "empty" in r.json()["error"]["message"].lower()


def test_checkout_validates_address_and_branch(client, world, register):
    w = world
    client.post("/cart/items", headers=w["customer"].h, json={"product_id": w["product"]["id"]})
    other = register("CUSTOMER")
    other_addr = client.post(f"/customers/{other.id}/addresses", headers=other.h,
                             json={"address_line": "Not Mine", "city": "Accra"}).json()
    base = {"delivery_address_id": w["address"]["id"], "branch_id": w["branch"]["id"], "payment_method": "CASH"}
    assert client.post("/orders", headers=w["customer"].h, json={**base, "delivery_address_id": other_addr["id"]}).status_code == 404
    assert client.post("/orders", headers=w["customer"].h, json={**base, "branch_id": 9999}).status_code == 404
    client.patch(f"/branches/{w['branch']['id']}", headers=w["owner"].h, json={"is_active": False})
    assert client.post("/orders", headers=w["customer"].h, json=base).status_code == 409


def test_checkout_branch_must_match_cart_restaurant(client, world, register):
    w = world
    o2 = register("RESTAURANT_OWNER")
    r2 = client.post("/restaurants", headers=o2.h, json={"name": "Other Kitchen"}).json()
    b2 = client.post(f"/restaurants/{r2['id']}/branches", headers=o2.h, json={
        "name": "Other Branch", "address_line": "Road 9", "city": "Accra"}).json()
    client.post("/cart/items", headers=w["customer"].h, json={"product_id": w["product"]["id"]})
    r = client.post("/orders", headers=w["customer"].h, json={"delivery_address_id": w["address"]["id"], "branch_id": b2["id"], "payment_method": "CASH"})
    assert r.status_code == 400


def test_checkout_reports_all_unavailable_items_and_keeps_cart(client, world):
    w = world
    client.post("/cart/items", headers=w["customer"].h, json={"product_id": w["product"]["id"], "variant_id": w["variant"]["id"]})
    client.patch(f"/products/{w['product']['id']}", headers=w["owner"].h, json={"is_available": False})
    r = client.post("/orders", headers=w["customer"].h, json={"delivery_address_id": w["address"]["id"], "branch_id": w["branch"]["id"], "payment_method": "CASH"})
    assert r.status_code == 409 and r.json()["error"]["details"]
    cart = client.get("/cart", headers=w["customer"].h).json()
    assert len(cart["items"]) == 1 and cart["items"][0]["product"]["is_available"] is False


def test_checkout_is_atomic_on_failure(client, world, monkeypatch, db):
    """If the LAST step (payment record) blows up, nothing may be persisted and the cart survives."""
    from app.services import order_service
    w = world
    client.post("/cart/items", headers=w["customer"].h, json={"product_id": w["product"]["id"], "quantity": 3})

    def boom(*a, **k):
        raise RuntimeError("payment table exploded")

    monkeypatch.setattr(order_service, "create_payment_record", boom)
    r = client.post("/orders", headers=w["customer"].h, json={"delivery_address_id": w["address"]["id"], "branch_id": w["branch"]["id"], "payment_method": "CASH"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    db.expire_all()
    assert db.query(Order).count() == 0 and db.query(OrderItem).count() == 0
    assert db.query(OrderStatusHistory).count() == 0 and db.query(Payment).count() == 0
    assert db.query(Delivery).count() == 0
    cart = client.get("/cart", headers=w["customer"].h).json()
    assert sum(i["quantity"] for i in cart["items"]) == 3                                   # customer can simply retry
    monkeypatch.undo()
    assert client.post("/orders", headers=w["customer"].h, json={"delivery_address_id": w["address"]["id"], "branch_id": w["branch"]["id"], "payment_method": "CASH"}).status_code == 201


def test_only_customers_can_checkout(client, world):
    for who in ("owner", "driver", "staff", "admin"):
        r = client.post("/orders", headers=world[who].h, json={"delivery_address_id": 1, "branch_id": 1, "payment_method": "CASH"})
        assert r.status_code == 403


# ───────── listing / visibility ─────────
def test_order_visibility_by_role(client, world, place_order, register):
    order = place_order()
    oid = order["id"]
    w = world
    for who in ("customer", "owner", "staff", "admin"):
        assert client.get(f"/orders/{oid}", headers=w[who].h).status_code == 200, who
    stranger = register("CUSTOMER")
    assert client.get(f"/orders/{oid}", headers=stranger.h).status_code == 403
    assert client.get(f"/orders/{oid}", headers=w["driver"].h).status_code == 403   # not assigned
    other_owner = register("RESTAURANT_OWNER")
    assert client.get(f"/orders/{oid}", headers=other_owner.h).status_code == 403
    assert client.get(f"/orders/{oid}").status_code == 401
    assert client.get("/orders/99999", headers=w["admin"].h).status_code == 404


def test_order_list_is_scoped(client, world, place_order, register):
    place_order()
    stranger = register("CUSTOMER")
    assert client.get("/orders", headers=stranger.h).json()["total"] == 0
    assert client.get("/orders", headers=world["customer"].h).json()["total"] == 1
    assert client.get("/orders", headers=world["owner"].h).json()["total"] == 1
    assert client.get("/orders", headers=world["staff"].h).json()["total"] == 1
    assert client.get("/orders", headers=world["driver"].h).json()["total"] == 0
    assert client.get("/orders", headers=register("RESTAURANT_OWNER").h).json()["total"] == 0
    assert client.get("/orders", headers=stranger.h, params={"customer_id": world["customer"].id}).status_code == 403


def test_order_list_filters_sorting_pagination(client, world, place_order, db):
    place_order(qty=1)
    second = place_order(qty=3)
    third = place_order(qty=2)
    assert patch_status(client, world["owner"], second["id"], "CONFIRMED").status_code == 200
    h = world["admin"].h
    assert client.get("/orders", headers=h, params={"status": "CONFIRMED"}).json()["total"] == 1
    assert client.get("/orders", headers=h, params={"status": "PENDING"}).json()["total"] == 2
    assert client.get("/orders", headers=h, params={"customer_id": world["customer"].id}).json()["total"] == 3
    assert client.get("/orders", headers=h, params={"restaurant_id": 999}).json()["total"] == 0
    asc = client.get("/orders", headers=h, params={"sort_by": "total", "order": "asc"}).json()
    assert [i["total"] for i in asc["items"]] == ["55.00", "105.00", "155.00"]
    page = client.get("/orders", headers=h, params={"limit": 2, "page": 2, "sort_by": "total", "order": "asc"}).json()
    assert len(page["items"]) == 1 and page["pages"] == 2 and page["items"][0]["id"] == second["id"]
    assert third["id"]


def test_order_date_range_filter(client, world, place_order, db):
    from datetime import datetime, timedelta
    a, b, c = place_order(qty=1), place_order(qty=1), place_order(qty=1)
    now = datetime.utcnow()
    for oid, days in ((a["id"], 10), (b["id"], 5)):
        db.query(Order).filter_by(id=oid).update({"created_at": now - timedelta(days=days)})
    db.commit()
    h = world["admin"].h
    d = lambda n: (now - timedelta(days=n)).date().isoformat()
    assert client.get("/orders", headers=h, params={"date_from": d(6)}).json()["total"] == 2
    assert client.get("/orders", headers=h, params={"date_to": d(6)}).json()["total"] == 1
    assert client.get("/orders", headers=h, params={"date_from": d(11), "date_to": d(4)}).json()["total"] == 2
    assert client.get("/orders", headers=h, params={"date_from": d(1), "date_to": d(9)}).status_code == 422
    assert client.get("/orders", headers=h, params={"date_from": "not-a-date"}).status_code == 422
    assert c["id"]


# ───────── state machine ─────────
def test_happy_path_walks_the_whole_machine(client, world, place_order):
    w = world
    order = place_order(method="CASH")
    oid = order["id"]
    did = order["delivery"]["id"]
    assert patch_status(client, w["owner"], oid, "CONFIRMED").json()["status"] == "CONFIRMED"
    assert patch_status(client, w["staff"], oid, "PREPARING").json()["status"] == "PREPARING"
    assert patch_status(client, w["staff"], oid, "READY_FOR_PICKUP").json()["status"] == "READY_FOR_PICKUP"
    assert client.post(f"/deliveries/{did}/assign-driver", headers=w["staff"].h,
                       json={"driver_id": w["driver_profile"]["id"]}).status_code == 200
    assert patch_status(client, w["driver"], oid, "OUT_FOR_DELIVERY").json()["status"] == "OUT_FOR_DELIVERY"
    done = patch_status(client, w["driver"], oid, "DELIVERED", note="Left at gate")
    assert done.status_code == 200 and done.json()["status"] == "DELIVERED"
    assert done.json()["payment"]["status"] == "PAID"          # cash collected on delivery
    hist = client.get(f"/orders/{oid}/status-history", headers=w["customer"].h).json()
    assert [h["to_status"] for h in hist] == ["PENDING", "CONFIRMED", "PREPARING", "READY_FOR_PICKUP", "OUT_FOR_DELIVERY", "DELIVERED"]
    assert hist[-1]["note"] == "Left at gate" and hist[1]["changed_by_id"] == w["owner"].id
    assert client.get(f"/deliveries/{did}", headers=w["admin"].h).json()["status"] == "DELIVERED"
    assert client.get("/drivers/me", headers=w["driver"].h).json()["is_available"] is True


@pytest.mark.parametrize("target", ["PREPARING", "READY_FOR_PICKUP", "OUT_FOR_DELIVERY", "DELIVERED", "PENDING"])
def test_cannot_skip_states_from_pending(client, world, place_order, target):
    order = place_order()
    r = patch_status(client, world["admin"], order["id"], target)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "INVALID_STATE_TRANSITION"
    assert err["details"]["current"] == "PENDING" and "CONFIRMED" in err["details"]["allowed"]


def test_cannot_go_backwards_or_leave_terminal_states(client, world, place_order):
    order = place_order()
    oid = order["id"]
    patch_status(client, world["owner"], oid, "CONFIRMED")
    patch_status(client, world["owner"], oid, "PREPARING")
    assert patch_status(client, world["owner"], oid, "CONFIRMED").status_code == 409
    assert patch_status(client, world["owner"], oid, "CANCELLED").status_code == 200
    for s in ("PENDING", "CONFIRMED", "DELIVERED", "CANCELLED"):
        assert patch_status(client, world["admin"], oid, s).status_code == 409


def test_invalid_status_value(client, world, place_order):
    order = place_order()
    assert patch_status(client, world["admin"], order["id"], "TELEPORTED").status_code == 422


def test_role_rules_for_status_changes(client, world, place_order, register):
    w = world
    order = place_order()
    oid = order["id"]
    assert patch_status(client, w["customer"], oid, "CONFIRMED").status_code == 403     # customers only cancel
    assert patch_status(client, w["driver"], oid, "CONFIRMED").status_code == 403
    stranger = register("CUSTOMER")
    assert patch_status(client, stranger, oid, "CANCELLED").status_code == 403
    other_owner = register("RESTAURANT_OWNER")
    assert patch_status(client, other_owner, oid, "CONFIRMED").status_code == 403
    assert patch_status(client, w["staff"], oid, "CONFIRMED").status_code == 200
    assert patch_status(client, w["staff"], oid, "PREPARING").status_code == 200
    assert patch_status(client, w["staff"], oid, "READY_FOR_PICKUP").status_code == 200
    assert patch_status(client, w["staff"], oid, "OUT_FOR_DELIVERY").status_code == 403  # drivers/admin only
    assert patch_status(client, w["owner"], oid, "OUT_FOR_DELIVERY").status_code == 403


def test_customer_can_cancel_only_while_pending(client, world, place_order):
    w = world
    o1, o2 = place_order(), place_order()
    r = patch_status(client, w["customer"], o1["id"], "CANCELLED", note="changed my mind")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    patch_status(client, w["owner"], o2["id"], "CONFIRMED")
    assert patch_status(client, w["customer"], o2["id"], "CANCELLED").status_code == 403
    assert client.get(f"/deliveries/{o1['delivery']['id']}", headers=w["admin"].h).json()["status"] == "CANCELLED"


def test_card_orders_cannot_be_confirmed_until_paid(client, world, place_order):
    w = world
    order = place_order(method="CARD")
    r = patch_status(client, w["owner"], order["id"], "CONFIRMED")
    assert r.status_code == 409 and "payment" in r.json()["error"]["message"].lower()
    assert client.post("/payments", headers=w["customer"].h, json={"order_id": order["id"], "method": "CARD", "provider_reference": "tok_ok"}).status_code == 201
    assert patch_status(client, w["owner"], order["id"], "CONFIRMED").status_code == 200


def test_cannot_dispatch_without_driver(client, world, place_order):
    w = world
    order = place_order()
    for s in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        patch_status(client, w["owner"], order["id"], s)
    r = patch_status(client, w["admin"], order["id"], "OUT_FOR_DELIVERY")
    assert r.status_code == 409 and "driver" in r.json()["error"]["message"].lower()
