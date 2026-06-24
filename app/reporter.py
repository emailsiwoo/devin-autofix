from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.models import SessionStatus, TrackedSession, store

logger = logging.getLogger("autofix.reporter")

GITHUB_API = "https://api.github.com"

_STATUS_EMOJI = {
    SessionStatus.succeeded: "\u2705",  # green check
    SessionStatus.failed: "\u274c",  # red X
    SessionStatus.running: "\u23f3",  # hourglass
    SessionStatus.pending: "\u23f3",  # hourglass
    SessionStatus.unknown: "\u2753",  # question mark
}


def build_report_markdown() -> str:
    """Build a markdown status report from all tracked sessions."""
    all_sessions = store.all()
    total = len(all_sessions)
    succeeded = [s for s in all_sessions if s.status == SessionStatus.succeeded]
    failed = [s for s in all_sessions if s.status == SessionStatus.failed]
    running = [
        s for s in all_sessions if s.status in (SessionStatus.running, SessionStatus.pending)
    ]
    prs_waiting = [s for s in all_sessions if s.pr_url and s.status == SessionStatus.succeeded]
    completed = len(succeeded) + len(failed)
    completion_pct = round((completed / total) * 100, 1) if total > 0 else 0.0
    success_rate = round((len(succeeded) / completed) * 100, 1) if completed > 0 else 0.0

    lines: list[str] = []
    lines.append("## \U0001f4cb Devin Autofix — Status Report")
    lines.append("")
    lines.append(f"> **Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"> **Target repo:** `{settings.target_repo}`")
    lines.append(f"> **Progress:** {completion_pct}% complete ({completed}/{total} sessions)")
    if completed > 0:
        lines.append(f"> **Success rate:** {success_rate}%")
    lines.append("")

    # BLUF summary
    lines.append("### Bottom Line")
    lines.append("")
    if total == 0:
        lines.append("No autofix sessions tracked yet.")
    else:
        parts: list[str] = []
        if succeeded:
            parts.append(f"\u2705 **{len(succeeded)} fixed**")
        if running:
            parts.append(f"\u23f3 **{len(running)} still running**")
        if failed:
            parts.append(f"\u274c **{len(failed)} failed**")
        if prs_waiting:
            parts.append(f"\U0001f50d **{len(prs_waiting)} PRs waiting for review**")
        lines.append(" | ".join(parts))
    lines.append("")

    # What was fixed?
    if succeeded:
        lines.append("### \u2705 What Was Fixed")
        lines.append("")
        for s in succeeded:
            pr_link = f" — [PR]({s.pr_url})" if s.pr_url else ""
            issue_ref = (
                f"[#{s.issue_number}]({s.issue_url})" if s.issue_url else f"#{s.issue_number}"
            )
            lines.append(f"- {issue_ref} **{s.issue_title}**{pr_link}")
        lines.append("")

    # What is still running?
    if running:
        lines.append("### \u23f3 What Is Still Running")
        lines.append("")
        for s in running:
            issue_ref = (
                f"[#{s.issue_number}]({s.issue_url})" if s.issue_url else f"#{s.issue_number}"
            )
            session_link = f"[Devin session]({s.devin_session_url})"
            lines.append(f"- {issue_ref} **{s.issue_title}** — {session_link}")
        lines.append("")

    # What failed?
    if failed:
        lines.append("### \u274c What Failed")
        lines.append("")
        for s in failed:
            issue_ref = (
                f"[#{s.issue_number}]({s.issue_url})" if s.issue_url else f"#{s.issue_number}"
            )
            session_link = f"[Devin session]({s.devin_session_url})"
            lines.append(f"- {issue_ref} **{s.issue_title}** — {session_link}")
        lines.append("")

    # What PRs are waiting for review?
    all_prs = [s for s in all_sessions if s.pr_url]
    if all_prs:
        lines.append("### \U0001f50d PRs Waiting for Review")
        lines.append("")
        for s in all_prs:
            emoji = _STATUS_EMOJI.get(s.status, "\u2753")
            issue_ref = f"#{s.issue_number}" if s.issue_number else "scheduled scan"
            lines.append(f"- {emoji} [{s.pr_url}]({s.pr_url}) (from {issue_ref}: {s.issue_title})")
        lines.append("")

    # Session detail table
    lines.append("### Session Details")
    lines.append("")
    lines.append("| Status | Issue | Title | Devin Session | PR |")
    lines.append("|--------|-------|-------|---------------|-----|")
    for s in all_sessions:
        emoji = _STATUS_EMOJI.get(s.status, "\u2753")
        issue_ref = f"[#{s.issue_number}]({s.issue_url})" if s.issue_url else f"#{s.issue_number}"
        session_link = f"[View]({s.devin_session_url})"
        pr = f"[PR]({s.pr_url})" if s.pr_url else "-"
        title_short = s.issue_title[:50] + ("..." if len(s.issue_title) > 50 else "")
        lines.append(
            f"| {emoji} {s.status.value} | {issue_ref} | {title_short} | {session_link} | {pr} |"
        )
    lines.append("")

    return "\n".join(lines)


async def post_comment_to_issue(issue_number: int, body: str) -> bool:
    """Post a comment on a GitHub issue. Returns True on success."""
    if not settings.github_token:
        logger.warning("GITHUB_TOKEN not set — cannot post comment to issue #%d", issue_number)
        return False

    url = f"{GITHUB_API}/repos/{settings.target_repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"body": body}, headers=headers)
            if resp.status_code == 201:
                logger.info("Posted report comment to issue #%d", issue_number)
                return True
            logger.warning(
                "Failed to post comment to issue #%d: HTTP %d", issue_number, resp.status_code
            )
            return False
    except Exception:
        logger.exception("Error posting comment to issue #%d", issue_number)
        return False


async def post_comment_to_pr(pr_url: str, body: str) -> bool:
    """Post a comment on a GitHub PR (identified by its full URL). Returns True on success."""
    if not settings.github_token:
        logger.warning("GITHUB_TOKEN not set — cannot post comment to PR %s", pr_url)
        return False

    # Extract owner/repo/number from URL like https://github.com/owner/repo/pull/123
    try:
        parts = pr_url.rstrip("/").split("/")
        pr_number = parts[-1]
        repo = f"{parts[-4]}/{parts[-3]}"
    except (IndexError, ValueError):
        logger.error("Could not parse PR URL: %s", pr_url)
        return False

    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"body": body}, headers=headers)
            if resp.status_code == 201:
                logger.info("Posted report comment to PR %s", pr_url)
                return True
            logger.warning("Failed to post comment to PR %s: HTTP %d", pr_url, resp.status_code)
            return False
    except Exception:
        logger.exception("Error posting comment to PR %s", pr_url)
        return False


