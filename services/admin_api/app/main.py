from common.logging import configure_json_logging
from fastapi import FastAPI

configure_json_logging()

app = FastAPI(title="TradeMind Admin API")


@app.get("/health")
async def health() -> dict:
    """Liveness probe (PROJECT.md Section 11) — no auth, no dependency
    checks. The remaining admin_api routes (status, signals, decisions,
    kill switch, config) are Phase 4 scope."""
    return {"status": "ok"}
