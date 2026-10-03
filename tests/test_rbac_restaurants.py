import pytest

from tests.conftest import H


# ───────── role permissions ─────────
@pytest.mark.parametrize("role,expected", [("CUSTOMER", 403), ("DRIVER", 403)])
def test_only_owner_or_admin_can_create_restaurant(client, register, role, expected):
    u = register(role)
    r = client.post("/restaurants", headers=u.h, json={"name": "Nope Place"})
    assert r.status_code == expected
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_anonymous_cannot_create_restaurant(client):
    assert client.post("/restaurants", json={"name": "Anon Eats"}).status_code == 401


def test_only_restaurant_owners_create_restaurants(client, admin, register):
    # Nana's RestaurantCreate has no owner_id: the caller becomes the owner
    assert client.post("/restaurants", headers=admin.h, json={"name": "Admin Eats"}).status_code == 403
    assert client.post("/restaurants", headers=register("CUSTOMER").h, json={"name": "Cust Eats"}).status_code == 403
    owner = register("RESTAURANT_OWNER")
    r = client.post("/restaurants", headers=owner.h, json={"name": "Owner Eats"})
    assert r.status_code == 201 and r.json()["owner_id"] == owner.id


def test_owner_id_in_body_is_ignored(client, register):
    a, b = register("RESTAURANT_OWNER"), register("RESTAURANT_OWNER")
    r = client.post("/restaurants", headers=a.h, json={"name": "Sneaky", "owner_id": b.id})
    assert r.status_code == 201 and r.json()["owner_id"] == a.id


def test_duplicate_restaurant_name(client, world):
    r = client.post("/restaurants", headers=world["owner"].h, json={"name": "Mama's Kitchen"})
    assert r.status_code == 409


def test_owner_cannot_touch_other_owners_restaurant(client, world, register):
    other = register("RESTAURANT_OWNER")
    rid = world["restaurant"]["id"]
    assert client.patch(f"/restaurants/{rid}", headers=other.h, json={"cuisine_type": "x"}).status_code == 403
    assert client.post(f"/restaurants/{rid}/branches", headers=other.h, json={
        "name": "Bad", "address_line": "Somewhere", "city": "Accra"}).status_code == 403
    assert client.post("/menus", headers=other.h, json={"restaurant_id": rid, "name": "Bad"}).status_code == 403
    assert client.post("/products", headers=other.h, json={
        "category_id": world["category"]["id"], "name": "Bad Food", "base_price": "1"}).status_code == 403


def test_staff_can_only_toggle_availability(client, world):
    pid, staff = world["product"]["id"], world["staff"]
    assert client.patch(f"/products/{pid}", headers=staff.h, json={"base_price": "1.00"}).status_code == 403
    r = client.patch(f"/products/{pid}", headers=staff.h, json={"is_available": False})
    assert r.status_code == 200 and r.json()["is_available"] is False
    assert client.post("/products", headers=staff.h, json={
        "category_id": world["category"]["id"], "name": "Staff Food", "base_price": "5"}).status_code == 403


def test_staff_of_other_restaurant_cannot_toggle(client, world, register):
    owner2 = register("RESTAURANT_OWNER")
    rest2 = client.post("/restaurants", headers=owner2.h, json={"name": "Other Place"}).json()
    b2 = client.post(f"/restaurants/{rest2['id']}/branches", headers=owner2.h, json={
        "name": "Branch Two", "address_line": "Road 1", "city": "Accra"}).json()
    s2 = register("STAFF", email="s2@test.com")
    assert client.post(f"/branches/{b2['id']}/staff", headers=owner2.h, json={"user_id": s2.id}).status_code == 201
    r = client.patch(f"/products/{world['product']['id']}", headers=s2.h, json={"is_available": False})
    assert r.status_code == 403


def test_customer_cannot_manage_menu(client, world):
    c = world["customer"]
    assert client.patch(f"/products/{world['product']['id']}", headers=c.h, json={"is_available": False}).status_code == 403
    assert client.delete(f"/categories/{world['category']['id']}", headers=c.h).status_code == 403


def test_admin_user_management_is_admin_only(client, world):
    for who in ("owner", "customer", "staff", "driver"):
        assert client.get("/users", headers=world[who].h).status_code == 403
    assert client.get("/users", headers=world["admin"].h).status_code == 200


def test_admin_cannot_demote_self(client, admin):
    r = client.patch(f"/users/{admin.id}", headers=admin.h, json={"role": "CUSTOMER"})
    assert r.status_code == 409


def test_cart_is_customer_only(client, world):
    for who in ("owner", "driver", "staff", "admin"):
        assert client.get("/cart", headers=world[who].h).status_code == 403


def test_staff_creation_rules(client, world, register):
    rid, bid, owner = world["restaurant"]["id"], world["branch"]["id"], world["owner"]
    cust = register("CUSTOMER", email="cust-as-staff@test.com")
    url = f"/branches/{bid}/staff"
    assert client.post(url, headers=owner.h, json={"user_id": cust.id}).status_code == 409      # not a STAFF user
    assert client.post(url, headers=owner.h, json={"user_id": 99999}).status_code == 404        # unknown user
    assert client.post(url, headers=owner.h, json={"user_id": world["staff"].id}).status_code == 409   # already on branch
    assert client.post(url, headers=world["customer"].h, json={"user_id": cust.id}).status_code == 403
    listing = client.get(f"/restaurants/{rid}/staff", headers=owner.h).json()
    assert listing["total"] == 1 and listing["items"][0]["user_id"] == world["staff"].id


