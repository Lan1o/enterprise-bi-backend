from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from bi_backend.db import Base


def utcnow() -> datetime:

    return datetime.now(UTC).replace(tzinfo=None)


class SourceState(Base):
    __tablename__ = "source_states"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    lock_token: Mapped[str | None] = mapped_column(String(36))
    lock_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','succeeded','failed')", name="ck_sync_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_states.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class RawRecord(Base):
    __tablename__ = "raw_records"

    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.id"), primary_key=True)
    row_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class SalesFact(Base):
    __tablename__ = "sales_facts"
    __table_args__ = (CheckConstraint("amount >= 0", name="ck_sales_amount"),)

    source_id: Mapped[str] = mapped_column(ForeignKey("source_states.id"), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(100))
    closed: Mapped[bool] = mapped_column(Boolean)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    customer_id: Mapped[str | None] = mapped_column(String(200))
