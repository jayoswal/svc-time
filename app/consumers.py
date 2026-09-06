import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from .db import SessionLocal
from .events import Binding
from .models import EmployeeRead, EmployeeStatus, Timesheet, TimesheetStatus

logger = logging.getLogger(__name__)


def _finalize_timesheet_sync(timesheet_id: str, status: str) -> None:
    with SessionLocal() as db:
        timesheet = db.scalar(
            select(Timesheet)
            .where(Timesheet.id == uuid.UUID(timesheet_id))
            .with_for_update()
        )
        if timesheet is None:
            logger.warning("Received a decision for unknown timesheet %s", timesheet_id)
            return
        if timesheet.status != TimesheetStatus.PENDING_APPROVAL:
            # Already finalized (or never submitted): a redelivered decision is a no-op.
            return
        timesheet.status = status
        db.commit()


async def handle_timesheet_approved(event: dict[str, Any]) -> None:
    await asyncio.to_thread(
        _finalize_timesheet_sync, event["data"]["timesheet_id"], TimesheetStatus.APPROVED
    )


async def handle_timesheet_rejected(event: dict[str, Any]) -> None:
    await asyncio.to_thread(
        _finalize_timesheet_sync, event["data"]["timesheet_id"], TimesheetStatus.REJECTED
    )


def _upsert_employee_created_sync(data: dict[str, Any]) -> None:
    employee_id = uuid.UUID(data["employee_id"])
    with SessionLocal() as db:
        employee = db.get(EmployeeRead, employee_id, with_for_update=True)
        manager_id = uuid.UUID(data["manager_id"]) if data.get("manager_id") else None
        if employee is None:
            db.add(
                EmployeeRead(
                    id=employee_id,
                    status=data.get("status", EmployeeStatus.ACTIVE),
                    manager_id=manager_id,
                    pto_entitlement_days=data.get("pto_entitlement_days", 0),
                    roles=[],
                )
            )
        else:
            # A redelivered employee.created is a no-op update, not a duplicate row.
            employee.status = data.get("status", employee.status)
            employee.manager_id = manager_id
            employee.pto_entitlement_days = data.get(
                "pto_entitlement_days", employee.pto_entitlement_days
            )
        db.commit()


def _apply_employee_updated_sync(data: dict[str, Any]) -> None:
    employee_id = uuid.UUID(data["employee_id"])
    changed = data.get("changed", {})
    with SessionLocal() as db:
        employee = db.get(EmployeeRead, employee_id, with_for_update=True)
        if employee is None:
            logger.warning("Received an update for unprovisioned employee %s", employee_id)
            return
        if "manager_id" in changed:
            employee.manager_id = (
                uuid.UUID(changed["manager_id"]) if changed["manager_id"] else None
            )
        if "pto_entitlement_days" in changed:
            employee.pto_entitlement_days = changed["pto_entitlement_days"]
        db.commit()


def _deactivate_employee_sync(employee_id: str) -> None:
    with SessionLocal() as db:
        employee = db.get(EmployeeRead, uuid.UUID(employee_id), with_for_update=True)
        if employee is None:
            logger.warning("Received a deactivation for unprovisioned employee %s", employee_id)
            return
        employee.status = EmployeeStatus.INACTIVE
        db.commit()


async def handle_employee_created(event: dict[str, Any]) -> None:
    await asyncio.to_thread(_upsert_employee_created_sync, event["data"])


async def handle_employee_updated(event: dict[str, Any]) -> None:
    await asyncio.to_thread(_apply_employee_updated_sync, event["data"])


async def handle_employee_deactivated(event: dict[str, Any]) -> None:
    await asyncio.to_thread(_deactivate_employee_sync, event["data"]["employee_id"])


async def handle_employee_event(event: dict[str, Any]) -> None:
    handlers: dict[str, Any] = {
        "employee.created": handle_employee_created,
        "employee.updated": handle_employee_updated,
        "employee.deactivated": handle_employee_deactivated,
    }
    handler = handlers.get(event["type"])
    if handler is not None:
        await handler(event)


BINDINGS: list[Binding] = [
    (
        "workflow.events",
        "time.timesheet.approved",
        ["timesheet.approved"],
        handle_timesheet_approved,
    ),
    (
        "workflow.events",
        "time.timesheet.rejected",
        ["timesheet.rejected"],
        handle_timesheet_rejected,
    ),
    (
        "identity.events",
        "time.employee-events",
        ["employee.created", "employee.updated", "employee.deactivated"],
        handle_employee_event,
    ),
]
