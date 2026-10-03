import pytest

from app.models import (
    Address, Cart, Category, CustomerProfile, Delivery, DeliveryStatusHistory, Driver, Menu,
    Order, OrderItem, Payment, Product, ProductVariant, Refund, Restaurant, RestaurantBranch,
    RestaurantStaff, User,
)


def pay(client, actor, order_id, **body):
    if "card_token" in body:
        body["provider_reference"] = body.pop("card_token")
    body.setdefault("method", "CARD")
    return client.post("/payments", headers=actor.h, json={"order_id": order_id, **body})


def set_order(client, actor, oid, status):
    r = client.patch(f"/orders/{oid}/status", headers=actor.h, json={"status": status})
    assert r.status_code == 200, r.text
    return r.json()


def to_ready(client, w, order):
    for s in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        set_order(client, w["owner"], order["id"], s)


def assign(client, actor, delivery_id, driver_id):
    return client.post(f"/deliveries/{delivery_id}/assign-driver", headers=actor.h, json={"driver_id": driver_id})


def dstatus(client, actor, delivery_id, status, **extra):
    return client.patch(f"/deliveries/{delivery_id}/status", headers=actor.h, json={"status": status, **extra})


# ═════════════ payments ═════════════
def test_card_payment_success(client, world, place_order):
    order = place_order(method="CARD")
    r = pay(client, world["customer"], order["id"], card_token="tok_visa")
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "PAID" and p["amount"] == "105.00" and p["provider_reference"].startswith("CARD-") and p["paid_at"]
    assert client.get(f"/payments/{p['id']}", headers=world["customer"].h).json()["status"] == "PAID"
    assert client.get(f"/orders/{order['id']}", headers=world["customer"].h).json()["payment"]["status"] == "PAID"


def test_payment_failure_is_recorded_and_retry_allowed(client, world, place_order):
    order = place_order(method="CARD")
    r = pay(client, world["customer"], order["id"], card_token="tok_fail")
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["code"] == "PAYMENT_FAILED" and err["details"]["order_id"] == order["id"]
    stored = client.get(f"/orders/{order['id']}", headers=world["customer"].h).json()["payment"]
    assert stored["status"] == "FAILED" and "declined" in stored["failure_reason"]
    ok = pay(client, world["customer"], order["id"], card_token="tok_visa")          # retry succeeds
    assert ok.status_code == 201 and ok.json()["status"] == "PAID" and ok.json()["failure_reason"] is None


def test_momo_payment(client, world, place_order):
    order = place_order(method="MOMO")
    assert pay(client, world["customer"], order["id"], method="MOMO").status_code == 400   # reference missing
    assert pay(client, world["customer"], order["id"], method="MOMO", provider_reference="0240000000").status_code == 402
    ok = pay(client, world["customer"], order["id"], method="MOMO", provider_reference="0241234567")
    assert ok.status_code == 201 and ok.json()["provider_reference"].startswith("MOMO-")


def test_payment_validation_and_conflicts(client, world, place_order, register):
    order = place_order(method="CARD")
    assert pay(client, world["customer"], order["id"]).status_code == 400            # token missing
    assert pay(client, world["customer"], 9999, card_token="x").status_code == 404
    assert pay(client, world["customer"], order["id"], card_token="tok_visa").status_code == 201
    dup = pay(client, world["customer"], order["id"], card_token="tok_visa")
    assert dup.status_code == 409 and "PAID" in dup.json()["error"]["message"]      # no double charging
    stranger = register("CUSTOMER")
    assert pay(client, stranger, order["id"], card_token="tok_visa").status_code == 403
    assert pay(client, world["owner"], order["id"], card_token="tok_visa").status_code == 403


def test_cannot_pay_for_cancelled_order(client, world, place_order):
    order = place_order(method="CARD")
    set_order(client, world["customer"], order["id"], "CANCELLED")
    assert pay(client, world["customer"], order["id"], card_token="tok_visa").status_code == 409


def test_cash_payment_stays_pending_until_delivery(client, world, place_order):
    order = place_order(method="CASH")
    r = pay(client, world["customer"], order["id"], method="CASH")
    assert r.status_code == 201 and r.json()["status"] == "PENDING"


