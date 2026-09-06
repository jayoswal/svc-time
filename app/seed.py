import uuid
from typing import TypedDict

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import EmployeeRead, EmployeeStatus


class DemoEmployee(TypedDict):
    id: uuid.UUID
    manager_id: uuid.UUID | None
    pto_entitlement_days: int
    roles: tuple[str, ...]


DEMO_EMPLOYEES: tuple[DemoEmployee, ...] = (
    {
        "id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
        "manager_id": None,
        "pto_entitlement_days": 25,
        "roles": ("EMPLOYEE", "MANAGER"),
    },
    {
        "id": uuid.UUID("10000000-0000-4000-8000-000000000002"),
        "manager_id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
        "pto_entitlement_days": 22,
        "roles": ("EMPLOYEE",),
    },
    {
        "id": uuid.UUID("10000000-0000-4000-8000-000000000003"),
        "manager_id": None,
        "pto_entitlement_days": 25,
        "roles": ("EMPLOYEE", "FINANCE"),
    },
    {
        "id": uuid.UUID("10000000-0000-4000-8000-000000000004"),
        "manager_id": None,
        "pto_entitlement_days": 25,
        "roles": ("EMPLOYEE", "HR_ADMIN"),
    },
)


def seed_demo_data(db: Session) -> None:
    for values in DEMO_EMPLOYEES:
        employee = db.get(EmployeeRead, values["id"])
        if employee is not None:
            continue
        db.add(
            EmployeeRead(
                id=values["id"],
                status=EmployeeStatus.ACTIVE,
                manager_id=values["manager_id"],
                pto_entitlement_days=values["pto_entitlement_days"],
                roles=list(values["roles"]),
            )
        )
    db.commit()


def main() -> None:
    with SessionLocal() as db:
        seed_demo_data(db)
    print(f"Seeded {len(DEMO_EMPLOYEES)} time-service employee records.")


if __name__ == "__main__":
    main()
