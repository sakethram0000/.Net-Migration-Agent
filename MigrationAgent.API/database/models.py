"""
Database models — Users table.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from database.db import Base


class User(Base):
    __tablename__ = "users"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email         = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role          = Column(String, default="user")   # "admin" or "user"
    created_at    = Column(DateTime, default=datetime.utcnow)
