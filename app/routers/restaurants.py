from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.geo import haversine_km
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.database import get_db
from app.deps import (
    assert_can_manage_restaurant, can_manage_restaurant, get_optional_user, require_roles,
)
from app.enums import Role
from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.models import Restaurant, RestaurantBranch, RestaurantStaff, User
from app.schemas.restaurant import (
    BranchCreate, BranchRead, BranchUpdate, RestaurantCreate, RestaurantFilter, RestaurantRead,
    RestaurantUpdate, StaffCreate, StaffRead, StaffUpdate,
)
from app.utils import get_or_404, like_pattern

router = APIRouter(tags=["Restaurants"])
_ILIKE = {"escape": "\\"}
restaurant_sort = sort_params(["created_at", "name", "id", "cuisine_type"])
branch_sort = sort_params(["created_at", "name", "city", "id"])
staff_sort = sort_params(["created_at", "position", "id"])


@router.post("/restaurants", response_model=RestaurantRead, status_code=status.HTTP_201_CREATED,
             summary="Create a restaurant (the caller becomes its owner)")
def create_restaurant(data: RestaurantCreate, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(Role.RESTAURANT_OWNER))):
    if db.scalar(select(Restaurant.id).where(Restaurant.name == data.name)):
        raise ConflictError("A restaurant with this name already exists")
    restaurant = Restaurant(owner_id=user.id, **data.model_dump())
    db.add(restaurant)
    db.commit()
    return restaurant


@router.get("/restaurants", response_model=Page[RestaurantRead],
            summary="Search, filter (location / active) and paginate restaurants")
def list_restaurants(
    filters: Annotated[RestaurantFilter, Query()],
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(restaurant_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stmt = select(Restaurant)
    is_admin = user is not None and user.role == Role.ADMIN
    if filters.is_active is False:
        if is_admin:
            stmt = stmt.where(Restaurant.is_active.is_(False))
        elif user is not None and user.role == Role.RESTAURANT_OWNER:
            stmt = stmt.where(Restaurant.is_active.is_(False), Restaurant.owner_id == user.id)
        else:
            raise AuthorizationError("Only admins and restaurant owners can list inactive restaurants")
    elif filters.is_active is True or not is_admin:
        stmt = stmt.where(Restaurant.is_active.is_(True))

    if filters.search:
        pat = like_pattern(filters.search)
        stmt = stmt.where(or_(Restaurant.name.ilike(pat, **_ILIKE),
                              Restaurant.cuisine_type.ilike(pat, **_ILIKE),
                              Restaurant.description.ilike(pat, **_ILIKE)))
    if filters.cuisine_type:
        stmt = stmt.where(Restaurant.cuisine_type.ilike(filters.cuisine_type))
    if filters.city:
        stmt = stmt.where(Restaurant.id.in_(
            select(RestaurantBranch.restaurant_id).where(
                RestaurantBranch.city.ilike(filters.city), RestaurantBranch.is_active.is_(True))))
    if filters.latitude is not None:
        distance = haversine_km(RestaurantBranch.latitude, RestaurantBranch.longitude,
                                filters.latitude, filters.longitude)
        stmt = stmt.where(Restaurant.id.in_(
            select(RestaurantBranch.restaurant_id).where(
                RestaurantBranch.is_active.is_(True), RestaurantBranch.latitude.is_not(None),
                RestaurantBranch.longitude.is_not(None), distance <= filters.radius_km)))
    return paginate(db, stmt, Restaurant, page, sort)


def _visible_restaurant(db: Session, restaurant_id: int, user: User | None) -> Restaurant:
    r = get_or_404(db, Restaurant, restaurant_id, "Restaurant")
    if not r.is_active and not (user and can_manage_restaurant(db, user, r.id)):
        raise NotFoundError("Restaurant not found")
    return r


@router.get("/restaurants/{restaurant_id}", response_model=RestaurantRead)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db),
                   user: User | None = Depends(get_optional_user)):
    return _visible_restaurant(db, restaurant_id, user)


