import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base
from app.main import app
from app.database import get_db

import app.models  # noqa: F401


H = {"Authorization": "Bearer test-token"}
PW = "password123"

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://neondb_owner:npg_8aPQyGg9odrs@ep-quiet-water-b1xmghxz-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL)

    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)

    yield eng

    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
    """Each test runs inside a transaction that is rolled back afterwards."""
    conn = engine.connect()
    trans = conn.begin()

    session = sessionmaker(
        bind=conn,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()

    yield session

    session.close()
    trans.rollback()
    conn.close()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()