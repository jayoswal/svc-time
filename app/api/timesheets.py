import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.auth import Claims, require
from ..core.config import settings
from ..core.errors import AppError
from ..db import get_db
from ..events import publish
from ..models import EmployeeRead, EmployeeStatus, TimeEntry, Timesheet, TimesheetStatus
from ..schemas import (
    TimeEntryInput,
    TimesheetCreate,
    TimesheetList,
    TimesheetResponse,
    TimesheetUpdate,
)

router = APIRouter(prefix="/api/v1/time/timesheets", tags=["Timesheets"])


def employee_id_from(claims: Claims) -> uuid.UUID:
    return uuid.UUID(claims["sub"])


def lock_employee_read(db: Session, employee_id: uuid.UUID) -> EmployeeRead:
    employee = db.scalar(
        select(EmployeeRead).where(EmployeeRead.id == employee_id).with_for_update()
    )
    if employee is not None:
        return employee

    employee = EmployeeRead(
        id=employee_id,
        status=EmployeeStatus.ACTIVE,
        manager_id=None,
        pto_entitlement_days=0,
        roles=[],
    )
    db.add(employee)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        employee = db.scalar(
            select(EmployeeRead).where(EmployeeRead.id == employee_id).with_for_update()
        )
        if employee is None:
            raise
    return employee


