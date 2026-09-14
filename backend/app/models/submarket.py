# backend/app/models/submarket.py
"""The submarket list — a table that grows, not a fixed set of values.

Jack represents tenants wherever they go (Sterling, Loudoun, Prince William),
so the dropdown reads from here and gains a value whenever he adds one or a
confirmed lease names a place that is not on the list yet (auto_created=True).

Names are unique case-insensitively ("sterling" is Sterling). The NOCASE
collation enforces that at the database; services/submarket_service.py also
matches case-insensitively before it ever tries an insert.

Company.current_submarket stays a plain string, so every existing value and
every benchmark lookup keyed on it keeps working unchanged. A submarket with no
benchmark in config.SUBMARKET_BENCHMARKS is simply "not on file" — never an
error, never a quoted number.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text

from app.database import Base


class Submarket(Base):
    __tablename__ = "submarkets"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(collation="NOCASE"), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    auto_created = Column(Boolean, nullable=False, default=False, server_default=text("0"))
