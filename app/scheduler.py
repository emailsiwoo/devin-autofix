from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.devin_client import create_session
from app.models import SessionStatus, TrackedSession, store

logger = logging.getLogger("autofix.scheduler")

GITHUB_API = "https://api.github.com"


async def _fetch_dependabot_alerts() -> list[dict]:
    """Fetch open critical/high Dependabot alerts from the target repo."""
    url = f"{GITHUB_API}/repos/{settings.target_repo}/dependabot/alerts"
    params = {"state": "open", "severity": "critical,high", "per_page": "25"}
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 403:
                logger.warning("Dependabot alerts API returned 403 — token may lack permissions")
                return []
            if resp.status_code == 404:
                logger.warning("Dependabot alerts not enabled for %s", settings.target_repo)
                return []
            resp.raise_for_status()
            alerts: list[dict] = resp.json()
            return alerts
    except Exception:
        logger.exception("Failed to fetch Dependabot alerts")
        return []


def _build_vuln_prompt(alerts: list[dict]) -> str:
    lines = [
        f"Repository: {settings.target_repo}",
        f"Task: Fix the following critical/high vulnerabilities.\n",
    ]
    for alert in alerts:
        pkg = alert.get("dependency", {}).get("package", {})
        adv = alert.get("security_advisory", {})
        vuln = alert.get("security_vulnerability", {})
        lines.append(
            f"- {pkg.get('ecosystem', '?')}/{pkg.get('name', '?')} "
            f"(severity: {adv.get('severity', 'unknown')}): "
            f"{adv.get('summary', 'no summary')} "
            f"[fix: {vuln.get('first_patched_version', {}).get('identifier', 'unknown')}]"
        )
    lines.append(
        f"\nInstructions:\n"
        f"1. Clone https://github.com/{settings.target_repo}\n"
        f"2. Upgrade the affected dependencies to their patched versions.\n"
        f"3. Run tests to validate nothing is broken.\n"
        f"4. Open a pull request titled 'fix: security dependency upgrades' "
        f"back to {settings.target_repo}."
    )
    return "\n".join(lines)


def _build_dependency_upgrade_prompt() -> str:
    return (
        f"Repository: {settings.target_repo}\n"
        f"Task: Check for outdated dependencies and upgrade the most critical ones.\n\n"
        f"Instructions:\n"
        f"1. Clone https://github.com/{settings.target_repo}\n"
        f"2. Identify outdated dependencies (pip-audit, npm audit, or equivalent).\n"
        f"3. Focus on dependencies with known security vulnerabilities or major version upgrades.\n"
        f"4. Upgrade them, run tests, and ensure nothing is broken.\n"
        f"5. Open a pull request titled 'chore: dependency upgrades' "
        f"back to {settings.target_repo}."
    )


async def _create_scan_session(prompt: str, title: str) -> bool:
    """Create a Devin session for a scheduled scan and track it. Returns True on success."""
    try:
        result = await create_session(prompt)
        session_id = result["session_id"]
        session_url = result.get("url", f"https://app.devin.ai/sessions/{session_id}")
        tracked = TrackedSession(
            issue_number=0,
            issue_title=title,
            issue_url="",
            devin_session_id=session_id,
            devin_session_url=session_url,
            status=SessionStatus.running,
        )
        store.add(tracked)
        logger.info("Scheduled scan session created: id=%s title=%s", session_id, title)
        return True
    except Exception:
        logger.exception("Failed to create scheduled scan session: %s", title)
        return False


async def run_daily_scan() -> dict[str, str]:
    """Run the daily vulnerability and dependency scan. Returns a summary."""
    logger.info("Starting daily vulnerability & dependency scan for %s", settings.target_repo)

    # 1. Check Dependabot alerts for critical/high vulnerabilities
    alerts = await _fetch_dependabot_alerts()
    critical_alerts = [
        a for a in alerts if a.get("security_advisory", {}).get("severity") == "critical"
    ]
    high_alerts = [a for a in alerts if a.get("security_advisory", {}).get("severity") == "high"]

    vuln_session_created = False
    if alerts:
        logger.info(
            "Found %d critical and %d high vulnerability alerts",
            len(critical_alerts),
            len(high_alerts),
        )
        prompt = _build_vuln_prompt(alerts)
        vuln_session_created = await _create_scan_session(
            prompt, f"[scheduled] Fix {len(alerts)} vulnerability alert(s)"
        )
    else:
        logger.info("No critical/high Dependabot alerts found")

    # 2. Always run a general dependency upgrade check
    dep_prompt = _build_dependency_upgrade_prompt()
    dep_session_created = await _create_scan_session(
        dep_prompt, "[scheduled] Daily dependency upgrade check"
    )

    summary = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "critical_alerts": len(critical_alerts),
        "high_alerts": len(high_alerts),
        "vuln_session_created": vuln_session_created,
        "dependency_session_created": dep_session_created,
    }
    logger.info("Daily scan complete: %s", summary)
    return summary


def _seconds_until(hour: int, minute: int) -> float:
    """Seconds from now until the next occurrence of HH:MM UTC."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target.replace(day=target.day + 1)
    return (target - now).total_seconds()


async def scheduler_loop() -> None:
    """Run the daily scan at the configured time (default 08:00 UTC)."""
    logger.info(
        "Scheduler started — daily scan at %02d:%02d UTC",
        settings.scan_hour_utc,
        settings.scan_minute_utc,
    )
    while True:
        wait = _seconds_until(settings.scan_hour_utc, settings.scan_minute_utc)
        logger.info("Next scheduled scan in %.0f seconds (%.1f hours)", wait, wait / 3600)
        await asyncio.sleep(wait)
        await run_daily_scan()
