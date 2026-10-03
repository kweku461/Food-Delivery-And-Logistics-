from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from app import models  # noqa: F401  (registers tables)
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.enums import Role
from app.errors import register_error_handlers
from app.models import User
from app.routers import auth, cart, catalog, customers, logistics, orders, payments, restaurants
from app.security import hash_password

DESCRIPTION = """
Multi-entity food delivery & logistics backend: customers, restaurants with branches and staff,
menus/products/variants, carts, orders with a state machine, payments & refunds, drivers and deliveries.

**Auth:** `POST /auth/login`, then click **Authorize** and paste the `access_token`.

**Roles:** `ADMIN`, `RESTAURANT_OWNER`, `STAFF`, `CUSTOMER`, `DRIVER` — each endpoint lists who may call it
(see the README permission matrix).

**Errors** always look like `{"error": {"code", "message", "details"}}`.
"""


def seed_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == settings.first_admin_email.lower())) is None:
            db.add(User(email=settings.first_admin_email.lower(), full_name="Platform Admin",
                        hashed_password=hash_password(settings.first_admin_password), role=Role.ADMIN))
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_admin()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    register_error_handlers(app)
    for r in (auth.router, customers.router, restaurants.router, catalog.router, cart.router,
              orders.router, payments.router, logistics.router):
        app.include_router(r)

    @app.get("/health", tags=["Meta"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
