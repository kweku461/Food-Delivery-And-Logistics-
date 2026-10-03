"""Builds postman/FoodFlow.postman_collection.json — run the folders top to bottom for a full demo."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PW = "Password123"


def item(name, method, path, token=None, body=None, save=None, expect=None, query=None):
    """save: {var: 'json.path.expression'} stored as collection variables by a test script."""
    headers = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    if token:
        headers.append({"key": "Authorization", "value": "Bearer {{%s}}" % token})
    url = {"raw": "{{base_url}}" + path, "host": ["{{base_url}}"], "path": [p for p in path.strip("/").split("/") if p]}
    if query:
        url["query"] = [{"key": k, "value": str(v)} for k, v in query.items()]
        url["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    lines = []
    if expect:
        lines.append(f'pm.test("status is {expect}", () => pm.response.to.have.status({expect}));')
    if save:
        lines.append("const j = pm.response.json();")
        for var, expr in save.items():
            lines.append(f'pm.collectionVariables.set("{var}", j.{expr});')
    req = {"method": method, "header": headers, "url": url}
    if body is not None:
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    out = {"name": name, "request": req}
    if lines:
        out["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]
    return out


def folder(name, items):
    return {"name": name, "item": items}


def order_status(n, who, status, expect=200):
    return item(f"{n}. Order -> {status} ({who})", "PATCH", "/orders/{{order_id}}/status", f"{who}_token",
                {"status": status}, expect=expect)


collection = {
    "info": {
        "name": "FoodFlow Delivery API",
        "description": "Run folders 1-9 in order (Collection Runner) for a complete end-to-end demo. "
                       "Tokens and ids are stored as collection variables automatically. "
                       "Start the API first: uvicorn app.main:app --reload",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "variable": [{"key": "base_url", "value": "http://127.0.0.1:8000"}] + [
        {"key": k, "value": ""} for k in (
            "admin_token", "owner_token", "staff_token", "customer_token", "driver_token",
            "owner_id", "customer_id", "driver_user_id", "staff_user_id", "driver_id", "restaurant_id", "branch_id",
            "menu_id", "category_id", "product_id", "variant_id", "address_id", "cart_item_id",
            "order_id", "delivery_id", "payment_id", "refund_id")],
    "item": [
        folder("1. Authentication", [
            item("Login seeded ADMIN", "POST", "/auth/login", body={"email": "admin@foodflow.com", "password": "Admin12345"},
                 save={"admin_token": "access_token"}, expect=200),
            item("Admin: create RESTAURANT_OWNER", "POST", "/users", "admin_token",
                 body={"email": "owner.demo@foodflow.com", "password": PW, "full_name": "Ama Owner", "role": "RESTAURANT_OWNER"},
                 save={"owner_id": "id"}),
            item("Login owner", "POST", "/auth/login",
                 body={"email": "owner.demo@foodflow.com", "password": PW}, save={"owner_token": "access_token"}),
            item("Register CUSTOMER (public)", "POST", "/auth/register",
                 body={"email": "customer.demo@foodflow.com", "password": PW, "full_name": "Kofi Customer"},
                 save={"customer_id": "id"}, expect=201),
            item("Login customer", "POST", "/auth/login",
                 body={"email": "customer.demo@foodflow.com", "password": PW}, save={"customer_token": "access_token"}),
            item("Admin: create DRIVER user", "POST", "/users", "admin_token",
                 body={"email": "driver.demo@foodflow.com", "password": PW, "full_name": "Yaw Driver", "role": "DRIVER"},
                 save={"driver_user_id": "id"}),
            item("Admin: create driver profile", "POST", "/drivers", "admin_token",
                 body={"user_id": "{{driver_user_id}}", "vehicle_type": "Motorbike", "plate_number": "GR-1234-26"}, expect=201),
            item("Login driver", "POST", "/auth/login",
                 body={"email": "driver.demo@foodflow.com", "password": PW}, save={"driver_token": "access_token"}),
            item("Admin: create STAFF user", "POST", "/users", "admin_token",
                 body={"email": "staff.demo@foodflow.com", "password": PW, "full_name": "Efua Staff", "role": "STAFF"},
                 save={"staff_user_id": "id"}),
            item("Login staff", "POST", "/auth/login", body={"email": "staff.demo@foodflow.com", "password": PW},
                 save={"staff_token": "access_token"}),
            item("Get current user", "GET", "/auth/me", "customer_token", expect=200),
            item("Invalid login (401)", "POST", "/auth/login", body={"email": "customer.demo@foodflow.com", "password": "wrong-password"}, expect=401),
            item("Admin: list users", "GET", "/users", "admin_token", query={"role": "DRIVER", "limit": 10}),
        ]),
        folder("2. Restaurant setup (owner)", [
            item("Create restaurant", "POST", "/restaurants", "owner_token",
                 {"name": "Mama's Kitchen", "cuisine_type": "Ghanaian", "description": "Jollof, waakye and more"},
                 save={"restaurant_id": "id"}),
            item("Create branch", "POST", "/restaurants/{{restaurant_id}}/branches", "owner_token",
                 {"name": "Kumasi Main", "address_line": "Adum High Street", "city": "Kumasi",
                  "latitude": 6.6885, "longitude": -1.6244, "delivery_fee": "5.00"}, save={"branch_id": "id"}),
            item("Assign STAFF to branch", "POST", "/branches/{{branch_id}}/staff", "owner_token",
                 {"user_id": "{{staff_user_id}}"}, expect=201),
            item("List branch staff", "GET", "/branches/{{branch_id}}/staff", "owner_token"),
            item("Search restaurants (location + text)", "GET", "/restaurants",
                 query={"search": "kitchen", "city": "Kumasi", "latitude": 6.69, "longitude": -1.62, "radius_km": 10, "is_active": "true", "page": 1, "limit": 10}),
            item("Get restaurant", "GET", "/restaurants/{{restaurant_id}}"),
            item("List branches", "GET", "/restaurants/{{restaurant_id}}/branches"),
        ]),
        folder("3. Menu & products", [
            item("Create menu", "POST", "/menus", "owner_token", {"restaurant_id": "{{restaurant_id}}", "name": "Main Menu"}, save={"menu_id": "id"}),
            item("Create category", "POST", "/categories", "owner_token", {"menu_id": "{{menu_id}}", "name": "Meals"}, save={"category_id": "id"}),
            item("Create product", "POST", "/products", "owner_token",
                 {"category_id": "{{category_id}}", "name": "Chicken Jollof", "description": "Spicy chicken jollof", "base_price": "40.00"},
                 save={"product_id": "id"}),
            item("Create product variant", "POST", "/product-variants", "owner_token",
                 {"product_id": "{{product_id}}", "name": "Large", "price_modifier": "10.00"}, save={"variant_id": "id"}),
            item("Advanced product search", "GET", "/products",
                 query={"search": "chicken", "category": "meals", "min_price": 20, "max_price": 100, "available": "true",
                        "page": 1, "limit": 20, "sort_by": "base_price", "order": "asc"}),
            item("Product details (with variants)", "GET", "/products/{{product_id}}"),
            item("Menu with categories & products", "GET", "/menus/{{menu_id}}"),
            item("STAFF toggles availability", "PATCH", "/products/{{product_id}}", "staff_token", {"is_available": True}, expect=200),
            item("CUSTOMER cannot create product (403)", "POST", "/products", "customer_token",
                 {"category_id": "{{category_id}}", "name": "Hack", "base_price": "1"}, expect=403),
        ]),
        folder("4. Customer, address & cart", [
            item("Create address", "POST", "/customers/{{customer_id}}/addresses", "customer_token",
                 {"label": "Hostel", "address_line": "Unity Hall, KNUST", "city": "Kumasi", "landmark": "Main gate", "is_default": True},
                 save={"address_id": "id"}),
            item("Update profile", "PATCH", "/customers/{{customer_id}}/profile", "customer_token",
                 {"preferred_payment_method": "CARD", "dietary_preferences": "no pork"}),
            item("Add item to cart", "POST", "/cart/items", "customer_token",
                 {"product_id": "{{product_id}}", "variant_id": "{{variant_id}}", "quantity": 2},
                 save={"cart_item_id": "items[0].id"}, expect=201),
            item("Update cart item", "PATCH", "/cart/items/{{cart_item_id}}", "customer_token", {"quantity": 3, "notes": "Extra pepper"}),
            item("Get cart", "GET", "/cart", "customer_token"),
        ]),
        folder("5. Checkout (transaction)", [
            item("Create order from cart", "POST", "/orders", "customer_token",
                 {"delivery_address_id": "{{address_id}}", "branch_id": "{{branch_id}}", "payment_method": "CARD", "notes": "Call on arrival"},
                 save={"order_id": "id", "delivery_id": "delivery.id", "payment_id": "payment.id"}, expect=201),
            item("Cart is now empty", "GET", "/cart", "customer_token"),
            item("List my orders (filters)", "GET", "/orders", "customer_token",
                 query={"status": "PENDING", "page": 1, "limit": 20, "sort_by": "created_at", "order": "desc"}),
            item("Get order", "GET", "/orders/{{order_id}}", "customer_token"),
        ]),
        folder("6. Payment", [
            item("Declined card -> 402", "POST", "/payments", "customer_token", {"order_id": "{{order_id}}", "method": "CARD", "provider_reference": "tok_fail"}, expect=402),
            item("Successful card payment", "POST", "/payments", "customer_token", {"order_id": "{{order_id}}", "method": "CARD", "provider_reference": "tok_visa"}, expect=201),
            item("Get payment", "GET", "/payments/{{payment_id}}", "customer_token"),
            item("Pay again -> 409", "POST", "/payments", "customer_token", {"order_id": "{{order_id}}", "method": "CARD", "provider_reference": "tok_visa"}, expect=409),
        ]),
        folder("7. Order workflow & delivery", [
            item("Invalid jump PENDING -> DELIVERED (409)", "PATCH", "/orders/{{order_id}}/status", "admin_token", {"status": "DELIVERED"}, expect=409),
            order_status(1, "owner", "CONFIRMED"),
            order_status(2, "staff", "PREPARING"),
            order_status(3, "staff", "READY_FOR_PICKUP"),
            item("List drivers (available)", "GET", "/drivers", "staff_token", query={"is_available": "true"}),
            item("Driver profile", "GET", "/drivers/me", "driver_token", save={"driver_id": "id"}),
            item("Assign driver", "POST", "/deliveries/{{delivery_id}}/assign-driver", "staff_token", {"driver_id": "{{driver_id}}"}, expect=200),
            item("Driver: PICKED_UP", "PATCH", "/deliveries/{{delivery_id}}/status", "driver_token", {"status": "PICKED_UP"}, expect=200),
            item("Driver: IN_TRANSIT", "PATCH", "/deliveries/{{delivery_id}}/status", "driver_token", {"status": "IN_TRANSIT"}, expect=200),
            item("Driver: DELIVERED", "PATCH", "/deliveries/{{delivery_id}}/status", "driver_token", {"status": "DELIVERED", "note": "Handed to customer"}, expect=200),
            item("Order status history", "GET", "/orders/{{order_id}}/status-history", "customer_token"),
            item("Delivery status history", "GET", "/deliveries/{{delivery_id}}/status-history", "customer_token"),
        ]),
        folder("8. Refund", [
            item("Customer requests refund", "POST", "/refunds", "customer_token",
                 {"payment_id": "{{payment_id}}", "amount": "20.00", "reason": "Food arrived cold"}, save={"refund_id": "id"}, expect=201),
            item("Owner approves refund", "PATCH", "/refunds/{{refund_id}}/status", "owner_token", {"status": "PROCESSED"}, expect=200),
            item("Get refund", "GET", "/refunds/{{refund_id}}", "customer_token"),
            item("Payment now PARTIALLY_REFUNDED", "GET", "/payments/{{payment_id}}", "customer_token"),
        ]),
        folder("9. Admin & error checks", [
            item("Admin: all orders", "GET", "/orders", "admin_token", query={"date_from": "2026-01-01", "limit": 50}),
            item("Validation error (422)", "POST", "/auth/register", body={"email": "bad", "password": "x"}, expect=422),
            item("No token (401)", "GET", "/auth/me", expect=401),
            item("Not found (404)", "GET", "/products/999999", expect=404),
            item("Health", "GET", "/health", expect=200),
        ]),
    ],
}

if __name__ == "__main__":
    out = ROOT / "postman" / "FoodFlow.postman_collection.json"
    out.write_text(json.dumps(collection, indent=2))
    n = sum(len(f["item"]) for f in collection["item"])
    print(f"wrote {out} ({n} requests)")
