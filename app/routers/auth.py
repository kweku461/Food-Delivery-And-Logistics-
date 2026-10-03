from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.enums import Role
from app.errors import AuthenticationError, AuthorizationError, ConflictError
from app.models import CustomerProfile, User
from app.schemas.extra import AdminUserUpdate, ChangePassword
from app.schemas.user import LoginRequest, Token, UserAdminCreate, UserRead, UserRegister, UserUpdate
from app.security import create_access_token, hash_password, verify_password
from app.utils import get_or_404, like_pattern

router = APIRouter(tags=["Authentication"])
user_sort = sort_params(["created_at", "email", "full_name", "id"])


def _create_user(db: Session, data: UserRegister, role: Role) -> User:
    if db.scalar(select(User).where(User.email == data.email)):
        raise ConflictError("Email is already registered")
    user = User(email=data.email, hashed_password=hash_password(data.password),
                full_name=data.full_name, phone=data.phone, role=role)
    db.add(user)
    db.flush()
    if role == Role.CUSTOMER:
        db.add(CustomerProfile(user_id=user.id))
    db.commit()
    return user


@router.post("/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             summary="Register a CUSTOMER account")
def register(data: UserRegister, db: Session = Depends(get_db)):
    return _create_user(db, data, Role.CUSTOMER)


@router.post("/auth/login", response_model=Token, summary="Log in and receive a JWT")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email))
    if user is None or not verify_password(data.password, user.hashed_password):
        raise AuthenticationError("Incorrect email or password")
    if not user.is_active:
        raise AuthorizationError("This account has been deactivated")
    return Token(access_token=create_access_token(user.id, user.role.value))


@router.get("/auth/me", response_model=UserRead, summary="Current authenticated user")
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/auth/me", response_model=UserRead, summary="Update my name / phone")
def update_me(data: UserUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    changes = data.model_dump(exclude_unset=True)
    if "is_active" in changes:
        raise AuthorizationError("You cannot change your own active status")
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    return user


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(data: ChangePassword, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(data.current_password, user.hashed_password):
        raise AuthenticationError("Current password is incorrect")
    user.hashed_password = hash_password(data.new_password)
    db.commit()


# ───────── admin user management ─────────
@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED, tags=["Users (admin)"],
             summary="Create a user with any role (OWNER, STAFF, DRIVER, ...)")
def admin_create_user(data: UserAdminCreate, db: Session = Depends(get_db),
                      _: User = Depends(require_roles(Role.ADMIN))):
    return _create_user(db, data, data.role)


@router.get("/users", response_model=Page[UserRead], tags=["Users (admin)"])
def list_users(
    role: Role | None = None, is_active: bool | None = None, search: str | None = None,
    page: PageParams = Depends(page_params), sort: SortParams = Depends(user_sort),
    db: Session = Depends(get_db), _: User = Depends(require_roles(Role.ADMIN)),
):
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if search:
        pat = like_pattern(search)
        stmt = stmt.where(User.full_name.ilike(pat, escape="\\") | User.email.ilike(pat, escape="\\"))
    return paginate(db, stmt, User, page, sort)


@router.get("/users/{user_id}", response_model=UserRead, tags=["Users (admin)"])
def get_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(Role.ADMIN))):
    return get_or_404(db, User, user_id, "User")


@router.patch("/users/{user_id}", response_model=UserRead, tags=["Users (admin)"],
              summary="Change role / activate / deactivate a user")
def admin_update_user(user_id: int, data: AdminUserUpdate, db: Session = Depends(get_db),
                      admin: User = Depends(require_roles(Role.ADMIN))):
    user = get_or_404(db, User, user_id, "User")
    changes = data.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("is_active") is False or
                                ("role" in changes and changes["role"] != Role.ADMIN)):
        raise ConflictError("Admins cannot demote or deactivate themselves")
    for k, v in changes.items():
        setattr(user, k, v)
    if user.role == Role.CUSTOMER and user.profile is None:
        db.add(CustomerProfile(user_id=user.id))
    db.commit()
    return user
