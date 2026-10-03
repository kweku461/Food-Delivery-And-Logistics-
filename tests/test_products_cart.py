import pytest


@pytest.fixture()
def catalog(client, world):
    """Extra products across two categories for search/filter tests."""
    o, menu = world["owner"], world["menu"]
    drinks = client.post("/categories", headers=o.h, json={"menu_id": menu["id"], "name": "Drinks"}).json()
    rows = [
        (world["category"]["id"], "Chicken Fried Rice", "35.00"),
        (world["category"]["id"], "Beef Kebab", "60.00"),
        (world["category"]["id"], "Goat Light Soup", "120.00"),
        (drinks["id"], "Chicken Broth Shot", "15.00"),
        (drinks["id"], "Sobolo", "10.00"),
    ]
    made = {}
    for cat_id, name, price in rows:
        made[name] = client.post("/products", headers=o.h, json={
            "category_id": cat_id, "name": name, "base_price": price}).json()
    client.patch(f"/products/{made['Beef Kebab']['id']}", headers=o.h, json={"is_available": False})
    return made


def names(resp):
    return sorted(i["name"] for i in resp.json()["items"])


# ───────── products: advanced search ─────────
def test_product_combined_search_from_brief(client, catalog):
    r = client.get("/products", params={"search": "chicken", "category": "meals", "min_price": 20,
                                        "max_price": 100, "available": True, "page": 1, "limit": 20})
    assert r.status_code == 200
    assert names(r) == ["Chicken Fried Rice", "Chicken Jollof"]


def test_product_filters_individually(client, catalog, world):
    assert names(client.get("/products", params={"category": "DRINKS"})) == ["Chicken Broth Shot", "Sobolo"]
    assert names(client.get("/products", params={"available": False})) == ["Beef Kebab"]
    assert names(client.get("/products", params={"min_price": 100})) == ["Goat Light Soup"]
    assert names(client.get("/products", params={"max_price": 15})) == ["Chicken Broth Shot", "Sobolo"]
    assert client.get("/products", params={"restaurant_id": world["restaurant"]["id"]}).json()["total"] == 6
    assert client.get("/products", params={"restaurant_id": 9999}).json()["total"] == 0
    assert client.get("/products", params={"category_id": world["category"]["id"]}).json()["total"] == 4


def test_product_price_range_validation(client):
    assert client.get("/products", params={"min_price": 50, "max_price": 10}).status_code == 422
    assert client.get("/products", params={"min_price": -1}).status_code == 422


def test_product_search_treats_wildcards_literally(client, catalog):
    assert client.get("/products", params={"search": "%"}).json()["total"] == 0


def test_product_sorting_and_pagination(client, catalog):
    r = client.get("/products", params={"sort_by": "base_price", "order": "asc", "limit": 3}).json()
    assert [float(i["base_price"]) for i in r["items"]] == [10.0, 15.0, 35.0]
    assert r["total"] == 6 and r["pages"] == 2
    p2 = client.get("/products", params={"sort_by": "base_price", "order": "asc", "limit": 3, "page": 2}).json()
    assert [float(i["base_price"]) for i in p2["items"]] == [40.0, 60.0, 120.0]


def test_product_detail_includes_variants(client, world):
    r = client.get(f"/products/{world['product']['id']}")
    assert r.status_code == 200
    assert [v["name"] for v in r.json()["variants"]] == ["Large"]
    assert client.get("/products/99999").status_code == 404


def test_product_validation(client, world):
    o, cat = world["owner"], world["category"]["id"]
    assert client.post("/products", headers=o.h, json={"category_id": cat, "name": "Free", "base_price": "0"}).status_code == 422
    assert client.post("/products", headers=o.h, json={"category_id": cat, "name": "X Y", "base_price": "1.999"}).status_code == 422
    assert client.post("/products", headers=o.h, json={"category_id": 999, "name": "Ghost", "base_price": "5"}).status_code == 404


def test_variant_cannot_make_price_negative(client, world):
    r = client.post("/product-variants", headers=world["owner"].h,
                    json={"product_id": world["product"]["id"], "name": "Cheap", "price_modifier": "-50"})
    assert r.status_code == 400


def test_duplicate_category_name_conflict(client, world):
    r = client.post("/categories", headers=world["owner"].h,
                    json={"menu_id": world["menu"]["id"], "name": "Meals"})
    assert r.status_code == 409


def test_menu_detail_nests_categories_and_hides_unavailable_from_public(client, world, catalog):
    menu = client.get(f"/menus/{world['menu']['id']}").json()
    assert {c["name"] for c in menu["categories"]} >= {"Meals", "Drinks"}
    names = lambda r: [p["name"] for p in r.json()["items"]]
    mid = world["menu"]["id"]
    assert "Beef Kebab" not in names(client.get("/products", params={"limit": 100, "available": True}))
    assert "Sobolo" in names(client.get("/products", params={"limit": 100}))
    assert "Beef Kebab" in names(client.get("/products", headers=world["owner"].h, params={"limit": 100}))
    assert mid


