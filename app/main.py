import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.profiles import router as profiles_router
from .api.pto import router as pto_router
from .api.timesheets import router as timesheets_router
from .consumers import BINDINGS
from .core.config import settings
from .core.errors import AppError
from .core.middleware import CorrelationIdMiddleware
from .events import consume


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    consumer = asyncio.create_task(consume(BINDINGS))
    yield
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)


app = FastAPI(title="Atlas Time API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)
app.include_router(timesheets_router)
app.include_router(pto_router)
app.include_router(profiles_router)


def correlation_id(request: Request) -> str:
    return cast(str, request.state.correlation_id)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "correlation_id": correlation_id(request),
                "details": details or [],
            }
        },
        headers={"X-Correlation-Id": correlation_id(request)},
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]),
            "issue": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return error_response(
        request, 422, "TIME_VALIDATION_ERROR", "Request validation failed.", details
    )


@app.get("/api/v1/time/healthz", tags=["Health"], operation_id="timeHealth")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/healthz", include_in_schema=False)
async def internal_health() -> dict[str, str]:
    return await health()
