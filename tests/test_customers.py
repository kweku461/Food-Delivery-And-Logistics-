def addr(**kw):
    return {"address_line": "Ayeduase Gate", "city": "Kumasi", **kw}


def test_profile_update_persists_and_is_private(client, register, admin):
    c, other = register("CUSTOMER"), register("CUSTOMER")
    client.patch(f"/customers/{c.id}/profile", headers=c.h,
                 json={"dietary_preferences": "vegetarian", "preferred_payment_method": "MOMO"})
    again = client.get(f"/customers/{c.id}/profile", headers=c.h).json()
    assert again["dietary_preferences"] == "vegetarian"
    assert again["preferred_payment_method"] == "MOMO"
    assert client.get(f"/customers/{c.id}/profile", headers=other.h).status_code == 403
    assert client.get(f"/customers/{c.id}/profile", headers=admin.h).status_code == 200


def test_admin_can_create_address_for_customer(client, register, admin):
    c = register("CUSTOMER")
    r = client.post(f"/customers/{c.id}/addresses", headers=admin.h, json=addr())
    assert r.status_code == 201 and r.json()["user_id"] == c.id


def test_delete_address_removes_it(client, register):
    c = register("CUSTOMER")
    a = client.post(f"/customers/{c.id}/addresses", headers=c.h, json=addr()).json()
    assert client.delete(f"/customers/{c.id}/addresses/{a['id']}", headers=c.h).status_code == 204
    assert client.get(f"/customers/{c.id}/addresses", headers=c.h).json()["total"] == 0
    assert client.delete(f"/customers/{c.id}/addresses/{a['id']}", headers=c.h).status_code == 404


def test_address_pagination_and_sorting(client, register):
    c = register("CUSTOMER")
    for city in ("C", "A", "B"):
        client.post(f"/customers/{c.id}/addresses", headers=c.h, json=addr(city=city))
    r = client.get(f"/customers/{c.id}/addresses?page=1&limit=2&sort_by=city&order=asc", headers=c.h).json()
    assert [i["city"] for i in r["items"]] == ["A", "B"]
    assert r["total"] == 3 and r["pages"] == 2
    assert client.get(f"/customers/{c.id}/addresses?sort_by=bogus", headers=c.h).status_code == 422


def test_patch_address_rejects_null_required_field(client, register):
    # needs the AddressUpdate validator in schemas/user.py
    c = register("CUSTOMER")
    a = client.post(f"/customers/{c.id}/addresses", headers=c.h, json=addr()).json()
    r = client.patch(f"/customers/{c.id}/addresses/{a['id']}", headers=c.h, json={"city": None})
    assert r.status_code == 422