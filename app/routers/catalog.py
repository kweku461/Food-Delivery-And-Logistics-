from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.pagination import (
    Page,
    PageParams,
    SortParams,
    page_params,
    paginate,
    sort_params,
)
from app.database import get_db
from app.deps import (
    assert_can_manage_restaurant,
    can_manage_restaurant,
    get_optional_user,
    require_roles,
)
from app.enums import Role
from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.models import Category, Menu, Product, ProductVariant, Restaurant, User
from app.schemas.catalog import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    MenuCreate,
    MenuDetail,
    MenuRead,
    MenuUpdate,
    ProductCreate,
    ProductDetail,
    ProductFilter,
    ProductRead,
    ProductUpdate,
    VariantCreate,
    VariantRead,
    VariantUpdate,
)
from app.utils import get_or_404, like_pattern

router = APIRouter(tags=["Catalog"])
_ILIKE = {"escape": "\\"}
menu_sort = sort_params(["created_at", "name", "id"])
category_sort = sort_params(
    ["sort_order", "created_at", "name", "id"], default="sort_order"
)
product_sort = sort_params(
    ["created_at", "name", "base_price", "prep_time_minutes", "id"]
)
variant_sort = sort_params(["created_at", "name", "price_modifier", "id"])


def _menu(db: Session, menu_id: int) -> Menu:
    return get_or_404(db, Menu, menu_id, "Menu")


def _category(db: Session, category_id: int) -> Category:
    return get_or_404(db, Category, category_id, "Category")


def _product(db: Session, product_id: int) -> Product:
    return get_or_404(db, Product, product_id, "Product")


def _variant(db: Session, variant_id: int) -> ProductVariant:
    return get_or_404(db, ProductVariant, variant_id, "Variant")


def _can_manage(
    db: Session, user: User, restaurant_id: int, *, allow_staff: bool = False
) -> None:
    assert_can_manage_restaurant(db, user, restaurant_id, allow_staff=allow_staff)


def _public_menu(menu: Menu, user: User | None, db: Session) -> Menu:
    if menu.is_active and menu.restaurant.is_active:
        return menu
    if user and can_manage_restaurant(db, user, menu.restaurant_id):
        return menu
    raise NotFoundError("Menu not found")


