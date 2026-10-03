from fastapi import FastAPI

from app.errors import register_error_handlers
from app.routers.auth import router as auth_router
from app.routers.catalog import router as catalog_router
from app.routers.restaurants import router as restaurants_router

app = FastAPI(title="Food Delivery and Logistics API")
app.include_router(auth_router)
app.include_router(restaurants_router)
app.include_router(catalog_router)
register_error_handlers(app)
