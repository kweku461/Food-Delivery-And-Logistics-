from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

def configure_sqlite(dbapi_conn) -> None:
    """Enforce foreign keys and make sure the maths functions used by the geo filter exist.

    Some SQLite builds ship without SIN/COS/ASIN/...; PostgreSQL always has them.
    """
    import math

    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
    for name, fn, n in (("sin", math.sin, 1), ("cos", math.cos, 1), ("asin", math.asin, 1),
                        ("sqrt", math.sqrt, 1), ("radians", math.radians, 1), ("power", math.pow, 2)):
        dbapi_conn.create_function(name, n, fn)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_connect(dbapi_conn, _):
        configure_sqlite(dbapi_conn)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
