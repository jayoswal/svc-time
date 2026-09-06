import uuid
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.consumers import (
    handle_employee_created,
    handle_employee_deactivated,
    handle_employee_updated,
    handle_timesheet_approved,
    handle_timesheet_rejected,
)
from app.core.config import Settings, settings
from app.models import EmployeeRead, EmployeeStatus, TimeEntry, Timesheet
from app.seed import DEMO_EMPLOYEES, seed_demo_data
from tests.conftest import bearer, token_for

ADA_ID = "10000000-0000-4000-8000-000000000002"
GRACE_ID = "10000000-0000-4000-8000-000000000001"


def create_timesheet(
    client: TestClient,
    token: str,
    start: str = "2026-09-01",
    end: str = "2026-09-07",
    entries: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token),
        json={
            "period_start": start,
            "period_end": end,
            "entries": entries or [],
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def test_health_and_auth_errors_use_correlation_envelope(client: TestClient) -> None:
    correlation_id = str(uuid.uuid4())
    health = client.get("/healthz", headers={"X-Correlation-Id": correlation_id})
    assert health.json() == {"status": "ok", "service": "svc-time"}
    assert health.headers["X-Correlation-Id"] == correlation_id

    denied = client.get("/api/v1/time/timesheets", headers={"X-Correlation-Id": correlation_id})
    assert denied.status_code == 401
    assert denied.json() == {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "Missing bearer token.",
            "correlation_id": correlation_id,
            "details": [],
        }
    }


def test_create_get_and_list_only_self_timesheets(client: TestClient) -> None:
    ada = token_for()
    grace = token_for(GRACE_ID, ["EMPLOYEE", "MANAGER"])
    created = create_timesheet(client, ada)

    fetched = client.get(f"/api/v1/time/timesheets/{created['id']}", headers=bearer(ada))
    assert fetched.status_code == 200
    assert fetched.json()["employee_id"] == ADA_ID
    assert fetched.json()["status"] == "DRAFT"

    forbidden = client.get(f"/api/v1/time/timesheets/{created['id']}", headers=bearer(grace))
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "TIME_FORBIDDEN"

    listed = client.get("/api/v1/time/timesheets", headers=bearer(ada))
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    other_list = client.get("/api/v1/time/timesheets", headers=bearer(grace))
    assert other_list.json() == {"items": [], "total": 0}


def test_update_is_owner_only(client: TestClient) -> None:
    created = create_timesheet(client, token_for())
    grace_headers = bearer(token_for(GRACE_ID, ["EMPLOYEE", "MANAGER"]))
    response = client.put(
        f"/api/v1/time/timesheets/{created['id']}",
        headers=grace_headers,
        json={"entries": []},
    )
    assert response.status_code == 403
    submit = client.post(
        f"/api/v1/time/timesheets/{created['id']}/submit",
        headers=grace_headers,
    )
    assert submit.status_code == 403


def test_update_replaces_entries_for_the_same_work_date(client: TestClient) -> None:
    token = token_for()
    created = create_timesheet(
        client,
        token,
        entries=[{"work_date": "2026-09-01", "hours": 8, "project_code": "OLD"}],
    )
    response = client.put(
        f"/api/v1/time/timesheets/{created['id']}",
        headers=bearer(token),
        json={"entries": [{"work_date": "2026-09-01", "hours": 6.5, "project_code": "NEW"}]},
    )
    assert response.status_code == 200
    assert response.json()["entries"] == [
        {
            "id": response.json()["entries"][0]["id"],
            "work_date": "2026-09-01",
            "hours": 6.5,
            "project_code": "NEW",
            "note": None,
        }
    ]