# ───────── restaurants: search / filter / pagination ─────────
@pytest.fixture()
def many_restaurants(client, register, admin):
    owner = register("RESTAURANT_OWNER")
    specs = [
        ("Accra Bites", "Ghanaian", "Accra", 5.6037, -0.1870),
        ("Kumasi Grill", "Grill", "Kumasi", 6.6885, -1.6244),
        ("Kente Pizza", "Italian", "Kumasi", 6.70, -1.62),
        ("Cape Coast Fish", "Seafood", "Cape Coast", 5.1053, -1.2466),
        ("Tamale Suya", "Grill", "Tamale", 9.4034, -0.8424),
    ]
    ids = {}
    for name, cuisine, city, lat, lng in specs:
        r = client.post("/restaurants", headers=owner.h, json={"name": name, "cuisine_type": cuisine}).json()
        client.post(f"/restaurants/{r['id']}/branches", headers=owner.h, json={
            "name": f"{city} branch", "address_line": "Main St", "city": city, "latitude": lat, "longitude": lng})
        ids[name] = r["id"]
    return owner, ids


def test_restaurant_search_and_city_filter(client, many_restaurants):
    _, ids = many_restaurants
    r = client.get("/restaurants", params={"search": "grill"}).json()
    assert {i["name"] for i in r["items"]} == {"Kumasi Grill", "Tamale Suya"}   # name or cuisine
    assert client.get("/restaurants", params={"search": "Ital"}).json()["total"] == 1   # cuisine match
    assert client.get("/restaurants", params={"city": "kumasi"}).json()["total"] == 2
    assert client.get("/restaurants", params={"cuisine_type": "grill"}).json()["total"] == 2


def test_restaurant_location_radius(client, many_restaurants):
    r = client.get("/restaurants", params={"latitude": 6.69, "longitude": -1.62, "radius_km": 10}).json()
    assert {i["name"] for i in r["items"]} == {"Kumasi Grill", "Kente Pizza"}
    wide = client.get("/restaurants", params={"latitude": 6.69, "longitude": -1.62, "radius_km": 100}).json()
    assert wide["total"] == 2
    assert client.get("/restaurants", params={"latitude": 6.69, "longitude": -1.62, "radius_km": 400}).status_code == 422
    assert client.get("/restaurants", params={"latitude": 6.69}).status_code == 422


def test_restaurant_pagination_and_sorting(client, many_restaurants):
    p1 = client.get("/restaurants", params={"page": 1, "limit": 2, "sort_by": "name", "order": "asc"}).json()
    p3 = client.get("/restaurants", params={"page": 3, "limit": 2, "sort_by": "name", "order": "asc"}).json()
    assert p1["total"] == 5 and p1["pages"] == 3 and len(p1["items"]) == 2 and len(p3["items"]) == 1
    assert [i["name"] for i in p1["items"]] == ["Accra Bites", "Cape Coast Fish"]
    desc = client.get("/restaurants", params={"sort_by": "name", "order": "desc"}).json()
    assert desc["items"][0]["name"] == "Tamale Suya"
    beyond = client.get("/restaurants", params={"page": 9, "limit": 2}).json()
    assert beyond["items"] == [] and beyond["total"] == 5


def test_invalid_pagination_and_sort_params(client):
    assert client.get("/restaurants", params={"page": 0}).status_code == 422
    assert client.get("/restaurants", params={"limit": 1000}).status_code == 422
    assert client.get("/restaurants", params={"order": "sideways"}).status_code == 422
    r = client.get("/restaurants", params={"sort_by": "hashed_password"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_active_inactive_filtering(client, many_restaurants, register, admin):
    owner, ids = many_restaurants
    assert client.delete(f"/restaurants/{ids['Tamale Suya']}", headers=owner.h).status_code == 204
    public = client.get("/restaurants").json()
    assert public["total"] == 4 and "Tamale Suya" not in {i["name"] for i in public["items"]}
    assert client.get(f"/restaurants/{ids['Tamale Suya']}").status_code == 404   # hidden from public
    assert client.get("/restaurants", params={"is_active": False}).status_code == 403
    assert client.get("/restaurants", headers=register("CUSTOMER").h, params={"is_active": False}).status_code == 403
    mine = client.get("/restaurants", headers=owner.h, params={"is_active": False}).json()
    assert [i["name"] for i in mine["items"]] == ["Tamale Suya"]
    assert client.get(f"/restaurants/{ids['Tamale Suya']}", headers=owner.h).status_code == 200
    assert client.get("/restaurants", headers=admin.h).json()["total"] == 5
    assert client.get("/restaurants", headers=admin.h, params={"is_active": False}).json()["total"] == 1


def test_branch_listing_hides_inactive_branches_from_public(client, world):
    rid, bid = world["restaurant"]["id"], world["branch"]["id"]
    client.post(f"/restaurants/{rid}/branches", headers=world["owner"].h, json={
        "name": "Closed", "address_line": "Old road", "city": "Kumasi"})
    assert client.get(f"/restaurants/{rid}/branches").json()["total"] == 2
    client.patch(f"/branches/{bid}", headers=world["owner"].h, json={"is_active": False})
    assert client.get(f"/restaurants/{rid}/branches").json()["total"] == 1
    assert client.get(f"/restaurants/{rid}/branches", headers=world["owner"].h).json()["total"] == 2
