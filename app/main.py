from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.models import SessionStatus, store
from app.poller import poll_loop
from app.reporter import build_report_markdown, post_report_to_github
from app.scheduler import run_daily_scan, scheduler_loop
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
    poller_task = asyncio.create_task(poll_loop())
    scheduler_task = asyncio.create_task(scheduler_loop())
    logger.info("Devin Autofix service started")
    yield
    for task in (poller_task, scheduler_task):
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
# Scheduled scan endpoints
# ---------------------------------------------------------------------------
@app.post("/scan/trigger")
async def trigger_scan() -> dict[str, object]:
    """Manually trigger the daily vulnerability & dependency scan."""
    logger.info("Manual scan triggered via /scan/trigger")
    result = await run_daily_scan()
    return result


# ---------------------------------------------------------------------------
# Reporting endpoints
# ---------------------------------------------------------------------------
@app.post("/report/github")
async def publish_report_to_github() -> dict[str, object]:
    """Post the full status report as comments on all tracked GitHub issues and PRs."""
    logger.info("Publishing report to GitHub issues and PRs")
    result = await post_report_to_github()
    return result


@app.get("/report/markdown", response_class=PlainTextResponse)
async def report_markdown() -> str:
    """Return the status report as markdown (for pasting into PRs, issues, etc.)."""
    return build_report_markdown()


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
    active_list = store.active()
    active = len(active_list)
    succeeded = sum(1 for s in all_sessions if s.status == SessionStatus.succeeded)
    failed = sum(1 for s in all_sessions if s.status == SessionStatus.failed)
    pending = sum(1 for s in all_sessions if s.status == SessionStatus.pending)
    running = sum(1 for s in all_sessions if s.status == SessionStatus.running)
    unknown = sum(1 for s in all_sessions if s.status == SessionStatus.unknown)
    completed = succeeded + failed
    prs = [s.pr_url for s in all_sessions if s.pr_url]

    completion_pct = round((completed / total) * 100, 1) if total > 0 else 0.0
    success_rate = round((succeeded / completed) * 100, 1) if completed > 0 else 0.0

    # BLUF summary line
    if total == 0:
        bluf = "No sessions tracked yet."
    else:
        parts = [f"{total} sessions"]
        parts.append(f"{completion_pct}% complete")
        if succeeded:
            parts.append(f"{succeeded} succeeded")
        if failed:
            parts.append(f"{failed} failed")
        if active:
            parts.append(f"{active} active")
        bluf = " | ".join(parts)

    return {
        "summary": bluf,
        "target_repo": settings.target_repo,
        "trigger_label": settings.trigger_label,
        "completion_pct": completion_pct,
        "success_rate_pct": success_rate,
        "total_sessions": total,
        "status_breakdown": {
            "pending": pending,
            "running": running,
            "succeeded": succeeded,
            "failed": failed,
            "unknown": unknown,
        },
        "active_sessions": active,
        "completed_sessions": completed,
        "pull_requests_opened": len(prs),
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


# ---------------------------------------------------------------------------
# Dashboard — human-readable text report
# ---------------------------------------------------------------------------
_STATUS_ICON = {
    SessionStatus.succeeded: "[PASS]",
    SessionStatus.failed: "[FAIL]",
    SessionStatus.running: "[RUN]",
    SessionStatus.pending: "[WAIT]",
    SessionStatus.unknown: "[???]",
}


@app.get("/dashboard", response_class=PlainTextResponse)
async def dashboard() -> str:
    """Human-readable plain-text dashboard for quick status checks."""
    all_sessions = store.all()
    total = len(all_sessions)
    active = len(store.active())
    succeeded = sum(1 for s in all_sessions if s.status == SessionStatus.succeeded)
    failed = sum(1 for s in all_sessions if s.status == SessionStatus.failed)
    completed = succeeded + failed
    prs = [s for s in all_sessions if s.pr_url]
    completion_pct = round((completed / total) * 100, 1) if total > 0 else 0.0
    success_rate = round((succeeded / completed) * 100, 1) if completed > 0 else 0.0

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  DEVIN AUTOFIX — STATUS DASHBOARD")
    lines.append("=" * 60)
    lines.append("")

    # BLUF
    if total == 0:
        lines.append("  BOTTOM LINE: No sessions tracked yet.")
    else:
        lines.append(
            f"  BOTTOM LINE: {completion_pct}% complete | {succeeded} passed | {failed} failed | {active} active"
        )
        lines.append(f"  SUCCESS RATE: {success_rate}% of completed sessions")
    lines.append("")

    # Config
    lines.append("-" * 60)
    lines.append(f"  Target repo:    {settings.target_repo}")
    lines.append(f"  Trigger label:  {settings.trigger_label}")
    lines.append(
        f"  Scan schedule:  {settings.scan_hour_utc:02d}:{settings.scan_minute_utc:02d} UTC daily"
    )
    lines.append(
        f"  Polled at:      {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    lines.append("-" * 60)
    lines.append("")

    # Progress bar
    if total > 0:
        bar_len = 30
        filled = int(bar_len * completed / total)
        bar = "#" * filled + "-" * (bar_len - filled)
        lines.append(f"  PROGRESS: [{bar}] {completion_pct}% ({completed}/{total})")
        lines.append("")

    # Session table
    lines.append("  SESSIONS:")
    lines.append("  " + "-" * 56)
    lines.append(f"  {'STATUS':<8} {'ID':<14} {'TITLE':<30} {'PR':}")
    lines.append("  " + "-" * 56)

    if not all_sessions:
        lines.append("  (none)")
    else:
        for s in all_sessions:
            icon = _STATUS_ICON.get(s.status, "[???]")
            short_id = s.devin_session_id[-8:]
            title = s.issue_title[:28] + (".." if len(s.issue_title) > 28 else "")
            pr = "Yes" if s.pr_url else "-"
            lines.append(f"  {icon:<8} ..{short_id:<12} {title:<30} {pr}")

    lines.append("  " + "-" * 56)
    lines.append("")

    # PRs opened
    if prs:
        lines.append(f"  PULL REQUESTS ({len(prs)}):")
        for s in prs:
            lines.append(f"    - {s.pr_url}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines) + "\n"
