import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.auth import Claims, require
from ..core.errors import AppError
from ..db import get_db
from ..models import EmployeeRead
from ..schemas import ProfileResponse

router = APIRouter(prefix="/api/v1/time/profiles", tags=["Profiles"])


@router.get("/{employee_id}", response_model=ProfileResponse, operation_id="getTimeProfile")
def get_profile(
    employee_id: uuid.UUID,
    _: Claims = Depends(require("HR_ADMIN")),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    employee = db.get(EmployeeRead, employee_id)
    if employee is None:
        raise AppError(404, "TIME_PROFILE_NOT_FOUND", "Time profile not yet provisioned.")
    return ProfileResponse(
        employee_id=employee.id,
        status=employee.status,
        manager_id=employee.manager_id,
        pto_entitlement_days=employee.pto_entitlement_days,
    )