@router.post("/menus", response_model=MenuRead, status_code=status.HTTP_201_CREATED)
def create_menu(
    data: MenuCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    _can_manage(db, user, data.restaurant_id)
    menu = Menu(**data.model_dump())
    db.add(menu)
    db.commit()
    return menu


@router.get("/restaurants/{restaurant_id}/menus", response_model=Page[MenuRead])
def list_menus(
    restaurant_id: int,
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(menu_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    restaurant = get_or_404(db, Restaurant, restaurant_id, "Restaurant")
    managing = user is not None and can_manage_restaurant(db, user, restaurant.id)
    if not restaurant.is_active and not managing:
        raise NotFoundError("Restaurant not found")
    stmt = select(Menu).where(Menu.restaurant_id == restaurant.id)
    if not managing:
        stmt = stmt.where(Menu.is_active.is_(True))
    return paginate(db, stmt, Menu, page, sort)


@router.get("/menus/{menu_id}", response_model=MenuDetail)
def get_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    return _public_menu(_menu(db, menu_id), user, db)


@router.patch("/menus/{menu_id}", response_model=MenuRead)
def update_menu(
    menu_id: int,
    data: MenuUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    menu = _menu(db, menu_id)
    _can_manage(db, user, menu.restaurant_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(menu, key, value)
    db.commit()
    return menu


@router.delete("/menus/{menu_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    menu = _menu(db, menu_id)
    _can_manage(db, user, menu.restaurant_id)
    menu.is_active = False
    db.commit()


@router.post(
    "/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED
)
def create_category(
    data: CategoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    menu = _menu(db, data.menu_id)
    _can_manage(db, user, menu.restaurant_id)
    if db.scalar(
        select(Category.id).where(
            Category.menu_id == menu.id, Category.name == data.name
        )
    ):
        raise ConflictError("A category with this name already exists in the menu")
    category = Category(**data.model_dump())
    db.add(category)
    db.commit()
    return category


@router.get("/menus/{menu_id}/categories", response_model=Page[CategoryRead])
def list_categories(
    menu_id: int,
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(category_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    menu = _public_menu(_menu(db, menu_id), user, db)
    stmt = select(Category).where(Category.menu_id == menu.id)
    return paginate(db, stmt, Category, page, sort)


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: int,
    data: CategoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    category = _category(db, category_id)
    _can_manage(db, user, category.menu.restaurant_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and db.scalar(
        select(Category.id).where(
            Category.menu_id == category.menu_id,
            Category.name == changes["name"],
            Category.id != category.id,
        )
    ):
        raise ConflictError("A category with this name already exists in the menu")
    for key, value in changes.items():
        setattr(category, key, value)
    db.commit()
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    category = _category(db, category_id)
    _can_manage(db, user, category.menu.restaurant_id)
    db.delete(category)
    db.commit()


@router.post(
    "/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED
)
def create_product(
    data: ProductCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    category = _category(db, data.category_id)
    _can_manage(db, user, category.menu.restaurant_id)
    product = Product(restaurant_id=category.menu.restaurant_id, **data.model_dump())
    db.add(product)
    db.commit()
    return product


@router.get("/products", response_model=Page[ProductRead])
def list_products(
    filters: Annotated[ProductFilter, Query()],
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(product_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stmt = select(Product).join(Category).join(Menu).join(Restaurant)
    is_admin = user is not None and user.role == Role.ADMIN
    managed_restaurants = set()
    if is_admin:
        managed_restaurants = set(db.scalars(select(Restaurant.id)))
    elif user is not None and user.role == Role.RESTAURANT_OWNER:
        managed_restaurants = set(
            db.scalars(select(Restaurant.id).where(Restaurant.owner_id == user.id))
        )
    if filters.restaurant_id is not None:
        stmt = stmt.where(Product.restaurant_id == filters.restaurant_id)
    if filters.category_id is not None:
        stmt = stmt.where(Product.category_id == filters.category_id)
    if filters.category:
        stmt = stmt.where(Category.name.ilike(like_pattern(filters.category), **_ILIKE))
    if filters.search:
        pat = like_pattern(filters.search)
        stmt = stmt.where(
            or_(
                Product.name.ilike(pat, **_ILIKE),
                Product.description.ilike(pat, **_ILIKE),
                Category.name.ilike(pat, **_ILIKE),
                Restaurant.name.ilike(pat, **_ILIKE),
            )
        )
    if filters.min_price is not None:
        stmt = stmt.where(Product.base_price >= filters.min_price)
    if filters.max_price is not None:
        stmt = stmt.where(Product.base_price <= filters.max_price)
    if filters.available is not None:
        stmt = stmt.where(Product.is_available.is_(filters.available))
    elif not managed_restaurants:
        stmt = stmt.where(Product.is_available.is_(True))
    if not managed_restaurants:
        stmt = stmt.where(
            Restaurant.is_active.is_(True),
            Menu.is_active.is_(True),
            Product.is_available.is_(True),
        )
    elif not is_admin:
        stmt = stmt.where(
            or_(
                Product.restaurant_id.in_(managed_restaurants),
                Restaurant.is_active.is_(True),
                Menu.is_active.is_(True),
                Product.is_available.is_(True),
            )
        )
    return paginate(db, stmt, Product, page, sort)


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    product = _product(db, product_id)
    managing = user is not None and can_manage_restaurant(
        db, user, product.restaurant_id
    )
    if (
        not product.is_available
        or not product.category.menu.is_active
        or not product.category.menu.restaurant.is_active
    ) and not managing:
        raise NotFoundError("Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    data: ProductUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER, Role.STAFF)),
):
    product = _product(db, product_id)
    allow_staff = user.role == Role.STAFF
    _can_manage(db, user, product.restaurant_id, allow_staff=allow_staff)
    changes = data.model_dump(exclude_unset=True)
    if allow_staff and set(changes) != {"is_available"}:
        raise AuthorizationError("Staff may only change product availability")
    if "category_id" in changes:
        category = _category(db, changes["category_id"])
        _can_manage(db, user, category.menu.restaurant_id, allow_staff=allow_staff)
        product.restaurant_id = category.menu.restaurant_id
    for key, value in changes.items():
        if key != "category_id":
            setattr(product, key, value)
    db.commit()
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    product = _product(db, product_id)
    _can_manage(db, user, product.restaurant_id)
    product.is_available = False
    db.commit()


@router.post(
    "/variants", response_model=VariantRead, status_code=status.HTTP_201_CREATED
)
def create_variant(
    data: VariantCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    product = _product(db, data.product_id)
    _can_manage(db, user, product.restaurant_id)
    variant = ProductVariant(**data.model_dump())
    db.add(variant)
    db.commit()
    return variant


@router.get("/products/{product_id}/variants", response_model=Page[VariantRead])
def list_variants(
    product_id: int,
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(variant_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    product = _product(db, product_id)
    managing = user is not None and can_manage_restaurant(
        db, user, product.restaurant_id
    )
    if not managing and (
        not product.is_available
        or not product.category.menu.is_active
        or not product.category.menu.restaurant.is_active
    ):
        raise NotFoundError("Product not found")
    stmt = select(ProductVariant).where(ProductVariant.product_id == product.id)
    if not managing:
        stmt = stmt.where(ProductVariant.is_available.is_(True))
    return paginate(db, stmt, ProductVariant, page, sort)


@router.patch("/variants/{variant_id}", response_model=VariantRead)
def update_variant(
    variant_id: int,
    data: VariantUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER, Role.STAFF)),
):
    variant = _variant(db, variant_id)
    allow_staff = user.role == Role.STAFF
    _can_manage(db, user, variant.product.restaurant_id, allow_staff=allow_staff)
    changes = data.model_dump(exclude_unset=True)
    if allow_staff and set(changes) != {"is_available"}:
        raise AuthorizationError("Staff may only change variant availability")
    for key, value in changes.items():
        setattr(variant, key, value)
    db.commit()
    return variant


@router.delete("/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    variant_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.ADMIN, Role.RESTAURANT_OWNER)),
):
    variant = _variant(db, variant_id)
    _can_manage(db, user, variant.product.restaurant_id)
    db.delete(variant)
    db.commit()
