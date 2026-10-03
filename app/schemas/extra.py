"""Schemas that are not part of the core schema set (kept separate so they never clash with it)."""
from pydantic import BaseModel, Field

from app.enums import Role
from app.schemas.user import UserUpdate


class AdminUserUpdate(UserUpdate):
    role: Role | None = None


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


from app.schemas.delivery import DeliveryFilter  # noqa: E402
from app.schemas.order import OrderFilter  # noqa: E402


# FastAPI only allows ONE query-parameter model per endpoint, so extra filters
# are added by extending Nana's filter models rather than as loose parameters.
class OrderListFilter(OrderFilter):
    restaurant_id: int | None = None
    branch_id: int | None = None


class DeliveryListFilter(DeliveryFilter):
    order_id: int | None = None
