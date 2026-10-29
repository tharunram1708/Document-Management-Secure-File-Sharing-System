import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.core.config import settings

logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(api_router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info("request complete request_id=%s method=%s path=%s status=%d duration_ms=%.2f", request_id, request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"error": {"code": exc.status_code, "message": exc.detail, "request_id": getattr(request.state, "request_id", None)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    issues = [{"field": ".".join(str(part) for part in issue["loc"]), "message": issue["msg"], "type": issue["type"]} for issue in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": 422, "message": "Request validation failed", "details": issues, "request_id": getattr(request.state, "request_id", None)}})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("unhandled request error path=%s", request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"error": {"code": 500, "message": "Internal server error", "request_id": getattr(request.state, "request_id", None)}})


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
