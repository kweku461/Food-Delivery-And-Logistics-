"""Auth dependencies and access-control helpers (RBAC)."""
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.enums import Role
from app.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.models import Restaurant, RestaurantBranch, RestaurantStaff, User
from app.security import decode_access_token

bearer = HTTPBearer(auto_error=False, description="Paste the access_token from /auth/login")


def _user_from_credentials(creds: HTTPAuthorizationCredentials | None, db: Session) -> User:
    if creds is None:
        raise AuthenticationError("Not authenticated")
    payload = decode_access_token(creds.credentials)
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or deactivated")
    return user


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    return _user_from_credentials(creds, db)


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User | None:
    return _user_from_credentials(creds, db) if creds else None


def require_roles(*roles: Role):
    """Dependency factory: `Depends(require_roles(Role.ADMIN, Role.CUSTOMER))`."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AuthorizationError(
                f"Role {user.role.value} is not allowed to perform this action",
                {"required": [r.value for r in roles]},
            )
        return user

    return checker


# ───────── ownership helpers ─────────
def staff_branch_ids(db: Session, user: User) -> list[int]:
    return list(
        db.scalars(
            select(RestaurantStaff.branch_id).where(
                RestaurantStaff.user_id == user.id, RestaurantStaff.is_active.is_(True)
            )
        )
    )


def staff_restaurant_ids(db: Session, user: User) -> list[int]:
    return list(
        db.scalars(
            select(RestaurantBranch.restaurant_id)
            .join(RestaurantStaff, RestaurantStaff.branch_id == RestaurantBranch.id)
            .where(RestaurantStaff.user_id == user.id, RestaurantStaff.is_active.is_(True))
            .distinct()
        )
    )


def can_manage_restaurant(db: Session, user: User, restaurant_id: int, *, allow_staff: bool = False) -> bool:
    if user.role == Role.ADMIN:
        return True
    r = db.get(Restaurant, restaurant_id)
    if r is None:
        return False
    if user.role == Role.RESTAURANT_OWNER and r.owner_id == user.id:
        return True
    if allow_staff and user.role == Role.STAFF:
        return restaurant_id in staff_restaurant_ids(db, user)
    return False


def assert_can_manage_restaurant(db: Session, user: User, restaurant_id: int, *, allow_staff: bool = False) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found")
    if not can_manage_restaurant(db, user, restaurant_id, allow_staff=allow_staff):
        raise AuthorizationError("You do not have access to this restaurant")
    return restaurant
