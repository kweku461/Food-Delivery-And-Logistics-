from app.database import Base, engine
import app.models  # noqa: F401

Base.metadata.create_all(engine)
print("Tables created")