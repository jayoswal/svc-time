import uuid
from collections.abc import Callable
from typing import TypedDict, cast

import jwt
from fastapi import Depends, Header

from .config import settings
from .errors import AppError


class Claims(TypedDict):
    sub: str
    roles: list[str]
    mgr: str | None
    email: str
    exp: int
    iat: int


def current_user(authorization: str = Header(default="")) -> Claims:
    if not authorization.startswith("Bearer "):
        raise AppError(401, "UNAUTHENTICATED", "Missing bearer token.")
    try:
        payload = cast(
            dict[str, object],
            jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"]),
        )
    except jwt.PyJWTError as exc:
        raise AppError(401, "UNAUTHENTICATED", "Invalid or expired token.") from exc

    sub = payload.get("sub")
    roles = payload.get("roles")
    email = payload.get("email")
    manager = payload.get("mgr")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if (
        not isinstance(sub, str)
        or not isinstance(email, str)
        or not isinstance(roles, list)
        or not all(isinstance(role, str) for role in roles)
        or manager is not None
        and not isinstance(manager, str)
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
    ):
        raise AppError(401, "UNAUTHENTICATED", "Token claims are invalid.")
    try:
        employee_id = uuid.UUID(sub)
        manager_id = uuid.UUID(manager) if manager is not None else None
    except ValueError as exc:
        raise AppError(401, "UNAUTHENTICATED", "Token claims are invalid.") from exc
    return Claims(
        sub=str(employee_id),
        roles=cast(list[str], roles),
        mgr=str(manager_id) if manager_id is not None else None,
        email=email,
        iat=issued_at,
        exp=expires_at,
    )


def require(*roles: str) -> Callable[[Claims], Claims]:
    def dependency(user: Claims = Depends(current_user)) -> Claims:
        if roles and not set(roles).intersection(user["roles"]):
            raise AppError(403, "TIME_FORBIDDEN", "Insufficient role.")
        return user

    return dependency