def test_submit_computes_totals_and_publishes_after_commit(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = token_for()
    created = create_timesheet(
        client,
        token,
        entries=[
            {
                "work_date": "2026-09-01",
                "hours": 24,
                "project_code": "ATLAS",
                "note": "Build",
            },
            {
                "work_date": "2026-09-02",
                "hours": 24,
                "project_code": "ATLAS",
            },
            {
                "work_date": "2026-09-03",
                "hours": 2,
                "project_code": "OPS",
            },
        ],
    )
    published: list[tuple[str, str, dict[str, object], str]] = []

    async def record_publish(
        exchange: str, event_type: str, data: dict[str, object], correlation_id: str
    ) -> None:
        persisted = db.get(Timesheet, uuid.UUID(created["id"]))
        assert persisted is not None
        assert persisted.status == "PENDING_APPROVAL"
        published.append((exchange, event_type, data, correlation_id))

    monkeypatch.setattr("app.api.timesheets.publish", record_publish)
    correlation_id = str(uuid.uuid4())
    response = client.post(
        f"/api/v1/time/timesheets/{created['id']}/submit",
        headers={**bearer(token), "X-Correlation-Id": correlation_id},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_APPROVAL"
    assert response.json()["total_hours"] == 50
    assert response.json()["overtime_hours"] == 10
    assert len(published) == 1
    exchange, event_type, data, event_correlation_id = published[0]
    assert exchange == "time.events"
    assert event_type == "timesheet.submitted"
    assert event_correlation_id == correlation_id
    assert data == {
        "timesheet_id": created["id"],
        "employee_id": ADA_ID,
        "period_start": "2026-09-01",
        "period_end": "2026-09-07",
        "total_hours": 50.0,
        "overtime_hours": 10.0,
    }


def test_submitted_timesheet_cannot_be_updated_or_resubmitted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ignore_publish(*_: object) -> None:
        return None

    monkeypatch.setattr("app.api.timesheets.publish", ignore_publish)
    token = token_for()
    created = create_timesheet(
        client,
        token,
        entries=[{"work_date": "2026-09-01", "hours": 8, "project_code": "ATLAS"}],
    )
    submit_url = f"/api/v1/time/timesheets/{created['id']}/submit"
    assert client.post(submit_url, headers=bearer(token)).status_code == 202

    update = client.put(
        f"/api/v1/time/timesheets/{created['id']}",
        headers=bearer(token),
        json={"entries": []},
    )
    assert update.status_code == 422
    assert update.json()["error"]["code"] == "TIME_TIMESHEET_NOT_DRAFT"
    assert client.post(submit_url, headers=bearer(token)).status_code == 409


@pytest.mark.parametrize(
    ("entries", "error_code"),
    [
        ([], "TIME_TIMESHEET_EMPTY"),
        (
            [{"work_date": "2026-09-08", "hours": 8, "project_code": "ATLAS"}],
            "TIME_ENTRY_OUTSIDE_PERIOD",
        ),
    ],
)
def test_submit_rejects_invalid_entries(
    client: TestClient,
    entries: list[dict[str, object]],
    error_code: str,
) -> None:
    token = token_for()
    created = create_timesheet(client, token, entries=entries)
    response = client.post(f"/api/v1/time/timesheets/{created['id']}/submit", headers=bearer(token))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == error_code


@pytest.mark.parametrize(
    "entry",
    [
        {"work_date": "2026-09-01", "hours": 0, "project_code": "ATLAS"},
        {"work_date": "2026-09-01", "hours": 25, "project_code": "ATLAS"},
        {"work_date": "2026-09-01", "hours": 8, "project_code": "   "},
        {"work_date": "2026-09-01", "hours": 8.001, "project_code": "ATLAS"},
    ],
)
def test_entry_schema_rejects_invalid_values(client: TestClient, entry: dict[str, object]) -> None:
    response = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token_for()),
        json={
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "entries": [entry],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TIME_VALIDATION_ERROR"


def test_entry_hours_rejects_json_strings(client: TestClient) -> None:
    response = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token_for()),
        json={
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "entries": [{"work_date": "2026-09-01", "hours": "8", "project_code": "ATLAS"}],
        },
    )
    assert response.status_code == 422