def test_inactive_restaurant_products_hidden(client, world):
    client.delete(f"/restaurants/{world['restaurant']['id']}", headers=world["owner"].h)
    assert client.get("/products").json()["total"] == 0
    assert client.get(f"/products/{world['product']['id']}").status_code == 404
    assert client.get("/products", headers=world["owner"].h).json()["total"] == 1


def test_delete_product_and_cascade_variants(client, world):
    pid = world["product"]["id"]
    assert client.delete(f"/products/{pid}", headers=world["owner"].h).status_code == 204
    assert client.get(f"/products/{pid}").status_code == 404
    assert client.get("/product-variants", params={"product_id": pid}).json()["total"] == 0


# ───────── cart ─────────
def add(client, w, **body):
    return client.post("/cart/items", headers=w["customer"].h, json=body)


def test_empty_cart(client, world):
    r = client.get("/cart", headers=world["customer"].h)
    assert r.status_code == 200 and r.json()["items"] == [] and float(r.json()["subtotal"]) == 0


def test_add_item_with_variant_prices(client, world):
    r = add(client, world, product_id=world["product"]["id"], variant_id=world["variant"]["id"], quantity=2)
    assert r.status_code == 201
    cart = r.json()
    assert cart["items"][0]["unit_price"] == "50.00" and cart["items"][0]["line_total"] == "100.00"
    assert float(cart["subtotal"]) == 100 and cart["items"][0]["quantity"] == 2
    assert cart["restaurant_id"] == world["restaurant"]["id"]


def test_adding_same_item_merges_quantity(client, world):
    pid = world["product"]["id"]
    add(client, world, product_id=pid, quantity=2)
    cart = add(client, world, product_id=pid, quantity=3).json()
    assert len(cart["items"]) == 1 and cart["items"][0]["quantity"] == 5
    add(client, world, product_id=pid, variant_id=world["variant"]["id"], quantity=1)
    assert len(client.get("/cart", headers=world["customer"].h).json()["items"]) == 2   # variant is a separate line


def test_quantity_limits(client, world):
    pid = world["product"]["id"]
    assert add(client, world, product_id=pid, quantity=0).status_code == 422
    assert add(client, world, product_id=pid, quantity=51).status_code == 422
    add(client, world, product_id=pid, quantity=50)
    assert add(client, world, product_id=pid, quantity=1).status_code == 400


def test_update_and_remove_item(client, world):
    pid = world["product"]["id"]
    item_id = add(client, world, product_id=pid, quantity=1).json()["items"][0]["id"]
    up = client.patch(f"/cart/items/{item_id}", headers=world["customer"].h, json={"quantity": 4, "notes": "no pepper"})
    assert up.status_code == 200 and up.json()["items"][0]["quantity"] == 4 and up.json()["subtotal"] == "160.00"
    assert client.patch(f"/cart/items/{item_id}", headers=world["customer"].h, json={"quantity": 0}).status_code == 422
    rm = client.delete(f"/cart/items/{item_id}", headers=world["customer"].h)
    assert rm.status_code == 200 and rm.json()["items"] == [] and rm.json()["restaurant_id"] is None
    assert client.delete(f"/cart/items/{item_id}", headers=world["customer"].h).status_code == 404


def test_cannot_touch_another_customers_cart_item(client, world, register):
    item_id = add(client, world, product_id=world["product"]["id"]).json()["items"][0]["id"]
    other = register("CUSTOMER")
    assert client.patch(f"/cart/items/{item_id}", headers=other.h, json={"quantity": 9}).status_code == 404
    assert client.delete(f"/cart/items/{item_id}", headers=other.h).status_code == 404


def test_cart_rejects_bad_products(client, world):
    w = world
    assert add(client, w, product_id=99999).status_code == 404
    assert add(client, w, product_id=w["product"]["id"], variant_id=99999).status_code == 404
    client.patch(f"/products/{w['product']['id']}", headers=w["owner"].h, json={"is_available": False})
    assert add(client, w, product_id=w["product"]["id"]).status_code == 409


def test_variant_must_belong_to_product(client, world):
    w = world
    other = client.post("/products", headers=w["owner"].h, json={
        "category_id": w["category"]["id"], "name": "Waakye", "base_price": "25"}).json()
    assert add(client, w, product_id=other["id"], variant_id=w["variant"]["id"]).status_code == 400


def test_cart_single_restaurant_rule(client, world, register):
    owner2 = register("RESTAURANT_OWNER")
    r2 = client.post("/restaurants", headers=owner2.h, json={"name": "Second Spot"}).json()
    m2 = client.post("/menus", headers=owner2.h, json={"restaurant_id": r2["id"], "name": "Menu Two"}).json()
    c2 = client.post("/categories", headers=owner2.h, json={"menu_id": m2["id"], "name": "Snacks"}).json()
    p2 = client.post("/products", headers=owner2.h, json={"category_id": c2["id"], "name": "Meat Pie", "base_price": "12"}).json()
    add(client, world, product_id=world["product"]["id"])
    r = add(client, world, product_id=p2["id"])
    assert r.status_code == 409 and "another restaurant" in r.json()["error"]["message"]
    assert client.delete("/cart", headers=world["customer"].h).json()["items"] == []
    assert add(client, world, product_id=p2["id"]).status_code == 201   # allowed after clearing