async def post_report_to_github() -> dict[str, Any]:
    """Post the full status report to all tracked issues and PRs on GitHub."""
    report_md = build_report_markdown()
    results: dict[str, Any] = {
        "report_posted": False,
        "issues_commented": [],
        "prs_commented": [],
        "errors": [],
    }

    all_sessions = store.all()

    # Post to each issue that has a real issue number
    seen_issues: set[int] = set()
    for s in all_sessions:
        if s.issue_number > 0 and s.issue_number not in seen_issues:
            seen_issues.add(s.issue_number)
            ok = await post_comment_to_issue(s.issue_number, report_md)
            if ok:
                results["issues_commented"].append(s.issue_number)
            else:
                results["errors"].append(f"issue #{s.issue_number}")

    # Post to each PR
    seen_prs: set[str] = set()
    for s in all_sessions:
        if s.pr_url and s.pr_url not in seen_prs:
            seen_prs.add(s.pr_url)
            ok = await post_comment_to_pr(s.pr_url, report_md)
            if ok:
                results["prs_commented"].append(s.pr_url)
            else:
                results["errors"].append(f"PR {s.pr_url}")

    results["report_posted"] = bool(results["issues_commented"] or results["prs_commented"])
    return results


async def report_session_completion(session: TrackedSession) -> None:
    """Post a completion comment on the issue when a Devin session finishes."""
    if session.issue_number <= 0:
        return

    emoji = _STATUS_EMOJI.get(session.status, "\u2753")
    status_text = "completed successfully" if session.success else "failed"
    lines = [
        f"## {emoji} Devin Autofix — Session {status_text.title()}",
        "",
        f"**Issue:** #{session.issue_number} — {session.issue_title}",
        f"**Status:** {session.status.value}",
        f"**Devin session:** {session.devin_session_url}",
    ]
    if session.pr_url:
        lines.append(f"**Pull request:** {session.pr_url}")
    lines.append("")

    if session.success:
        lines.append(
            "The fix has been implemented and a PR is ready for review."
            if session.pr_url
            else "The session completed successfully."
        )
    else:
        lines.append(
            "The session did not complete successfully. Check the Devin session for details."
        )

    body = "\n".join(lines)
    await post_comment_to_issue(session.issue_number, body)
