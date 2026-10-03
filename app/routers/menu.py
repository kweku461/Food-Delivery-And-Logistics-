"""Menus, categories, products and product variants."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import (
    assert_can_manage_restaurant, can_manage_restaurant, get_optional_user, require_roles,
)
from app.enums import Role
from app.errors import AuthorizationError, BadRequestError, ConflictError, NotFoundError
from app.models import Category, Menu, Product, ProductVariant, Restaurant, User
from app.core.pagination import Page, PageParams, SortParams, page_params, paginate, sort_params
from app.schemas.catalog import (
    CategoryCreate, CategoryRead, CategoryUpdate, MenuCreate, MenuDetail, MenuRead, MenuUpdate,
    ProductCreate, ProductDetail, ProductFilter, ProductRead, ProductUpdate, VariantCreate,
    VariantRead, VariantUpdate,
)
from app.utils import get_or_404, like_pattern

router = APIRouter(tags=["Menu & Products"])
menu_sort = sort_params(["created_at", "name", "id"])
category_sort = sort_params(["sort_order", "created_at", "name", "id"], default="sort_order")
product_sort = sort_params(["created_at", "name", "base_price", "id", "prep_time_minutes"])
variant_sort = sort_params(["created_at", "name", "price_modifier", "id"])
EDITORS = (Role.ADMIN, Role.RESTAURANT_OWNER)
EDITORS_AND_STAFF = (Role.ADMIN, Role.RESTAURANT_OWNER, Role.STAFF)


def _public_restaurant_filter(user: User | None):
    """SQL condition limiting what a caller may see through product/menu queries."""
    if user is not None and user.role == Role.ADMIN:
        return None
    cond = Restaurant.is_active.is_(True)
    if user is not None and user.role == Role.RESTAURANT_OWNER:
        cond = or_(cond, Restaurant.owner_id == user.id)
    return cond


# ═════════════════════════ menus ═════════════════════════
@router.post("/menus", response_model=MenuRead, status_code=status.HTTP_201_CREATED)
def create_menu(data: MenuCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*EDITORS))):
    assert_can_manage_restaurant(db, user, data.restaurant_id)
    menu = Menu(**data.model_dump())
    db.add(menu)
    db.commit()
    return menu


@router.get("/menus", response_model=Page[MenuRead])
def list_menus(restaurant_id: int | None = None, is_active: bool | None = None,
               page: PageParams = Depends(page_params), sort: SortParams = Depends(menu_sort), db: Session = Depends(get_db),
               user: User | None = Depends(get_optional_user)):
    stmt = select(Menu).join(Restaurant, Menu.restaurant_id == Restaurant.id)
    cond = _public_restaurant_filter(user)
    if cond is not None:
        stmt = stmt.where(cond)
        if not (user and user.role == Role.RESTAURANT_OWNER):
            stmt = stmt.where(Menu.is_active.is_(True))
    if restaurant_id is not None:
        stmt = stmt.where(Menu.restaurant_id == restaurant_id)
    if is_active is not None:
        stmt = stmt.where(Menu.is_active == is_active)
    return paginate(db, stmt, Menu, page, sort)


@router.get("/menus/{menu_id}", response_model=MenuDetail, summary="Menu with its categories")
def get_menu(menu_id: int, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    menu = get_or_404(db, Menu, menu_id, "Menu")
    managed = user is not None and can_manage_restaurant(db, user, menu.restaurant_id, allow_staff=True)
    if not managed and not (menu.is_active and menu.restaurant.is_active):
        raise NotFoundError("Menu not found")
    return menu


@router.patch("/menus/{menu_id}", response_model=MenuRead)
def update_menu(menu_id: int, data: MenuUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_roles(*EDITORS))):
    menu = get_or_404(db, Menu, menu_id, "Menu")
    assert_can_manage_restaurant(db, user, menu.restaurant_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(menu, k, v)
    db.commit()
    return menu


@router.delete("/menus/{menu_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu(menu_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*EDITORS))):
    menu = get_or_404(db, Menu, menu_id, "Menu")
    assert_can_manage_restaurant(db, user, menu.restaurant_id)
    db.delete(menu)
    db.commit()


# ═════════════════════════ categories ═════════════════════════
@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(data: CategoryCreate, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(*EDITORS))):
    menu = get_or_404(db, Menu, data.menu_id, "Menu")
    assert_can_manage_restaurant(db, user, menu.restaurant_id)
    if db.scalar(select(Category.id).where(Category.menu_id == menu.id, Category.name == data.name)):
        raise ConflictError("This menu already has a category with that name")
    cat = Category(**data.model_dump())
    db.add(cat)
    db.commit()
    return cat


@router.get("/categories", response_model=Page[CategoryRead])
def list_categories(menu_id: int | None = None, restaurant_id: int | None = None,
                    search: str | None = None, page: PageParams = Depends(page_params),
                    sort: SortParams = Depends(category_sort),
                    db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    stmt = select(Category).join(Menu).join(Restaurant, Menu.restaurant_id == Restaurant.id)
    cond = _public_restaurant_filter(user)
    if cond is not None:
        stmt = stmt.where(cond, Menu.is_active.is_(True) | (Restaurant.owner_id == (user.id if user else -1)))
    if menu_id is not None:
        stmt = stmt.where(Category.menu_id == menu_id)
    if restaurant_id is not None:
        stmt = stmt.where(Menu.restaurant_id == restaurant_id)
    if search:
        stmt = stmt.where(Category.name.ilike(like_pattern(search), escape="\\"))
    return paginate(db, stmt, Category, page, sort)


@router.get("/categories/{category_id}", response_model=CategoryRead)
def get_category(category_id: int, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    cat = get_or_404(db, Category, category_id, "Category")
    managed = user is not None and can_manage_restaurant(db, user, cat.menu.restaurant_id, allow_staff=True)
    if not managed and not (cat.menu.is_active and cat.menu.restaurant.is_active):
        raise NotFoundError("Category not found")
    return cat


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(category_id: int, data: CategoryUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(*EDITORS))):
    cat = get_or_404(db, Category, category_id, "Category")
    assert_can_manage_restaurant(db, user, cat.menu.restaurant_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != cat.name and db.scalar(
            select(Category.id).where(Category.menu_id == cat.menu_id, Category.name == changes["name"])):
        raise ConflictError("This menu already has a category with that name")
    for k, v in changes.items():
        setattr(cat, k, v)
    db.commit()
    return cat


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(*EDITORS))):
    cat = get_or_404(db, Category, category_id, "Category")
    assert_can_manage_restaurant(db, user, cat.menu.restaurant_id)
    db.delete(cat)
    db.commit()


# ═════════════════════════ products ═════════════════════════
@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*EDITORS))):
    cat = get_or_404(db, Category, data.category_id, "Category")
    assert_can_manage_restaurant(db, user, cat.menu.restaurant_id)
    product = Product(restaurant_id=cat.menu.restaurant_id, **data.model_dump())
    db.add(product)
    db.commit()
    return product


@router.get("/products", response_model=Page[ProductRead],
            summary="Advanced product search (text, category, restaurant, price range, availability)")
def list_products(
    filters: Annotated[ProductFilter, Query()],
    page: PageParams = Depends(page_params),
    sort: SortParams = Depends(product_sort),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stmt = (select(Product).join(Category, Product.category_id == Category.id)
            .join(Menu, Category.menu_id == Menu.id)
            .join(Restaurant, Product.restaurant_id == Restaurant.id))
    cond = _public_restaurant_filter(user)
    if cond is not None:
        stmt = stmt.where(cond, Menu.is_active.is_(True) | (Restaurant.owner_id == (user.id if user else -1)))
    if filters.search:
        pat = like_pattern(filters.search)
        stmt = stmt.where(or_(Product.name.ilike(pat, escape="\\"),
                              Product.description.ilike(pat, escape="\\")))
    if filters.category:
        stmt = stmt.where(Category.name.ilike(filters.category))
    if filters.category_id is not None:
        stmt = stmt.where(Product.category_id == filters.category_id)
    if filters.restaurant_id is not None:
        stmt = stmt.where(Product.restaurant_id == filters.restaurant_id)
    if filters.min_price is not None:
        stmt = stmt.where(Product.base_price >= filters.min_price)
    if filters.max_price is not None:
        stmt = stmt.where(Product.base_price <= filters.max_price)
    if filters.available is not None:
        stmt = stmt.where(Product.is_available == filters.available)
    return paginate(db, stmt, Product, page, sort)


@router.get("/products/{product_id}", response_model=ProductDetail, summary="Product details with variants")
def get_product(product_id: int, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    product = get_or_404(db, Product, product_id, "Product")
    managed = user is not None and can_manage_restaurant(db, user, product.restaurant_id, allow_staff=True)
    if not managed and not (product.restaurant.is_active and product.category.menu.is_active):
        raise NotFoundError("Product not found")
    out = ProductDetail.model_validate(product)
    if not managed:
        out.variants = [v for v in out.variants if v.is_available]
    return out


@router.patch("/products/{product_id}", response_model=ProductRead,
              summary="Update a product (STAFF may only toggle is_available)")
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*EDITORS_AND_STAFF))):
    product = get_or_404(db, Product, product_id, "Product")
    assert_can_manage_restaurant(db, user, product.restaurant_id, allow_staff=True)
    changes = data.model_dump(exclude_unset=True)
    if user.role == Role.STAFF and set(changes) - {"is_available"}:
        raise AuthorizationError("Staff can only change a product's availability")
    if "category_id" in changes:
        new_cat = get_or_404(db, Category, changes["category_id"], "Category")
        if new_cat.menu.restaurant_id != product.restaurant_id:
            raise BadRequestError("Category belongs to a different restaurant")
    for k, v in changes.items():
        setattr(product, k, v)
    db.commit()
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*EDITORS))):
    product = get_or_404(db, Product, product_id, "Product")
    assert_can_manage_restaurant(db, user, product.restaurant_id)
    db.delete(product)
    db.commit()


# ═════════════════════════ product variants ═════════════════════════
@router.post("/product-variants", response_model=VariantRead, status_code=status.HTTP_201_CREATED)
def create_variant(data: VariantCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*EDITORS))):
    product = get_or_404(db, Product, data.product_id, "Product")
    assert_can_manage_restaurant(db, user, product.restaurant_id)
    if product.base_price + data.price_modifier < 0:
        raise BadRequestError("price_modifier would make the final price negative")
    variant = ProductVariant(**data.model_dump())
    db.add(variant)
    db.commit()
    return variant


@router.get("/product-variants", response_model=Page[VariantRead])
def list_variants(product_id: int | None = None, available: bool | None = None,
                  page: PageParams = Depends(page_params), sort: SortParams = Depends(variant_sort), db: Session = Depends(get_db),
                  user: User | None = Depends(get_optional_user)):
    stmt = (select(ProductVariant).join(Product).join(Restaurant, Product.restaurant_id == Restaurant.id))
    cond = _public_restaurant_filter(user)
    if cond is not None:
        stmt = stmt.where(cond)
    if product_id is not None:
        stmt = stmt.where(ProductVariant.product_id == product_id)
    if available is not None:
        stmt = stmt.where(ProductVariant.is_available == available)
    return paginate(db, stmt, ProductVariant, page, sort)


@router.get("/product-variants/{variant_id}", response_model=VariantRead)
def get_variant(variant_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, ProductVariant, variant_id, "Variant")


@router.patch("/product-variants/{variant_id}", response_model=VariantRead,
              summary="Update a variant (STAFF may only toggle is_available)")
def update_variant(variant_id: int, data: VariantUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*EDITORS_AND_STAFF))):
    variant = get_or_404(db, ProductVariant, variant_id, "Variant")
    assert_can_manage_restaurant(db, user, variant.product.restaurant_id, allow_staff=True)
    changes = data.model_dump(exclude_unset=True)
    if user.role == Role.STAFF and set(changes) - {"is_available"}:
        raise AuthorizationError("Staff can only change a variant's availability")
    if "price_modifier" in changes and variant.product.base_price + changes["price_modifier"] < 0:
        raise BadRequestError("price_modifier would make the final price negative")
    for k, v in changes.items():
        setattr(variant, k, v)
    db.commit()
    return variant


@router.delete("/product-variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(variant_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*EDITORS))):
    variant = get_or_404(db, ProductVariant, variant_id, "Variant")
    assert_can_manage_restaurant(db, user, variant.product.restaurant_id)
    db.delete(variant)
    db.commit()