def test_period_validation_and_duplicate_start(client: TestClient) -> None:
    token = token_for()
    invalid = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token),
        json={"period_start": "2026-09-07", "period_end": "2026-09-01"},
    )
    assert invalid.status_code == 422

    create_timesheet(client, token)
    duplicate = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token),
        json={"period_start": "2026-09-01", "period_end": "2026-09-14"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "TIME_TIMESHEET_PERIOD_EXISTS"


def test_create_and_submit_reject_overlapping_periods(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ignore_publish(*_: object) -> None:
        return None

    monkeypatch.setattr("app.api.timesheets.publish", ignore_publish)
    token = token_for()
    first = create_timesheet(
        client,
        token,
        entries=[{"work_date": "2026-09-01", "hours": 8, "project_code": "ATLAS"}],
    )
    first_submit = client.post(
        f"/api/v1/time/timesheets/{first['id']}/submit", headers=bearer(token)
    )
    assert first_submit.status_code == 202

    create_overlap = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token),
        json={"period_start": "2026-09-05", "period_end": "2026-09-12"},
    )
    assert create_overlap.status_code == 409
    assert create_overlap.json()["error"]["code"] == "TIME_TIMESHEET_OVERLAP"

    overlapping_draft = Timesheet(
        employee_id=uuid.UUID(ADA_ID),
        period_start=date(2026, 9, 5),
        period_end=date(2026, 9, 12),
    )
    overlapping_draft.entries.append(
        TimeEntry(
            work_date=date(2026, 9, 5),
            hours=Decimal("8"),
            project_code="ATLAS",
        )
    )
    db.add(overlapping_draft)
    db.commit()
    submit_overlap = client.post(
        f"/api/v1/time/timesheets/{overlapping_draft.id}/submit", headers=bearer(token)
    )
    assert submit_overlap.status_code == 409
    assert submit_overlap.json()["error"]["code"] == "TIME_TIMESHEET_OVERLAP"


def test_jwt_claims_are_authoritative_even_without_a_synced_projection(
    client: TestClient, db: Session
) -> None:
    employee = db.get(EmployeeRead, uuid.UUID(ADA_ID))
    assert employee is not None
    token = token_for()
    employee.roles = []
    employee.status = EmployeeStatus.INACTIVE
    db.commit()
    allowed = client.get("/api/v1/time/timesheets", headers=bearer(token))
    assert allowed.status_code == 200

    denied = client.get(
        "/api/v1/time/timesheets",
        headers=bearer(token_for(ADA_ID, ["MANAGER"])),
    )
    assert denied.status_code == 403


def test_new_token_holder_can_create_without_existing_projection(
    client: TestClient, db: Session
) -> None:
    employee_id = "20000000-0000-4000-8000-000000000001"
    assert db.get(EmployeeRead, uuid.UUID(employee_id)) is None
    created = create_timesheet(client, token_for(employee_id))
    assert created["employee_id"] == employee_id
    projection = db.get(EmployeeRead, uuid.UUID(employee_id))
    assert projection is not None
    assert projection.pto_entitlement_days == 0


def test_pto_balance_uses_employee_entitlement(client: TestClient) -> None:
    response = client.get("/api/v1/time/pto/balance", headers=bearer(token_for()))
    assert response.status_code == 200
    assert response.json() == {
        "employee_id": ADA_ID,
        "entitlement_days": 22,
        "accrued_days": 22,
        "taken_days": 0,
        "pending_days": 0,
        "balance_days": 22,
    }


