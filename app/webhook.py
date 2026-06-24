from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.devin_client import create_session
from app.models import SessionStatus, TrackedSession, store

logger = logging.getLogger("autofix.webhook")
router = APIRouter()


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not settings.github_webhook_secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _build_prompt(issue: dict[str, Any]) -> str:
    return (
        f"A GitHub issue needs investigation and a fix.\n\n"
        f"Repository: {settings.target_repo}\n"
        f"Issue #{issue['number']}: {issue['title']}\n"
        f"URL: {issue['html_url']}\n\n"
        f"Issue body:\n{issue.get('body') or '(no body)'}\n\n"
        f"Instructions:\n"
        f"1. Clone https://github.com/{settings.target_repo}\n"
        f"2. Investigate the issue described above.\n"
        f"3. Implement a fix with appropriate tests.\n"
        f"4. Validate the change (lint, typecheck, tests).\n"
        f"5. Open a pull request back to {settings.target_repo} referencing issue #{issue['number']}."
    )


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(""),
    x_github_event: str = Header(""),
) -> dict[str, str]:
    body = await request.body()

    # --- signature verification ---
    if not verify_signature(body, x_hub_signature_256):
        logger.warning("Invalid webhook signature — rejecting request")
        raise HTTPException(status_code= 401, detail="Invalid signature")

    payload: dict[str, Any] = await request.json()
    action = payload.get("action", "")
    logger.info("Webhook received: event=%s action=%s", x_github_event, action)

    # We only care about issues being labeled
    if x_github_event != "issues" or action != "labeled":
        logger.info("Ignoring event (event=%s, action=%s)", x_github_event, action)
        return {"status": "ignored", "reason": "not a label event"}

    label_name = payload.get("label", {}).get("name", "")
    if label_name != settings.trigger_label:
        logger.info("Ignoring label '%s' (trigger_label=%s)", label_name, settings.trigger_label)
        return {"status": "ignored", "reason": f"label '{label_name}' is not trigger label"}

    issue: dict[str, Any] = payload.get("issue", {})
    issue_number = issue.get("number")
    issue_title = issue.get("title", "")
    issue_url = issue.get("html_url", "")

    logger.info("Issue accepted: #%s — %s", issue_number, issue_title)

    # Check for duplicate — don't re-trigger for the same issue
    for s in store.all():
        if s.issue_number == issue_number and s.status in (SessionStatus.pending, SessionStatus.running):
            logger.info("Session already active for issue #%s — skipping", issue_number)
            return {"status": "skipped", "reason": "session already active for this issue"}

    # Create Devin session
    try:
        prompt = _build_prompt(issue)
        result = await create_session(prompt)
        session_id = result["session_id"]
        session_url = result.get("url", f"https://app.devin.ai/sessions/{session_id}")

        tracked = TrackedSession(
            issue_number=issue_number,
            issue_title=issue_title,
            issue_url=issue_url,
            devin_session_id=session_id,
            devin_session_url=session_url,
            status=SessionStatus.running,
        )
        store.add(tracked)
        logger.info("Devin session created: id=%s url=%s", session_id, session_url)
        return {"status": "created", "session_id": session_id, "session_url": session_url}

    except Exception as exc:
        logger.exception("Failed to create Devin session for issue #%s: %s", issue_number, exc)
        raise HTTPException(status_code=502, detail=f"Failed to create Devin session: {exc}") from exc