def test_payment_visibility_and_listing(client, world, place_order, register):
    order = place_order(method="CARD")
    p = pay(client, world["customer"], order["id"], card_token="tok_visa").json()
    assert client.get(f"/payments/{p['id']}", headers=world["owner"].h).status_code == 200
    assert client.get(f"/payments/{p['id']}", headers=register("CUSTOMER").h).status_code == 403
    assert client.get("/payments/9999", headers=world["admin"].h).status_code == 404
    assert client.get("/payments", headers=register("CUSTOMER").h).json()["total"] == 0
    assert client.get("/payments", headers=world["customer"].h, params={"status": "PAID"}).json()["total"] == 1
    assert client.get("/payments", headers=world["customer"].h, params={"status": "FAILED"}).json()["total"] == 0


def test_one_payment_per_order_constraint(db, client, world, place_order):
    from sqlalchemy.exc import IntegrityError
    order = place_order()
    db.add(Payment(order_id=order["id"], method="CASH", amount=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ═════════════ refunds ═════════════
@pytest.fixture()
def paid_order(client, world, place_order):
    order = place_order(method="CARD")
    p = pay(client, world["customer"], order["id"], card_token="tok_visa").json()
    return order, p


def refund(client, actor, payment_id, amount, reason="Food arrived cold"):
    return client.post("/refunds", headers=actor.h, json={"payment_id": payment_id, "amount": amount, "reason": reason})


def test_refund_lifecycle_partial_then_full(client, world, paid_order):
    order, p = paid_order
    r1 = refund(client, world["customer"], p["id"], "30.00")
    assert r1.status_code == 201 and r1.json()["status"] == "PENDING"
    assert client.get(f"/payments/{p['id']}", headers=world["admin"].h).json()["status"] == "PAID"   # not yet processed
    done = client.patch(f"/refunds/{r1.json()['id']}/status", headers=world["owner"].h, json={"status": "PROCESSED"})
    assert done.status_code == 200 and done.json()["processed_by_id"] == world["owner"].id
    assert client.get(f"/payments/{p['id']}", headers=world["admin"].h).json()["status"] == "PARTIALLY_REFUNDED"
    r2 = refund(client, world["customer"], p["id"], "75.00")
    assert r2.status_code == 201
    client.patch(f"/refunds/{r2.json()['id']}/status", headers=world["admin"].h, json={"status": "PROCESSED"})
    assert client.get(f"/payments/{p['id']}", headers=world["admin"].h).json()["status"] == "REFUNDED"
    assert refund(client, world["customer"], p["id"], "1.00").status_code == 409                       # fully refunded


def test_refund_cannot_exceed_balance_including_pending(client, world, paid_order):
    _, p = paid_order
    assert refund(client, world["customer"], p["id"], "105.01").status_code == 400
    assert refund(client, world["customer"], p["id"], "100.00").status_code == 201
    over = refund(client, world["customer"], p["id"], "10.00")
    assert over.status_code == 400 and over.json()["error"]["details"]["refundable_balance"] == "5.00"


def test_rejected_refund_frees_balance(client, world, paid_order):
    _, p = paid_order
    rid = refund(client, world["customer"], p["id"], "105.00").json()["id"]
    assert client.patch(f"/refunds/{rid}/status", headers=world["owner"].h, json={"status": "REJECTED"}).status_code == 200
    assert client.get(f"/payments/{p['id']}", headers=world["admin"].h).json()["status"] == "PAID"
    assert refund(client, world["customer"], p["id"], "105.00").status_code == 201


def test_refund_rules(client, world, place_order, paid_order, register):
    order, p = paid_order
    unpaid = place_order(method="CARD")
    assert refund(client, world["customer"], unpaid["payment"]["id"], "5.00").status_code == 409
    assert refund(client, world["customer"], p["id"], "0").status_code == 422
    assert refund(client, world["customer"], p["id"], "5", reason="x").status_code == 422
    assert refund(client, world["customer"], 9999, "5").status_code == 404
    assert refund(client, register("CUSTOMER"), p["id"], "5.00").status_code == 403
    assert refund(client, world["driver"], p["id"], "5.00").status_code == 403
    rid = refund(client, world["customer"], p["id"], "5.00").json()["id"]
    assert client.patch(f"/refunds/{rid}/status", headers=world["customer"].h, json={"status": "PROCESSED"}).status_code == 403
    assert client.patch(f"/refunds/{rid}/status", headers=register("RESTAURANT_OWNER").h, json={"status": "PROCESSED"}).status_code == 403
    assert client.patch(f"/refunds/{rid}/status", headers=world["owner"].h, json={"status": "PENDING"}).status_code == 400
    assert client.patch(f"/refunds/{rid}/status", headers=world["owner"].h, json={"status": "PROCESSED"}).status_code == 200
    assert client.patch(f"/refunds/{rid}/status", headers=world["owner"].h, json={"status": "REJECTED"}).status_code == 409


def test_refund_reads_and_scoping(client, world, paid_order, register):
    _, p = paid_order
    rid = refund(client, world["customer"], p["id"], "5.00").json()["id"]
    assert client.get(f"/refunds/{rid}", headers=world["customer"].h).status_code == 200
    assert client.get(f"/refunds/{rid}", headers=register("CUSTOMER").h).status_code == 403
    assert client.get("/refunds/9999", headers=world["admin"].h).status_code == 404
    assert client.get("/refunds", headers=world["owner"].h).json()["total"] == 1
    assert client.get("/refunds", headers=register("CUSTOMER").h).json()["total"] == 0
    assert client.get("/refunds", headers=world["admin"].h, params={"status": "PENDING"}).json()["total"] == 1


# ═════════════ drivers & deliveries ═════════════
def test_delivery_created_with_order_and_history(client, world, place_order):
    order = place_order()
    d = client.get(f"/deliveries/{order['delivery']['id']}", headers=world["customer"].h).json()
    assert d["status"] == "UNASSIGNED" and d["driver_id"] is None
    assert "Adum 1" in d["pickup_address"] and "Ayeduase" in d["dropoff_address"]
    hist = client.get(f"/deliveries/{d['id']}/status-history", headers=world["customer"].h).json()
    assert [(h["from_status"], h["to_status"]) for h in hist] == [(None, "UNASSIGNED")]


def test_assign_driver_rules(client, world, place_order, register):
    w = world
    order = place_order()
    did, drv = order["delivery"]["id"], w["driver_profile"]["id"]
    assert assign(client, w["customer"], did, drv).status_code == 403
    assert assign(client, w["driver"], did, drv).status_code == 403
    assert assign(client, register("RESTAURANT_OWNER"), did, drv).status_code == 403
    assert assign(client, w["staff"], did, 9999).status_code == 404
    r = assign(client, w["owner"], did, drv)
    assert r.status_code == 200 and r.json()["status"] == "ASSIGNED" and r.json()["driver_id"] == drv
    assert client.get("/drivers/me", headers=w["driver"].h).json()["is_available"] is False
    assert client.get("/drivers", headers=w["staff"].h, params={"is_available": True}).json()["total"] == 0
    hist = client.get(f"/deliveries/{did}/status-history", headers=w["admin"].h).json()
    assert hist[-1]["to_status"] == "ASSIGNED" and hist[-1]["changed_by_id"] == w["owner"].id


def test_busy_driver_cannot_take_second_delivery(client, world, place_order):
    w = world
    o1, o2 = place_order(), place_order()
    drv = w["driver_profile"]["id"]
    assert assign(client, w["owner"], o1["delivery"]["id"], drv).status_code == 200
    r = assign(client, w["owner"], o2["delivery"]["id"], drv)
    assert r.status_code == 409 and "not available" in r.json()["error"]["message"]


def test_reassign_frees_previous_driver(client, world, place_order, register):
    w = world
    driver2 = register("DRIVER")
    d2 = client.get("/drivers/me", headers=driver2.h).json()["id"]
    order = place_order()
    assign(client, w["owner"], order["delivery"]["id"], w["driver_profile"]["id"])
    assert assign(client, w["owner"], order["delivery"]["id"], d2).status_code == 200
    assert client.get("/drivers/me", headers=w["driver"].h).json()["is_available"] is True
    assert client.get("/drivers/me", headers=driver2.h).json()["is_available"] is False


def test_cannot_assign_to_finished_or_cancelled_order(client, world, place_order):
    order = place_order()
    set_order(client, world["customer"], order["id"], "CANCELLED")
    assert assign(client, world["admin"], order["delivery"]["id"], world["driver_profile"]["id"]).status_code == 409


def test_staff_of_other_branch_cannot_dispatch(client, world, place_order, register):
    w = world
    order = place_order()
    b2 = client.post(f"/restaurants/{w['restaurant']['id']}/branches", headers=w["owner"].h, json={
        "name": "Second Branch", "address_line": "Road 7", "city": "Kumasi"}).json()
    b2staff = register("STAFF", email="b2staff@test.com")
    assert client.post(f"/branches/{b2['id']}/staff", headers=w["owner"].h, json={"user_id": b2staff.id}).status_code == 201
    assert assign(client, b2staff, order["delivery"]["id"], w["driver_profile"]["id"]).status_code == 403
    assert client.get("/orders", headers=b2staff.h).json()["total"] == 0


def test_delivery_status_flow_syncs_order(client, world, place_order):
    w = world
    order = place_order(method="CASH")
    did, oid = order["delivery"]["id"], order["id"]
    assign(client, w["owner"], did, w["driver_profile"]["id"])
    early = dstatus(client, w["driver"], did, "PICKED_UP")
    assert early.status_code == 409 and "READY_FOR_PICKUP" in early.json()["error"]["message"]
    to_ready(client, w, order)
    assert dstatus(client, w["driver"], did, "PICKED_UP").json()["picked_up_at"]
    assert client.get(f"/orders/{oid}", headers=w["customer"].h).json()["status"] == "OUT_FOR_DELIVERY"
    assert dstatus(client, w["driver"], did, "IN_TRANSIT").status_code == 200
    done = dstatus(client, w["driver"], did, "DELIVERED", note="Handed over")
    assert done.status_code == 200 and done.json()["delivered_at"]
    o = client.get(f"/orders/{oid}", headers=w["customer"].h).json()
    assert o["status"] == "DELIVERED" and o["payment"]["status"] == "PAID"
    hist = client.get(f"/deliveries/{did}/status-history", headers=w["driver"].h).json()
    assert [h["to_status"] for h in hist] == ["UNASSIGNED", "ASSIGNED", "PICKED_UP", "IN_TRANSIT", "DELIVERED"]
    assert client.get("/drivers/me", headers=w["driver"].h).json()["is_available"] is True


def test_delivery_invalid_transitions(client, world, place_order):
    w = world
    order = place_order()
    did = order["delivery"]["id"]
    assign(client, w["owner"], did, w["driver_profile"]["id"])
    for bad in ("DELIVERED", "IN_TRANSIT", "FAILED"):
        r = dstatus(client, w["driver"], did, bad)
        assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert dstatus(client, w["driver"], did, "ASSIGNED").status_code == 400
    assert dstatus(client, w["driver"], did, "BROKEN").status_code == 422


def test_only_assigned_driver_or_admin_updates_delivery(client, world, place_order, register):
    w = world
    order = place_order()
    did = order["delivery"]["id"]
    assign(client, w["owner"], did, w["driver_profile"]["id"])
    to_ready(client, w, order)
    other_driver = register("DRIVER")
    assert dstatus(client, other_driver, did, "PICKED_UP").status_code == 403
    assert dstatus(client, w["owner"], did, "PICKED_UP").status_code == 403
    assert dstatus(client, w["customer"], did, "PICKED_UP").status_code == 403
    assert dstatus(client, w["driver"], did, "CANCELLED").status_code == 403
    assert dstatus(client, w["admin"], did, "PICKED_UP").status_code == 200


def test_failed_delivery_can_be_redispatched(client, world, place_order, register):
    w = world
    order = place_order()
    did = order["delivery"]["id"]
    assign(client, w["owner"], did, w["driver_profile"]["id"])
    to_ready(client, w, order)
    dstatus(client, w["driver"], did, "PICKED_UP")
    assert dstatus(client, w["driver"], did, "FAILED", note="Customer unreachable").status_code == 200
    assert client.get("/drivers/me", headers=w["driver"].h).json()["is_available"] is True
    d2 = register("DRIVER")
    drv2 = client.get("/drivers/me", headers=d2.h).json()["id"]
    assert assign(client, w["owner"], did, drv2).json()["status"] == "ASSIGNED"


def test_driver_sees_only_own_deliveries(client, world, place_order, register):
    w = world
    order = place_order()
    assert client.get("/deliveries", headers=w["driver"].h).json()["total"] == 0
    assert client.get(f"/deliveries/{order['delivery']['id']}", headers=w["driver"].h).status_code == 403
    assign(client, w["owner"], order["delivery"]["id"], w["driver_profile"]["id"])
    assert client.get("/deliveries", headers=w["driver"].h).json()["total"] == 1
    assert client.get(f"/deliveries/{order['delivery']['id']}", headers=w["driver"].h).status_code == 200
    assert client.get("/deliveries", headers=register("DRIVER").h).json()["total"] == 0
    assert client.get("/deliveries", headers=w["admin"].h, params={"status": "ASSIGNED"}).json()["total"] == 1
    assert client.get("/deliveries", headers=w["admin"].h, params={"status": "DELIVERED"}).json()["total"] == 0


def test_driver_profile_management(client, world, register, admin):
    w = world
    did = w["driver_profile"]["id"]
    r = client.patch(f"/drivers/{did}", headers=w["driver"].h, json={"current_latitude": 6.69, "current_longitude": -1.62, "vehicle_type": "Car"})
    assert r.status_code == 200 and r.json()["vehicle_type"] == "Car"
    assert client.patch(f"/drivers/{did}", headers=register("DRIVER").h, json={"vehicle_type": "x"}).status_code == 403
    assert client.patch(f"/drivers/{did}", headers=w["driver"].h, json={"current_latitude": 123}).status_code == 422
    assert client.get("/drivers", headers=w["customer"].h).status_code == 403
    assert client.get(f"/drivers/{did}", headers=w["customer"].h).status_code == 403
    assert client.get(f"/drivers/{did}", headers=w["owner"].h).status_code == 200
    # admin creates profile only for DRIVER users without one
    assert client.post("/drivers", headers=admin.h, json={"user_id": w["driver"].id}).status_code == 409
    assert client.post("/drivers", headers=admin.h, json={"user_id": w["customer"].id}).status_code == 409
    assert client.post("/drivers", headers=w["owner"].h, json={"user_id": w["driver"].id}).status_code == 403


def test_active_driver_cannot_self_mark_available(client, world, place_order):
    w = world
    order = place_order()
    assign(client, w["owner"], order["delivery"]["id"], w["driver_profile"]["id"])
    r = client.patch(f"/drivers/{w['driver_profile']['id']}", headers=w["driver"].h, json={"is_available": True})
    assert r.status_code == 409


# ═════════════ database relationships ═════════════
def test_database_relationships_end_to_end(db, client, world, place_order):
    w = world
    order = place_order(method="CARD")
    pay(client, w["customer"], order["id"], card_token="tok_visa")
    refund(client, w["customer"], order["payment"]["id"], "5.00")
    assign(client, w["owner"], order["delivery"]["id"], w["driver_profile"]["id"])
    db.expire_all()

    cust = db.get(User, w["customer"].id)
    assert cust.profile is not None and cust.profile.user is cust                      # one-to-one
    assert len(cust.addresses) == 1 and cust.addresses[0].user is cust                 # one-to-many
    assert cust.cart is not None and cust.cart.customer is cust                        # one-to-one
    assert len(cust.orders) == 1

    rest = db.get(Restaurant, w["restaurant"]["id"])
    assert rest.owner.id == w["owner"].id and rest.owner.owned_restaurants == [rest]
    assert len(rest.branches) == 1 and len(rest.menus) == 1
    assert rest.menus[0].categories[0].products[0].variants[0].name == "Large"         # 4-level chain

    staff_user = db.get(User, w["staff"].id)                                           # many-to-many
    assert [b.id for b in staff_user.branches] == [w["branch"]["id"]]
    assert staff_user.staff_assignments[0].branch.staff[0].user is staff_user
    branch = db.get(RestaurantBranch, w["branch"]["id"])
    assert [s.user_id for s in branch.staff] == [w["staff"].id]

    o = db.get(Order, order["id"])
    assert o.payment.order is o and o.delivery.order is o                              # one-to-one
    assert len(o.items) == 1 and len(o.status_history) == 1
    assert o.payment.refunds[0].payment is o.payment                                   # payment -> refund
    assert o.delivery.driver.user_id == w["driver"].id and len(o.delivery.status_history) == 2
    assert o.delivery.driver.deliveries == [o.delivery]


def test_twenty_tables_exist(db):
    from app.database import Base
    assert len(Base.metadata.tables) == 20


def test_cascade_delete_user_removes_dependents(db, register, client):
    cust = register("CUSTOMER")
    client.post(f"/customers/{cust.id}/addresses", headers=cust.h, json={"address_line": "Some Road", "city": "Accra"})
    client.get("/cart", headers=cust.h)
    db.expire_all()
    db.delete(db.get(User, cust.id))
    db.commit()
    assert db.query(Address).filter_by(user_id=cust.id).count() == 0
    assert db.query(CustomerProfile).filter_by(user_id=cust.id).count() == 0
    assert db.query(Cart).filter_by(customer_id=cust.id).count() == 0