def test_pto_balance_is_zero_when_employee_projection_has_not_arrived(
    client: TestClient, db: Session
) -> None:
    employee_id = "20000000-0000-4000-8000-000000000002"
    assert db.get(EmployeeRead, uuid.UUID(employee_id)) is None

    response = client.get(
        "/api/v1/time/pto/balance",
        headers=bearer(token_for(employee_id)),
    )

    assert response.status_code == 200
    assert response.json() == {
        "employee_id": employee_id,
        "entitlement_days": 0,
        "accrued_days": 0,
        "taken_days": 0,
        "pending_days": 0,
        "balance_days": 0,
    }
    assert db.get(EmployeeRead, uuid.UUID(employee_id)) is None


def test_seed_is_idempotent(db: Session) -> None:
    ada = db.get(EmployeeRead, uuid.UUID(ADA_ID))
    assert ada is not None
    ada.status = EmployeeStatus.INACTIVE
    ada.manager_id = None
    ada.pto_entitlement_days = 99
    ada.roles = ["CUSTOM"]
    db.commit()

    seed_demo_data(db)
    seed_demo_data(db)
    count = db.scalar(select(func.count()).select_from(EmployeeRead))
    assert count == len(DEMO_EMPLOYEES)
    db.refresh(ada)
    assert ada.status == EmployeeStatus.INACTIVE
    assert ada.manager_id is None
    assert ada.pto_entitlement_days == 99
    assert ada.roles == ["CUSTOM"]


def test_entry_count_and_dates_are_bounded(client: TestClient) -> None:
    eight_entries = [
        {
            "work_date": f"2026-09-{day:02d}",
            "hours": 1,
            "project_code": "ATLAS",
        }
        for day in range(1, 9)
    ]
    too_many = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token_for()),
        json={
            "period_start": "2026-09-01",
            "period_end": "2026-09-08",
            "entries": eight_entries,
        },
    )
    assert too_many.status_code == 422

    duplicate_dates = client.post(
        "/api/v1/time/timesheets",
        headers=bearer(token_for()),
        json={
            "period_start": "2026-09-01",
            "period_end": "2026-09-07",
            "entries": [
                {"work_date": "2026-09-01", "hours": 8, "project_code": "ATLAS"},
                {"work_date": "2026-09-01", "hours": 2, "project_code": "OPS"},
            ],
        },
    )
    assert duplicate_dates.status_code == 422


def test_standard_week_hours_is_validated_and_used(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValidationError):
        Settings(standard_week_hours=Decimal("0"))
    with pytest.raises(ValidationError):
        Settings(standard_week_hours=Decimal("168.01"))

    monkeypatch.setattr(settings, "standard_week_hours", Decimal("37.50"))
    created = create_timesheet(
        client,
        token_for(),
        entries=[
            {
                "work_date": f"2026-09-{day:02d}",
                "hours": 8,
                "project_code": "ATLAS",
            }
            for day in range(1, 6)
        ],
    )

    async def ignore_publish(*_: object) -> None:
        return None

    monkeypatch.setattr("app.api.timesheets.publish", ignore_publish)
    response = client.post(
        f"/api/v1/time/timesheets/{created['id']}/submit",
        headers=bearer(token_for()),
    )
    assert response.status_code == 202
    assert response.json()["total_hours"] == 40
    assert response.json()["overtime_hours"] == 2.5


def test_time_entries_cascade_when_timesheet_is_deleted(db: Session) -> None:
    timesheet = Timesheet(
        employee_id=uuid.UUID(ADA_ID),
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 7),
    )
    timesheet.entries.append(
        TimeEntry(
            work_date=date(2026, 9, 1),
            hours=Decimal("8"),
            project_code="ATLAS",
        )
    )
    db.add(timesheet)
    db.commit()
    db.delete(timesheet)
    db.commit()
    assert db.scalar(select(func.count()).select_from(TimeEntry)) == 0


