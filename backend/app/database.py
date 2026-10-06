"""SQLite engine, the per-request session dependency, and table creation on startup."""

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (imported so its tables are registered before create_all)
from app.config import settings

DB_PATH = settings.data_path / "library.db"

# check_same_thread=False: FastAPI may open a session in one worker thread and use it in another.
engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}", connect_args={"check_same_thread": False})


def create_db() -> None:
    """Create the data folder and any missing tables. Existing tables and rows are left alone."""
    settings.data_path.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards."""
    with Session(engine) as session:
        yield session
