from __future__ import annotations

import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    
    async def dispatch(self, request: Request, call_next):
        # 1. Xóa context cũ
        clear_contextvars()

        # 2. Nhận hoặc sinh correlation ID
        raw_id = request.headers.get("x-request-id")
        if raw_id and raw_id.startswith("req-") and len(raw_id) == 12:
            correlation_id = raw_id
        else:
            correlation_id = f"req-{uuid.uuid4().hex[:8]}"

        # 3. Bind vào structlog
        bind_contextvars(correlation_id=correlation_id)

        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        response = await call_next(request)

        # 4. Trả header
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = correlation_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"

        return response