@router.patch("/restaurants/{restaurant_id}", response_model=RestaurantRead)
def update_restaurant(restaurant_id: int, data: RestaurantUpdate, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    r = assert_can_manage_restaurant(db, user, restaurant_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != r.name and db.scalar(
            select(Restaurant.id).where(Restaurant.name == changes["name"])):
        raise ConflictError("A restaurant with this name already exists")
    for k, v in changes.items():
        setattr(r, k, v)
    db.commit()
    return r


@router.delete("/restaurants/{restaurant_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Deactivate a restaurant (soft delete)")
def deactivate_restaurant(restaurant_id: int, db: Session = Depends(get_db),
                          user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    r = assert_can_manage_restaurant(db, user, restaurant_id)
    r.is_active = False
    db.commit()


# ───────── branches ─────────
@router.post("/restaurants/{restaurant_id}/branches", response_model=BranchRead,
             status_code=status.HTTP_201_CREATED)
def create_branch(restaurant_id: int, data: BranchCreate, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    assert_can_manage_restaurant(db, user, restaurant_id)
    branch = RestaurantBranch(restaurant_id=restaurant_id, **data.model_dump())
    db.add(branch)
    db.commit()
    return branch


@router.get("/restaurants/{restaurant_id}/branches", response_model=Page[BranchRead])
def list_branches(restaurant_id: int, page: PageParams = Depends(page_params),
                  sort: SortParams = Depends(branch_sort), db: Session = Depends(get_db),
                  user: User | None = Depends(get_optional_user)):
    r = _visible_restaurant(db, restaurant_id, user)
    stmt = select(RestaurantBranch).where(RestaurantBranch.restaurant_id == r.id)
    if not (user and can_manage_restaurant(db, user, r.id, allow_staff=True)):
        stmt = stmt.where(RestaurantBranch.is_active.is_(True))
    return paginate(db, stmt, RestaurantBranch, page, sort)


@router.patch("/branches/{branch_id}", response_model=BranchRead)
def update_branch(branch_id: int, data: BranchUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    branch = get_or_404(db, RestaurantBranch, branch_id, "Branch")
    assert_can_manage_restaurant(db, user, branch.restaurant_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(branch, k, v)
    db.commit()
    return branch


# ───────── staff (User <-> Branch many-to-many) ─────────
@router.post("/branches/{branch_id}/staff", response_model=StaffRead, status_code=status.HTTP_201_CREATED,
             summary="Assign an existing STAFF user to a branch")
def add_staff(branch_id: int, data: StaffCreate, db: Session = Depends(get_db),
              user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    branch = get_or_404(db, RestaurantBranch, branch_id, "Branch")
    assert_can_manage_restaurant(db, user, branch.restaurant_id)
    staff_user = get_or_404(db, User, data.user_id, "User")
    if staff_user.role != Role.STAFF:
        raise ConflictError("Only users with the STAFF role can be assigned to a branch")
    if db.scalar(select(RestaurantStaff.id).where(
            RestaurantStaff.user_id == staff_user.id, RestaurantStaff.branch_id == branch.id)):
        raise ConflictError("User is already assigned to this branch")
    link = RestaurantStaff(user_id=staff_user.id, branch_id=branch.id, position=data.position)
    db.add(link)
    db.commit()
    return link


@router.get("/branches/{branch_id}/staff", response_model=Page[StaffRead])
def list_branch_staff(branch_id: int, page: PageParams = Depends(page_params),
                      sort: SortParams = Depends(staff_sort), db: Session = Depends(get_db),
                      user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    branch = get_or_404(db, RestaurantBranch, branch_id, "Branch")
    assert_can_manage_restaurant(db, user, branch.restaurant_id)
    stmt = select(RestaurantStaff).where(RestaurantStaff.branch_id == branch.id)
    return paginate(db, stmt, RestaurantStaff, page, sort)


@router.get("/restaurants/{restaurant_id}/staff", response_model=Page[StaffRead],
            summary="All staff across a restaurant's branches")
def list_staff(restaurant_id: int, page: PageParams = Depends(page_params),
               sort: SortParams = Depends(staff_sort), db: Session = Depends(get_db),
               user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    assert_can_manage_restaurant(db, user, restaurant_id)
    stmt = (select(RestaurantStaff).join(RestaurantBranch)
            .where(RestaurantBranch.restaurant_id == restaurant_id))
    return paginate(db, stmt, RestaurantStaff, page, sort)


def _staff_link(db: Session, user: User, staff_id: int) -> RestaurantStaff:
    link = get_or_404(db, RestaurantStaff, staff_id, "Staff assignment")
    assert_can_manage_restaurant(db, user, link.branch.restaurant_id)
    return link


@router.patch("/staff/{staff_id}", response_model=StaffRead)
def update_staff(staff_id: int, data: StaffUpdate, db: Session = Depends(get_db),
                 user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    link = _staff_link(db, user, staff_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(link, k, v)
    db.commit()
    return link


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_staff(staff_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER))):
    db.delete(_staff_link(db, user, staff_id))
    db.commit()