def test_openapi_contains_time_surface(client: TestClient) -> None:
    specification = client.get("/openapi.json").json()
    paths = set(specification["paths"])
    assert {
        "/api/v1/time/healthz",
        "/api/v1/time/timesheets",
        "/api/v1/time/timesheets/{timesheet_id}",
        "/api/v1/time/timesheets/{timesheet_id}/submit",
        "/api/v1/time/pto/balance",
        "/api/v1/time/profiles/{employee_id}",
    } <= paths
    assert specification["paths"]["/api/v1/time/healthz"]["get"]["operationId"] == "timeHealth"
    assert (
        specification["paths"]["/api/v1/time/timesheets"]["get"]["operationId"] == "listTimesheets"
    )
    assert (
        specification["paths"]["/api/v1/time/timesheets"]["post"]["operationId"]
        == "createTimesheet"
    )
    assert (
        specification["paths"]["/api/v1/time/timesheets/{timesheet_id}"]["get"]["operationId"]
        == "getTimesheet"
    )
    assert (
        specification["paths"]["/api/v1/time/timesheets/{timesheet_id}"]["put"]["operationId"]
        == "updateTimesheet"
    )
    assert (
        specification["paths"]["/api/v1/time/timesheets/{timesheet_id}/submit"]["post"][
            "operationId"
        ]
        == "submitTimesheet"
    )
    assert (
        specification["paths"]["/api/v1/time/pto/balance"]["get"]["operationId"] == "getPtoBalance"
    )
    assert (
        specification["paths"]["/api/v1/time/profiles/{employee_id}"]["get"]["operationId"]
        == "getTimeProfile"
    )
    schemas = specification["components"]["schemas"]
    assert schemas["TimesheetCreate"]["properties"]["entries"]["maxItems"] == 7
    assert schemas["TimesheetUpdate"]["properties"]["entries"]["maxItems"] == 7


async def test_handle_timesheet_approved_finalizes_pending_timesheet(consumer_db: Session) -> None:
    timesheet = Timesheet(
        employee_id=uuid.UUID(ADA_ID),
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 7),
        status="PENDING_APPROVAL",
    )
    consumer_db.add(timesheet)
    consumer_db.commit()

    await handle_timesheet_approved({"data": {"timesheet_id": str(timesheet.id)}})

    consumer_db.refresh(timesheet)
    assert timesheet.status == "APPROVED"


async def test_handle_timesheet_rejected_finalizes_pending_timesheet(consumer_db: Session) -> None:
    timesheet = Timesheet(
        employee_id=uuid.UUID(ADA_ID),
        period_start=date(2026, 10, 8),
        period_end=date(2026, 10, 14),
        status="PENDING_APPROVAL",
    )
    consumer_db.add(timesheet)
    consumer_db.commit()

    await handle_timesheet_rejected({"data": {"timesheet_id": str(timesheet.id)}})

    consumer_db.refresh(timesheet)
    assert timesheet.status == "REJECTED"


async def test_handle_timesheet_decision_is_idempotent_and_ignores_unknown_ids(
    consumer_db: Session,
) -> None:
    timesheet = Timesheet(
        employee_id=uuid.UUID(ADA_ID),
        period_start=date(2026, 10, 15),
        period_end=date(2026, 10, 21),
        status="REJECTED",
    )
    consumer_db.add(timesheet)
    consumer_db.commit()

    # Already finalized as REJECTED: a redelivered APPROVED decision is a no-op.
    await handle_timesheet_approved({"data": {"timesheet_id": str(timesheet.id)}})
    consumer_db.refresh(timesheet)
    assert timesheet.status == "REJECTED"

    # Unknown timesheet id: logged and ignored, no exception raised.
    await handle_timesheet_approved({"data": {"timesheet_id": str(uuid.uuid4())}})


