from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class EmployeeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class TimesheetStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EmployeeRead(Base):
    __tablename__ = "employee_read"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="ck_employee_read_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default=EmployeeStatus.ACTIVE)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    pto_entitlement_days: Mapped[int] = mapped_column(default=0)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Timesheet(Base):
    __tablename__ = "timesheets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED')",
            name="ck_timesheets_status",
        ),
        CheckConstraint("period_end >= period_start", name="ck_timesheets_period"),
        CheckConstraint("total_hours >= 0", name="ck_timesheets_total_hours"),
        CheckConstraint("overtime_hours >= 0", name="ck_timesheets_overtime_hours"),
        UniqueConstraint("employee_id", "period_start", name="uq_timesheets_employee_period"),
        Index("ix_timesheets_employee_period", "employee_id", "period_start", "period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default=TimesheetStatus.DRAFT)
    total_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    overtime_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entries: Mapped[list[TimeEntry]] = relationship(
        back_populates="timesheet",
        cascade="all, delete-orphan",
        order_by="TimeEntry.work_date, TimeEntry.id",
        lazy="selectin",
    )


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        CheckConstraint("hours > 0 AND hours <= 24", name="ck_time_entries_hours"),
        UniqueConstraint("timesheet_id", "work_date", name="uq_time_entries_timesheet_work_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    timesheet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("timesheets.id", ondelete="CASCADE"), index=True
    )
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    project_code: Mapped[str] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    timesheet: Mapped[Timesheet] = relationship(back_populates="entries")
