from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import settings
from app.models import SessionStatus, store
from app.poller import poll_loop
from app.webhook import router as webhook_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-22s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autofix")


# ---------------------------------------------------------------------------
# Lifespan — start the background poller
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(poll_loop())
    logger.info("Devin Autofix service started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Devin Autofix Service",
    description="Automated GitHub issue remediation powered by Devin",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)


# ---------------------------------------------------------------------------
# Observability endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/report")
async def report() -> dict[str, object]:
    all_sessions = store.all()
    total = len(all_sessions)
    active = len(store.active())
    succeeded = sum(1 for s in all_sessions if s.status == SessionStatus.succeeded)
    failed = sum(1 for s in all_sessions if s.status == SessionStatus.failed)
    prs = [s.pr_url for s in all_sessions if s.pr_url]
    return {
        "target_repo": settings.target_repo,
        "trigger_label": settings.trigger_label,
        "total_sessions": total,
        "active_sessions": active,
        "succeeded": succeeded,
        "failed": failed,
        "pull_requests": prs,
        "healthy": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/sessions")
async def list_sessions() -> list[dict[str, object]]:
    return [s.model_dump() for s in store.all()]


@app.get("/sessions/active")
async def list_active_sessions() -> list[dict[str, object]]:
    return [s.model_dump() for s in store.active()]


@app.get("/sessions/{session_id}")
async def get_session_detail(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    if session is None:
        return {"error": "session not found"}
    return session.model_dump()
