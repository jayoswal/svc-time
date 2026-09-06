import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.auth import Claims, require
from ..db import get_db
from ..models import EmployeeRead
from ..schemas import PtoBalance

router = APIRouter(prefix="/api/v1/time/pto", tags=["PTO"])


@router.get("/balance", response_model=PtoBalance, operation_id="getPtoBalance")
def get_pto_balance(
    claims: Claims = Depends(require("EMPLOYEE")), db: Session = Depends(get_db)
) -> PtoBalance:
    employee_id = uuid.UUID(claims["sub"])
    employee = db.get(EmployeeRead, employee_id)
    entitlement = Decimal(employee.pto_entitlement_days if employee is not None else 0)
    return PtoBalance(
        employee_id=employee_id,
        entitlement_days=entitlement,
        accrued_days=entitlement,
        taken_days=Decimal("0"),
        pending_days=Decimal("0"),
        balance_days=entitlement,
    )
