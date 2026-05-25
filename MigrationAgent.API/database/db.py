"""
Database connection — Supabase PostgreSQL via SQLAlchemy.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase


class Base(DeclarativeBase):
    pass


def _get_engine():
    """Build engine lazily so .env is already loaded when this runs."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set — check your .env file.")
    return create_engine(url, pool_pre_ping=True)


def get_db():
    """FastAPI dependency — yields a database session."""
    engine = _get_engine()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables on startup."""
    from database.models import User  # noqa: F401
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
