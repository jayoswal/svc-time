import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from .db import SessionLocal
from .events import Binding
from .models import Timesheet, TimesheetStatus

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
]
