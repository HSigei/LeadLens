from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request, Response

from call_center_integration import router as call_center_router
from dashboard import router as dashboard_router
from observability import bind_request_id, capture_exception, configure_logging, current_request_id, elapsed_ms, initialize_error_tracking, log_event, request_id_from_header, reset_request_id
from outbound_agent import router as outbound_agent_router
from security import apply_security_headers

configure_logging()
initialize_error_tracking()
app = FastAPI(title="Call Intelligence Agent", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(dashboard_router)
app.include_router(call_center_router)
app.include_router(outbound_agent_router)


@app.middleware("http")
async def security_middleware(request: Request, call_next) -> Response:
    return await apply_security_headers(request, call_next)


@app.middleware("http")
async def observability_middleware(request: Request, call_next) -> Response:
    request_id = request_id_from_header(request.headers.get("X-Request-ID"))
    token = bind_request_id(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as error:
        log_event("request.exception", logging.ERROR, method=request.method, path=request.url.path, duration_ms=elapsed_ms(started), error_type=type(error).__name__, error=str(error))
        capture_exception(error)
        raise
    else:
        response.headers["X-Request-ID"] = current_request_id()
        log_event("request.complete", method=request.method, path=request.url.path, status_code=response.status_code, duration_ms=elapsed_ms(started))
        return response
    finally:
        reset_request_id(token)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "call-intelligence-agent", "status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
