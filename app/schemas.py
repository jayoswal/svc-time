import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import TimesheetStatus


class TimeEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_date: date
    hours: float = Field(strict=True, gt=0, le=24)
    project_code: str = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("hours")
    @classmethod
    def validate_hours_precision(cls, value: float) -> float:
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.quantize(Decimal("0.01")):
            raise ValueError("must have no more than two decimal places")
        return value

    @field_validator("project_code")
    @classmethod
    def normalize_project_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class TimeEntryResponse(TimeEntryInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class TimesheetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    entries: list[TimeEntryInput] = Field(
        default_factory=list, max_length=7, json_schema_extra={"default": []}
    )

    @field_validator("entries")
    @classmethod
    def validate_unique_entry_dates(cls, entries: list[TimeEntryInput]) -> list[TimeEntryInput]:
        dates = [entry.work_date for entry in entries]
        if len(dates) != len(set(dates)):
            raise ValueError("entries must contain at most one entry per work_date")
        return entries

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class TimesheetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TimeEntryInput] = Field(max_length=7)

    @field_validator("entries")
    @classmethod
    def validate_unique_entry_dates(cls, entries: list[TimeEntryInput]) -> list[TimeEntryInput]:
        dates = [entry.work_date for entry in entries]
        if len(dates) != len(set(dates)):
            raise ValueError("entries must contain at most one entry per work_date")
        return entries


class TimesheetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    period_start: date
    period_end: date
    status: TimesheetStatus
    total_hours: float = Field(ge=0)
    overtime_hours: float = Field(ge=0)
    entries: list[TimeEntryResponse]
    created_at: datetime
    updated_at: datetime


class TimesheetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TimesheetResponse]
    total: int = Field(ge=0)


class PtoBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: uuid.UUID
    entitlement_days: float
    accrued_days: float
    taken_days: float
    pending_days: float
    balance_days: float
