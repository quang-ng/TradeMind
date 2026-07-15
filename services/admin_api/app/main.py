import logging

from common.logging import configure_json_logging
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .routers import webhooks

configure_json_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="TradeMind Admin API")
app.include_router(webhooks.router)


@app.exception_handler(RequestValidationError)
async def _log_validation_errors(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Webhook payloads originate from an external process (Freqtrade); a
    shape mismatch should be visible in logs, not just a silent 422."""
    body = await request.body()
    errors = jsonable_encoder(exc.errors())
    logger.warning(
        "request_validation_failed",
        extra={"path": request.url.path, "errors": errors, "body": body.decode(errors="replace")},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


@app.get("/health")
async def health() -> dict:
    """Liveness probe (PROJECT.md Section 11) — no auth, no dependency
    checks. The remaining admin_api routes (status, signals, decisions,
    kill switch, config) are Phase 4 scope."""
    return {"status": "ok"}