async def test_handle_employee_created_provisions_new_row(
    consumer_db: Session,
) -> None:
    employee_id = "20000000-0000-4000-8000-000000000003"
    assert consumer_db.get(EmployeeRead, uuid.UUID(employee_id)) is None

    await handle_employee_created(
        {
            "type": "employee.created",
            "data": {
                "employee_id": employee_id,
                "email": "new.hire@atlas.dev",
                "full_name": "New Hire",
                "cost_center": "CC-200",
                "manager_id": GRACE_ID,
                "home_currency": "USD",
                "pto_entitlement_days": 20,
                "status": "ACTIVE",
            },
        }
    )

    projection = consumer_db.get(EmployeeRead, uuid.UUID(employee_id))
    assert projection is not None
    assert projection.pto_entitlement_days == 20
    assert projection.manager_id == uuid.UUID(GRACE_ID)
    assert projection.status == "ACTIVE"


async def test_handle_employee_created_is_idempotent_on_redelivery(
    consumer_db: Session,
) -> None:
    employee_id = "20000000-0000-4000-8000-000000000004"
    event = {
        "type": "employee.created",
        "data": {
            "employee_id": employee_id,
            "email": "redelivered@atlas.dev",
            "full_name": "Redelivered Hire",
            "cost_center": "CC-200",
            "manager_id": None,
            "home_currency": "USD",
            "pto_entitlement_days": 18,
            "status": "ACTIVE",
        },
    }
    await handle_employee_created(event)
    await handle_employee_created(event)

    count = db_count_employee_read(consumer_db, employee_id)
    assert count == 1


def db_count_employee_read(db: Session, employee_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(EmployeeRead)
            .where(EmployeeRead.id == uuid.UUID(employee_id))
        )
        or 0
    )


async def test_handle_employee_updated_applies_manager_and_entitlement_changes(
    consumer_db: Session,
) -> None:
    await handle_employee_updated(
        {
            "type": "employee.updated",
            "data": {
                "employee_id": ADA_ID,
                "changed": {"manager_id": None, "pto_entitlement_days": 30},
            },
        }
    )
    consumer_db.expire_all()
    projection = consumer_db.get(EmployeeRead, uuid.UUID(ADA_ID))
    assert projection is not None
    assert projection.manager_id is None
    assert projection.pto_entitlement_days == 30


async def test_handle_employee_updated_ignores_unknown_employee(
    consumer_db: Session,
) -> None:
    # No matching row and no exception: an update for an employee this
    # service hasn't seen yet (e.g. redelivery race) is a no-op.
    await handle_employee_updated(
        {
            "type": "employee.updated",
            "data": {"employee_id": str(uuid.uuid4()), "changed": {"pto_entitlement_days": 5}},
        }
    )


async def test_handle_employee_deactivated_marks_projection_inactive(
    consumer_db: Session,
) -> None:
    await handle_employee_deactivated(
        {"type": "employee.deactivated", "data": {"employee_id": ADA_ID}}
    )
    consumer_db.expire_all()
    projection = consumer_db.get(EmployeeRead, uuid.UUID(ADA_ID))
    assert projection is not None
    assert projection.status == "INACTIVE"

    # Unknown employee id: logged and ignored, no exception raised.
    await handle_employee_deactivated(
        {"type": "employee.deactivated", "data": {"employee_id": str(uuid.uuid4())}}
    )


def test_get_time_profile_returns_provisioned_projection(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/time/profiles/{ADA_ID}",
        headers=bearer(token_for(roles=["HR_ADMIN"])),
    )
    assert response.status_code == 200
    assert response.json() == {
        "employee_id": ADA_ID,
        "status": "ACTIVE",
        "manager_id": GRACE_ID,
        "pto_entitlement_days": 22,
    }


def test_get_time_profile_404s_when_not_yet_provisioned(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/time/profiles/{uuid.uuid4()}",
        headers=bearer(token_for(roles=["HR_ADMIN"])),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TIME_PROFILE_NOT_FOUND"


def test_get_time_profile_requires_hr_admin(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/time/profiles/{ADA_ID}",
        headers=bearer(token_for(roles=["EMPLOYEE"])),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TIME_FORBIDDEN"
