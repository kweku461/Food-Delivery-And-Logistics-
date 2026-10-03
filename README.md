# FoodFlow — Food Delivery & Logistics Platform API

Week 3 Zaptek brief (Backend Engineering with FastAPI, Team 2): a multi-entity e-commerce + logistics backend
inspired by the *kind* of system behind Flava Delivery, designed from business requirements rather than copied.

**20 entities · 74 endpoints · JWT + 5-role RBAC · order & delivery state machines · transactional checkout ·
170 automated tests**

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set SECRET_KEY for anything beyond local dev
uvicorn app.main:app --reload
```

* Swagger UI: http://127.0.0.1:8000/docs  (ReDoc: `/redoc`) — log in, click **Authorize**, paste the `access_token`.
* A seeded admin is created on first start: `admin@foodflow.com` / `Admin12345` (change via `.env`).
* SQLite by default. To use the team's shared PostgreSQL (Neon) database, create a local `.env` (it is git-ignored,
  never commit it) with `DATABASE_URL`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `FIRST_ADMIN_EMAIL` and
  `FIRST_ADMIN_PASSWORD`. `psycopg[binary]` and `psycopg2-binary` are in `requirements.txt`.
* Tests: `pytest` (in-memory SQLite, isolated per test, ~40 s).
* Postman: import `postman/FoodFlow.postman_collection.json` and run folders 1–9 in order with the Collection Runner.
  Tokens/ids are captured automatically. Regenerate with `python scripts/generate_postman.py`.
* ER diagram: `docs/er_diagram.png` (source `docs/er_diagram.mmd`, regenerate with `python scripts/generate_er.py`).

## Team and contributions

Team 2, Zaptek "Backend Engineering With FastAPI", Week 3. Work was split into phases; the final code on `main` is the
integrated version of all parts, and `pytest` runs every module together.

| Phase | Member | Part | Where it lives |
|---|---|---|---|
| 1 | Henry (Dela Kwabla Djirackor) | Database models, relationships, ER diagram; integration | `app/models.py`, `docs/er_diagram.*`, `app/main.py` |
| 1 | Nana Kweku (kweku461) | Pydantic schemas, pagination and sorting | `app/schemas/*`, `app/core/pagination.py`, `app/core/geo.py` |
| 1 | Mensah Justice | Auth, JWT, password hashing, RBAC | `app/security.py`, `app/deps.py`, `app/routers/auth.py` |
| 1 | Agyapong Emanuella | Centralized error handling; Swagger, Postman, README | `app/errors.py`, `postman/`, `README.md` |
| 2 | Bryan Owusu | Restaurants, branches, staff | `app/routers/restaurants.py` |
| 2 | Sanker Protus | Menus, categories, products, variants, search | `app/routers/catalog.py` |
| 2 | Joseph Boafo | Cart | `app/cart.py`, `app/routers/cart.py` |
| 2 | Nana Kweku (kweku461) | Customer profile and addresses | `app/routers/customers.py` |
| 3 | Donkor Owusu | Checkout transaction, orders, filters | `app/services/order_service.py`, `app/routers/orders.py` |
| 3 | Agnes | Order state machine, drivers, deliveries | `app/services/workflow.py`, `app/routers/logistics.py` |
| 3 | Mawutor Etornam Chris | Payments and refunds | `app/routers/payments.py`, `app/services/gateway.py` |

The cart (Joseph Boafo) and the catalog (Sanker Protus) are the members' own implementations, merged from their
branches. Payments and refunds (Chris Mawutor) are integrated into the checkout and order workflow (gateway charge,
refund approval by owner/admin, payment listing); his `feature/payments-refunds` branch is recorded in the history.

## Project layout

```
app/
  main.py            app factory, lifespan (create tables, seed admin), router wiring
  config.py          settings from env / .env
  database.py        engine, session, Base (SQLite foreign keys enforced)
  models.py          the 20 SQLAlchemy entities
  schemas/           Pydantic request/response models, one module per domain (user, restaurant, catalog, order, payment, delivery)
  core/              pagination.py (Page / PageParams / sort_params / paginate) and geo.py (haversine_km)
  security.py        bcrypt hashing, JWT create/decode
  deps.py            get_current_user, require_roles(), ownership helpers
  errors.py          exception hierarchy + centralised handlers
  services/
    workflow.py      order + delivery state machines and their synchronisation
    order_service.py checkout transaction (cart -> order)
    access.py        row-level visibility per role
    gateway.py       fake payment provider for demos/tests
  routers/           auth, customers, restaurants, catalog, cart, orders, payments, logistics
tests/               pytest + FastAPI TestClient
docs/                ER diagram        postman/   collection        scripts/   generators
```

## Shared contracts

Schemas (`app/schemas/*`), pagination (`app/core/pagination.py`) and geo (`app/core/geo.py`) are the team's shared
modules; routers import them directly. Query-parameter filters are Pydantic models used as `Annotated[Filter, Query()]`
(FastAPI allows one such model per endpoint, so extra filters are added by subclassing in `app/schemas/extra.py`).

## Data model (20 entities)

| Group | Entities |
|---|---|
| Customer & identity | `User`, `Address`, `CustomerProfile` |
| Restaurant | `Restaurant`, `RestaurantBranch`, `RestaurantStaff`, `Menu`, `Category`, `Product`, `ProductVariant` |
| Ordering | `Cart`, `CartItem`, `Order`, `OrderItem`, `OrderStatusHistory` |
| Payments | `Payment`, `Refund` |
| Delivery | `Driver`, `Delivery`, `DeliveryStatusHistory` |

![ER diagram](docs/er_diagram.png)

**Relationship types (all SQLAlchemy `relationship()`s with back-populates):**

* **One-to-one:** User–CustomerProfile, User–Driver, User–Cart, Order–Payment, Order–Delivery (enforced by `UNIQUE` FKs).
* **One-to-many:** User→Addresses/Orders, Restaurant→Branches/Menus, Menu→Categories→Products→Variants,
  Cart→CartItems, Order→OrderItems/StatusHistory, Payment→Refunds, Delivery→StatusHistory, Driver→Deliveries.
* **Many-to-many:** User ↔ RestaurantBranch through the `RestaurantStaff` association object
  (extra columns: `position`, `is_active`); `User.branches` exposes the direct many-to-many view.

**Design decisions worth knowing**

* `OrderItem` snapshots product/variant name and price, and `Order` snapshots the delivery address text, so later
  menu or address edits never rewrite history.
* `Product.restaurant_id` is deliberately denormalised (category → menu → restaurant) to keep search a single join.
* A cart holds items from one restaurant only; it resets when emptied.
* Money is `Numeric(10,2)` / `Decimal` end to end (SQLite stores it approximately — use PostgreSQL in production).
* Staff are scoped to **branches**, owners to **restaurants**, drivers to **their own deliveries**.
* Restaurants are soft-deleted (`is_active=false`) so past orders stay intact.

## Authentication & authorization

* `POST /auth/register` creates a `CUSTOMER` (bcrypt-hashed password); `POST /auth/login` returns a JWT (HS256, `sub`, `role`, `exp`, default 60 min).
* Protected routes use `HTTPBearer`; invalid, expired or forged tokens return `401`. The user is re-loaded from the
  database on every request, so the role claim is never trusted and deactivated users are locked out immediately.
* Public registration always creates a `CUSTOMER`. Other roles are created by an admin (`POST /users`); an owner then
  assigns a `STAFF` user to a branch (`POST /branches/{id}/staff`) and the admin creates the driver profile (`POST /drivers`).
  `ADMIN` is seeded and can change roles via `PATCH /users/{id}`.

### Permission matrix

| Capability | ADMIN | RESTAURANT_OWNER | STAFF | CUSTOMER | DRIVER |
|---|:-:|:-:|:-:|:-:|:-:|
| Browse restaurants / menus / products | ✅ | ✅ | ✅ | ✅ | ✅ (public) |
| See inactive restaurants | all | own | — | — | — |
| Create restaurant | ✅ (for an owner) | ✅ own | ❌ | ❌ | ❌ |
| Branches, menus, categories, products, variants (create / edit / delete) | ✅ | ✅ own | ❌ | ❌ | ❌ |
| Toggle product / variant availability | ✅ | ✅ own | ✅ own restaurant | ❌ | ❌ |
| Manage staff | ✅ | ✅ own | ❌ | ❌ | ❌ |
| Cart & checkout | ❌ | ❌ | ❌ | ✅ | ❌ |
| View orders | all | own restaurant | own branch | own | assigned only |
| Confirm / prepare / ready orders | ✅ | ✅ own | ✅ own branch | ❌ | ❌ |
| Cancel order | ✅ | ✅ own | ✅ own branch | own, only while `PENDING` | ❌ |
| Dispatch (`OUT_FOR_DELIVERY`, `DELIVERED`) | ✅ | ❌ | ❌ | ❌ | ✅ assigned |
| Pay for an order | ✅ | ❌ | ❌ | ✅ own | ❌ |
| Request refund | ✅ | ✅ own | ❌ | ✅ own | ❌ |
| Approve / reject refund | ✅ | ✅ own | ❌ | ❌ | ❌ |
| Assign driver | ✅ | ✅ own | ✅ own branch | ❌ | ❌ |
| Update delivery status | ✅ | ❌ | ❌ | ❌ | ✅ assigned |
| List drivers | ✅ | ✅ | ✅ | ❌ | self |
| Manage users | ✅ | ❌ | ❌ | ❌ | ❌ |

Unauthenticated → `401`; authenticated but not allowed → `403`.

## Order workflow (state machine)

```
PENDING → CONFIRMED → PREPARING → READY_FOR_PICKUP → OUT_FOR_DELIVERY → DELIVERED
   └─────────┴────────────┴──────────────┴──→ CANCELLED   (before dispatch)
```

* Any other jump (e.g. `PENDING → DELIVERED`) returns `409 INVALID_STATE_TRANSITION` with the `current` and
  `allowed` states in `details`. `DELIVERED` and `CANCELLED` are terminal.
* Every change writes an `OrderStatusHistory` row (who, from, to, note): `GET /orders/{id}/status-history`.
* Guards: card/MoMo orders cannot be `CONFIRMED` until paid (cash orders can); dispatch requires an assigned driver.
* **Delivery machine:** `UNASSIGNED → ASSIGNED → PICKED_UP → (IN_TRANSIT) → DELIVERED`, with
  `PICKED_UP/IN_TRANSIT → FAILED → ASSIGNED` for re-dispatch. Every change writes `DeliveryStatusHistory`.
* The two machines are linked: pickup moves the order to `OUT_FOR_DELIVERY`; delivery completes the order,
  marks cash payments `PAID`, and frees the driver. Cancelling an order cancels its delivery and frees the driver.

## Transaction management

`POST /orders` (`services/order_service.py`) runs as one unit of work:

`cart → validate items → calculate total → create Order → create OrderItems → clear cart → create Payment → create Delivery`

All validation problems are collected and returned together (`409` with a list in `details`). Any exception triggers
`rollback()`: no partial order is persisted and the cart is untouched so the customer can retry.
`test_checkout_is_atomic_on_failure` proves this by forcing the final step to fail.

## Search, filtering, pagination, sorting

Every list endpoint returns `{items, total, page, limit, pages}` and accepts
`?page=&limit=&sort_by=&order=asc|desc` (`limit` ≤ 100; `sort_by` is whitelisted per resource, else `422`).

* `GET /products?search=chicken&category=meals&min_price=20&max_price=100&available=true&page=1&limit=20`
  (also `category_id`, `restaurant_id`)
* `GET /restaurants?search=&cuisine_type=&city=&latitude=&longitude=&radius_km=&is_active=` (radius uses the haversine distance
  to any active branch)
* `GET /orders?status=&customer_id=&restaurant_id=&branch_id=&date_from=&date_to=` (scoped by role first)

## Error handling

All errors share one envelope, produced in `app/errors.py`:

```json
{"error": {"code": "INVALID_STATE_TRANSITION", "message": "Cannot move order from PENDING to DELIVERED",
           "details": {"current": "PENDING", "requested": "DELIVERED", "allowed": ["CANCELLED", "CONFIRMED"]}}}
```

| Code | HTTP | Raised for |
|---|---|---|
| `VALIDATION_ERROR` | 422 | bad body / params (details list field + message) |
| `AUTHENTICATION_FAILED` | 401 | missing, invalid or expired token; bad credentials |
| `FORBIDDEN` | 403 | wrong role or not your resource |
| `NOT_FOUND` | 404 | unknown id / route |
| `BAD_REQUEST` | 400 | business-rule violations (refund over balance, malformed business input…) |
| `CONFLICT` / `INTEGRITY_ERROR` | 409 | duplicates, unavailable items, DB constraint violations |
| `INVALID_STATE_TRANSITION` | 409 | illegal order/delivery transition |
| `PAYMENT_FAILED` | 402 | declined payment (the `FAILED` state is persisted before responding) |
| `DATABASE_ERROR` / `INTERNAL_ERROR` | 500 | unexpected failures — internals are logged, never leaked |

## Payments & refunds (simulated gateway)

* Checkout creates a `PENDING` payment for the order total. `POST /payments` charges it:
  body is `{order_id, method, provider_reference}`: `CARD` needs a reference (`tok_fail` simulates a decline); `MOMO` needs a phone number as reference (ending `0000` fails);
  `CASH` stays `PENDING` until the driver delivers. Failed payments can be retried; paid ones return `409`.
* Refunds: customer/owner/admin request (`PENDING`), owner/admin decide (`PROCESSED`/`REJECTED`). Pending plus processed
  refunds can never exceed the payment amount; the payment becomes `PARTIALLY_REFUNDED` then `REFUNDED`.

## Testing

`pytest` runs 170 tests with FastAPI `TestClient` against an isolated in-memory SQLite database per test.
Coverage: authentication (hashing, JWT tampering/expiry), role permissions, restaurant creation, product search,
cart operations, order creation (incl. atomic rollback), state transitions, payment records, driver assignment,
delivery status, database relationships, invalid requests, and pagination / filtering / sorting.

## Endpoint reference (74)

**Authentication**

| Method | Path | Summary |
|---|---|---|
| POST | `/auth/register` | Register a CUSTOMER account |
| POST | `/auth/login` | Log in and receive a JWT |
| GET | `/auth/me` | Current authenticated user |
| PATCH | `/auth/me` | Update my name / phone |
| POST | `/auth/change-password` | Change Password |
| POST | `/users` | Create a user with any role (OWNER, STAFF, DRIVER, ...) |
| GET | `/users` | List Users |
| GET | `/users/{user_id}` | Get User |
| PATCH | `/users/{user_id}` | Change role / activate / deactivate a user |

**Customers**

| Method | Path | Summary |
|---|---|---|
| GET | `/customers/{customer_id}/profile` | Get Profile |
| PATCH | `/customers/{customer_id}/profile` | Update Profile |
| GET | `/customers/{customer_id}/addresses` | List Addresses |
| POST | `/customers/{customer_id}/addresses` | Create Address |
| PATCH | `/customers/{customer_id}/addresses/{address_id}` | Update Address |
| DELETE | `/customers/{customer_id}/addresses/{address_id}` | Delete Address |

**Restaurants**

| Method | Path | Summary |
|---|---|---|
| POST | `/restaurants` | Create a restaurant (the caller becomes its owner) |
| GET | `/restaurants` | Search, filter (location / active) and paginate restaurants |
| GET | `/restaurants/{restaurant_id}` | Get Restaurant |
| PATCH | `/restaurants/{restaurant_id}` | Update Restaurant |
| DELETE | `/restaurants/{restaurant_id}` | Deactivate a restaurant (soft delete) |
| POST | `/restaurants/{restaurant_id}/branches` | Create Branch |
| GET | `/restaurants/{restaurant_id}/branches` | List Branches |
| PATCH | `/branches/{branch_id}` | Update Branch |
| POST | `/branches/{branch_id}/staff` | Assign an existing STAFF user to a branch |
| GET | `/branches/{branch_id}/staff` | List Branch Staff |
| GET | `/restaurants/{restaurant_id}/staff` | All staff across a restaurant's branches |
| PATCH | `/staff/{staff_id}` | Update Staff |
| DELETE | `/staff/{staff_id}` | Remove Staff |

**Catalog**

| Method | Path | Summary |
|---|---|---|
| POST | `/menus` | Create Menu |
| GET | `/restaurants/{restaurant_id}/menus` | List Menus |
| GET | `/menus/{menu_id}` | Get Menu |
| PATCH | `/menus/{menu_id}` | Update Menu |
| DELETE | `/menus/{menu_id}` | Deactivate Menu |
| POST | `/categories` | Create Category |
| GET | `/menus/{menu_id}/categories` | List Categories |
| PATCH | `/categories/{category_id}` | Update Category |
| DELETE | `/categories/{category_id}` | Delete Category |
| POST | `/products` | Create Product |
| GET | `/products` | List Products |
| GET | `/products/{product_id}` | Get Product |
| PATCH | `/products/{product_id}` | Update Product |
| DELETE | `/products/{product_id}` | Deactivate Product |
| POST | `/variants` | Create Variant |
| GET | `/products/{product_id}/variants` | List Variants |
| PATCH | `/variants/{variant_id}` | Update Variant |
| DELETE | `/variants/{variant_id}` | Delete Variant |

**Cart**

| Method | Path | Summary |
|---|---|---|
| GET | `/cart` | Get my current cart (an empty one is created on first view) |
| DELETE | `/cart` | Empty my cart |
| POST | `/cart/items` | Add an item to my cart (same product + variant are merged) |
| PATCH | `/cart/items/{item_id}` | Update quantity / notes of one of my cart items |
| DELETE | `/cart/items/{item_id}` | Remove one item from my cart |

**Orders**

| Method | Path | Summary |
|---|---|---|
| POST | `/orders` | Checkout: convert the cart into an order (single DB transaction) |
| GET | `/orders` | List orders visible to the caller |
| GET | `/orders/{order_id}` | Get Order |
| PATCH | `/orders/{order_id}/status` | Move an order through the state machine (role + transition checked) |
| GET | `/orders/{order_id}/status-history` | Order Status History |

**Payments & Refunds**

| Method | Path | Summary |
|---|---|---|
| POST | `/payments` | Pay for an order (simulated gateway) |
| GET | `/payments` | List Payments |
| GET | `/payments/{payment_id}` | Get Payment |
| POST | `/refunds` | Request a refund on a paid order |
| GET | `/refunds` | List Refunds |
| GET | `/refunds/{refund_id}` | Get Refund |
| PATCH | `/refunds/{refund_id}/status` | Approve (PROCESSED) or REJECT a pending refund — ADMIN / restaurant owner |

**Drivers & Deliveries**

| Method | Path | Summary |
|---|---|---|
| POST | `/drivers` | Create a driver profile for a DRIVER user (ADMIN) |
| GET | `/drivers` | List Drivers |
| GET | `/drivers/me` | My Driver Profile |
| GET | `/drivers/{driver_id}` | Get Driver |
| PATCH | `/drivers/{driver_id}` | Update a driver (self or ADMIN): vehicle, availability, GPS position |
| GET | `/deliveries` | List Deliveries |
| GET | `/deliveries/{delivery_id}` | Get Delivery |
| POST | `/deliveries/{delivery_id}/assign-driver` | Dispatch: assign an available driver (ADMIN / owner / branch staff) |
| PATCH | `/deliveries/{delivery_id}/status` | Driver progress update (assigned driver or ADMIN); syncs the order status |
| GET | `/deliveries/{delivery_id}/status-history` | Delivery Status History |

**Meta**

| Method | Path | Summary |
|---|---|---|
| GET | `/health` | Health |

## Known limitations / next steps

* Payment gateway is simulated; swap `services/gateway.py` for a real provider (Paystack, Hubpay…) and add webhooks.
* No Alembic migrations yet (`create_all` on start-up) — add them before any production deployment.
* No refresh tokens, rate limiting or email verification.
* No real-time driver tracking (drivers can PATCH their position; wire up WebSockets for live maps).
