from __future__ import annotations

import os
import time
from collections import defaultdict

from fastapi import HTTPException, Request, Response
from starlette.responses import JSONResponse


MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "1048576"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMITS = {
    "default": int(os.getenv("RATE_LIMIT_DEFAULT", "200")),
    "admin": int(os.getenv("RATE_LIMIT_ADMIN", "10")),
}
RATE_LIMIT_BUCKETS: dict[tuple[str, str], list[int]] = defaultdict(list)


def check_rate_limit(client_key: str, route: str, limit: int | None = None, window_seconds: int | None = None) -> None:
    bucket_key = (client_key, route)
    now = int(time.time())
    window = window_seconds or RATE_LIMIT_WINDOW_SECONDS
    bucket = RATE_LIMIT_BUCKETS[bucket_key]
    bucket[:] = [stamp for stamp in bucket if now - stamp < window]
    max_requests = limit if limit is not None else RATE_LIMITS["admin" if route.startswith("/api/privacy/") or route.startswith("/api/knowledge/") else "default"]
    if len(bucket) >= max_requests:
        raise HTTPException(429, "Too many requests. Please try again later.")
    bucket.append(now)


async def apply_security_headers(request: Request, call_next) -> Response:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_BYTES:
        return JSONResponse({"detail": "Request body is too large."}, status_code=413)
    client_key = request.client.host if request.client else "unknown"
    if request.url.path.startswith("/api/"):
        try:
            check_rate_limit(client_key, request.url.path)
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if os.getenv("PUBLIC_BASE_URL", "").startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response