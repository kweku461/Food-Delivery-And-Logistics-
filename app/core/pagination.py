from dataclasses import dataclass
from math import ceil
from typing import Generic, Literal, Sequence, TypeVar

from fastapi import HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

T = TypeVar("T")
SortOrder = Literal["asc", "desc"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    limit: int
    pages: int


@dataclass
class PageParams:
    page: int
    limit: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


@dataclass
class SortParams:
    sort_by: str
    order: SortOrder


def page_params(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PageParams:
    return PageParams(page, limit)


def sort_params(allowed: Sequence[str], default: str = "created_at"):
    """Factory: each resource declares which columns clients may sort on."""
    allowed = tuple(allowed)

    def dependency(
        sort_by: str = Query(default),
        order: SortOrder = Query("desc"),
    ) -> SortParams:
        if sort_by not in allowed:
            # swap for the custom validation exception once error handling exists
            raise HTTPException(422, f"sort_by must be one of: {', '.join(allowed)}")
        return SortParams(sort_by, order)

    return dependency


def paginate(db: Session, stmt, model, page: PageParams, sort: SortParams | None = None) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    if sort:
        col = getattr(model, sort.sort_by)
        stmt = stmt.order_by(col.asc() if sort.order == "asc" else col.desc(), model.id.asc())
    items = db.scalars(stmt.offset(page.offset).limit(page.limit)).unique().all()
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "limit": page.limit,
        "pages": ceil(total / page.limit) if total else 0,
    }