def owned_timesheet(
    db: Session,
    timesheet_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Timesheet:
    statement = select(Timesheet).where(Timesheet.id == timesheet_id)
    if for_update:
        statement = statement.with_for_update()
    timesheet = db.scalar(statement)
    if timesheet is None:
        raise AppError(404, "TIME_TIMESHEET_NOT_FOUND", "Timesheet not found.")
    if timesheet.employee_id != employee_id:
        raise AppError(403, "TIME_FORBIDDEN", "Timesheet belongs to another employee.")
    return timesheet


def ensure_draft(timesheet: Timesheet) -> None:
    if timesheet.status != TimesheetStatus.DRAFT:
        raise AppError(409, "TIME_TIMESHEET_NOT_DRAFT", "Only draft timesheets can be changed.")


def replace_entries(timesheet: Timesheet, entries: list[TimeEntryInput]) -> None:
    timesheet.entries.clear()
    for entry in entries:
        timesheet.entries.append(
            TimeEntry(
                work_date=entry.work_date,
                hours=Decimal(str(entry.hours)),
                project_code=entry.project_code,
                note=entry.note,
            )
        )


@router.get("", response_model=TimesheetList, operation_id="listTimesheets")
def list_timesheets(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    claims: Claims = Depends(require("EMPLOYEE")),
    db: Session = Depends(get_db),
) -> TimesheetList:
    employee_id = employee_id_from(claims)
    total = db.scalar(
        select(func.count()).select_from(Timesheet).where(Timesheet.employee_id == employee_id)
    )
    timesheets = db.scalars(
        select(Timesheet)
        .where(Timesheet.employee_id == employee_id)
        .order_by(Timesheet.period_start.desc(), Timesheet.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return TimesheetList(items=timesheets, total=total or 0)


@router.post(
    "",
    response_model=TimesheetResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTimesheet",
)
def create_timesheet(
    body: TimesheetCreate,
    claims: Claims = Depends(require("EMPLOYEE")),
    db: Session = Depends(get_db),
) -> Timesheet:
    employee_id = employee_id_from(claims)
    lock_employee_read(db, employee_id)
    duplicate_start = db.scalar(
        select(Timesheet.id).where(
            Timesheet.employee_id == employee_id,
            Timesheet.period_start == body.period_start,
        )
    )
    if duplicate_start is not None:
        raise AppError(
            409,
            "TIME_TIMESHEET_PERIOD_EXISTS",
            "A timesheet already starts on this date.",
        )
    overlap = db.scalar(
        select(Timesheet.id).where(
            Timesheet.employee_id == employee_id,
            Timesheet.period_start <= body.period_end,
            Timesheet.period_end >= body.period_start,
        )
    )
    if overlap is not None:
        raise AppError(
            409,
            "TIME_TIMESHEET_OVERLAP",
            "The timesheet period overlaps another timesheet.",
        )
    timesheet = Timesheet(
        employee_id=employee_id,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    replace_entries(timesheet, list(body.entries))
    db.add(timesheet)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409,
            "TIME_TIMESHEET_PERIOD_EXISTS",
            "A timesheet already starts on this date.",
        ) from exc
    db.refresh(timesheet)
    return timesheet


@router.get(
    "/{timesheet_id}", response_model=TimesheetResponse, operation_id="getTimesheet"
)
def get_timesheet(
    timesheet_id: uuid.UUID,
    claims: Claims = Depends(require("EMPLOYEE")),
    db: Session = Depends(get_db),
) -> Timesheet:
    return owned_timesheet(db, timesheet_id, employee_id_from(claims))


@router.put(
    "/{timesheet_id}", response_model=TimesheetResponse, operation_id="updateTimesheet"
)
def update_timesheet(
    timesheet_id: uuid.UUID,
    body: TimesheetUpdate,
    claims: Claims = Depends(require("EMPLOYEE")),
    db: Session = Depends(get_db),
) -> Timesheet:
    timesheet = owned_timesheet(
        db, timesheet_id, employee_id_from(claims), for_update=True
    )
    if timesheet.status != TimesheetStatus.DRAFT:
        raise AppError(
            422,
            "TIME_TIMESHEET_NOT_DRAFT",
            "Only draft timesheets can be changed.",
        )
    timesheet.entries.clear()
    db.flush()
    replace_entries(timesheet, list(body.entries))
    db.commit()
    db.refresh(timesheet)
    return timesheet


@router.post(
    "/{timesheet_id}/submit",
    response_model=TimesheetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submitTimesheet",
)
async def submit_timesheet(
    timesheet_id: uuid.UUID,
    request: Request,
    claims: Claims = Depends(require("EMPLOYEE")),
    db: Session = Depends(get_db),
) -> Timesheet:
    employee_id = employee_id_from(claims)
    timesheet = owned_timesheet(db, timesheet_id, employee_id, for_update=True)
    ensure_draft(timesheet)
    if not timesheet.entries:
        raise AppError(
            422, "TIME_TIMESHEET_EMPTY", "A timesheet must contain at least one entry."
        )
    invalid_dates = [
        entry
        for entry in timesheet.entries
        if entry.work_date < timesheet.period_start or entry.work_date > timesheet.period_end
    ]
    if invalid_dates:
        raise AppError(
            422,
            "TIME_ENTRY_OUTSIDE_PERIOD",
            "All entry dates must fall within the timesheet period.",
            [{"field": "entries", "issue": "contains a date outside the timesheet period"}],
        )
    overlap = db.scalar(
        select(Timesheet.id).where(
            Timesheet.employee_id == employee_id,
            Timesheet.id != timesheet.id,
            Timesheet.period_start <= timesheet.period_end,
            Timesheet.period_end >= timesheet.period_start,
        )
    )
    if overlap is not None:
        raise AppError(
            409,
            "TIME_TIMESHEET_OVERLAP",
            "The timesheet period overlaps another submitted timesheet.",
        )

    total = sum((entry.hours for entry in timesheet.entries), start=Decimal("0.00"))
    timesheet.total_hours = total
    timesheet.overtime_hours = max(
        Decimal("0.00"), total - settings.standard_week_hours
    )
    timesheet.status = TimesheetStatus.PENDING_APPROVAL
    db.commit()
    db.refresh(timesheet)

    await publish(
        "time.events",
        "timesheet.submitted",
        {
            "timesheet_id": str(timesheet.id),
            "employee_id": str(timesheet.employee_id),
            "period_start": timesheet.period_start.isoformat(),
            "period_end": timesheet.period_end.isoformat(),
            "total_hours": float(timesheet.total_hours),
            "overtime_hours": float(timesheet.overtime_hours),
        },
        request.state.correlation_id,
    )
    return timesheet
