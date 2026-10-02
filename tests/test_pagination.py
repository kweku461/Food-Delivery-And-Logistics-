import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.pagination import PageParams, SortParams, page_params, paginate, sort_params
from app.models import User


def make_users(db, n, same_name=False):
    for i in range(n):
        db.add(User(
            email=f"user{i:02d}@test.com",
            hashed_password="x",
            full_name="Same Name" if same_name else f"User {i:02d}",
        ))
    db.flush()


def run(db, page, limit, sort_by="email", order="asc"):
    return paginate(db, select(User), User, PageParams(page, limit), SortParams(sort_by, order))


def test_first_page(db):
    make_users(db, 25)
    r = run(db, 1, 10)
    assert len(r["items"]) == 10
    assert r["total"] == 25 and r["pages"] == 3 and r["page"] == 1 and r["limit"] == 10
    assert r["items"][0].email == "user00@test.com"


def test_middle_page(db):
    make_users(db, 25)
    r = run(db, 2, 10)
    assert [u.email for u in r["items"]][0] == "user10@test.com"
    assert len(r["items"]) == 10


def test_last_partial_page(db):
    make_users(db, 25)
    r = run(db, 3, 10)
    assert len(r["items"]) == 5


def test_page_beyond_end_returns_empty_items(db):
    make_users(db, 25)
    r = run(db, 99, 10)
    assert r["items"] == [] and r["total"] == 25


def test_empty_table(db):
    r = run(db, 1, 10)
    assert r["items"] == [] and r["total"] == 0 and r["pages"] == 0


def test_sort_descending(db):
    make_users(db, 5)
    r = run(db, 1, 10, order="desc")
    emails = [u.email for u in r["items"]]
    assert emails == sorted(emails, reverse=True)


def test_ties_are_stable_across_pages(db):
    make_users(db, 12, same_name=True)
    seen = []
    for p in (1, 2, 3):
        seen += [u.id for u in run(db, p, 5, sort_by="full_name")["items"]]
    assert len(seen) == 12
    assert len(set(seen)) == 12          # no duplicates or gaps between pages


def test_sort_params_accepts_allowed_column():
    dep = sort_params(["created_at", "name"])
    s = dep(sort_by="name", order="asc")
    assert s.sort_by == "name" and s.order == "asc"


def test_sort_params_rejects_unknown_column():
    dep = sort_params(["created_at", "name"])
    with pytest.raises(HTTPException) as exc:
        dep(sort_by="hashed_password", order="asc")
    assert exc.value.status_code == 422


# ── query-string validation through a throwaway app ──
_app = FastAPI()


@_app.get("/t")
def _t(p: PageParams = Depends(page_params)):
    return {"page": p.page, "limit": p.limit}


client = TestClient(_app)


def test_defaults():
    assert client.get("/t").json() == {"page": 1, "limit": 20}


@pytest.mark.parametrize("qs", ["page=0", "page=-1", "limit=0", "limit=101"])
def test_invalid_page_params_rejected(qs):
    assert client.get(f"/t?{qs}").status_code == 422