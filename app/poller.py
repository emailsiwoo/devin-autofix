from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.devin_client import get_session
from app.models import SessionStatus, store

logger = logging.getLogger("autofix.poller")

# Map Devin API status strings to our internal enum
_STATUS_MAP: dict[str, SessionStatus] = {
    "running": SessionStatus.running,
    "finished": SessionStatus.succeeded,
    "stopped": SessionStatus.failed,
    "failed": SessionStatus.failed,
    "blocked": SessionStatus.running,
}


async def _poll_once() -> None:
    active = store.active()
    if not active:
        return
    logger.info("Polling %d active session(s)…", len(active))
    for tracked in active:
        try:
            data = await get_session(tracked.devin_session_id)
            new_status = _STATUS_MAP.get(data.get("status_enum", ""), SessionStatus.unknown)

            # Detect PR URL from structured_output or pull_request fields
            pr_url = tracked.pr_url
            structured = data.get("structured_output") or {}
            if isinstance(structured, dict):
                pr_url = pr_url or structured.get("pull_request_url")
            pr_url = pr_url or data.get("pull_request", {}).get("url")

            success: bool | None = None
            if new_status == SessionStatus.succeeded:
                success = True
            elif new_status == SessionStatus.failed:
                success = False

            store.update(
                tracked.devin_session_id,
                status=new_status,
                pr_url=pr_url,
                success=success,
            )
            if pr_url and pr_url != tracked.pr_url:
                logger.info("PR detected for issue #%s: %s", tracked.issue_number, pr_url)
            if new_status != tracked.status:
                logger.info(
                    "Session %s status: %s → %s",
                    tracked.devin_session_id,
                    tracked.status.value,
                    new_status.value,
                )
        except Exception:
            logger.exception("Error polling session %s", tracked.devin_session_id)


async def poll_loop() -> None:
    """Continuously poll active sessions on an interval."""
    logger.info("Background poller started (interval=%ds)", settings.poll_interval_seconds)
    while True:
        await _poll_once()
        await asyncio.sleep(settings.poll_interval_seconds)
