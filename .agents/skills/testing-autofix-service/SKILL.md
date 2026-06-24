---
name: testing-autofix-service
description: Test the Devin Autofix Service end-to-end. Use when verifying webhook handling, observability endpoints, scan scheduling, or Devin API integration changes.
---

# Testing the Devin Autofix Service

## Prerequisites

### Devin Secrets Needed
- `DEVIN_API_KEY` — Required for real Devin session creation. Without it, you can still test all service logic up to the API boundary (webhook filtering, signature verification, observability, error handling).
- `GITHUB_TOKEN` (optional) — Needed for Dependabot alerts API. Without it, the scan endpoint gracefully returns 0 alerts.
- `GITHUB_WEBHOOK_SECRET` (optional) — Needed for HMAC signature verification tests. Without it, the service skips verification.

### Environment
- Docker and Docker Compose must be available
- No GUI needed — all testing is shell-based (curl + docker compose)
- No screen recording needed

## Setup

1. Clone the repo and `cd` into it
2. Create `.env` file with at minimum:
   ```
   DEVIN_API_KEY=<your-key>
   TARGET_REPO=emailsiwoo/superset-demo
   TRIGGER_LABEL=devin-autofix
   ```
3. Build and start: `docker compose up --build -d`
4. Verify startup logs: `docker compose logs` should show:
   - "Devin Autofix service started"
   - "Background poller started"
   - "Scheduler started — daily scan at HH:MM UTC"
   - "Next scheduled scan in N seconds"

## Test Procedure

### 1. Observability Endpoints
```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# Expect: {"status": "ok", "timestamp": "<ISO>"}

curl -s http://localhost:8000/report | python3 -m json.tool
# Expect: JSON with these fields:
#   summary (string), completion_pct (float), success_rate_pct (float),
#   status_breakdown (object with keys: pending, running, succeeded, failed, unknown),
#   completed_sessions (int), pull_requests_opened (int),
#   total_sessions, active_sessions, pull_requests, healthy, timestamp
# Verify: sum of status_breakdown values == total_sessions
# Verify: completed_sessions == status_breakdown.succeeded + status_breakdown.failed
# Verify: completion_pct == round((completed_sessions / total_sessions) * 100, 1) if total > 0

curl -s http://localhost:8000/dashboard
# Expect: plain-text (Content-Type: text/plain) with:
#   - "DEVIN AUTOFIX — STATUS DASHBOARD" header
#   - "BOTTOM LINE:" with completion %, pass/fail counts, active count
#   - "PROGRESS:" with visual bar [####------] and percentage
#   - "SESSIONS:" table with STATUS/ID/TITLE/PR columns
#   - Status icons: [PASS]=succeeded, [FAIL]=failed, [RUN]=running, [WAIT]=pending, [???]=unknown
```

### 2. Webhook Filtering
```bash
# Non-issue event → ignored
curl -s -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"action":"push"}'
# Expect: {"status": "ignored", "reason": "not a label event"}

# Wrong label → ignored
curl -s -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -d '{"action":"labeled","label":{"name":"bug"},"issue":{"number":1,"title":"Test","html_url":"https://github.com/example/repo/issues/1","body":"test"}}'
# Expect: {"status": "ignored", "reason": "label 'bug' is not trigger label"}
```

### 3. Webhook with Trigger Label
```bash
curl -s -w "\nHTTP_CODE:%{http_code}" -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -d '{"action":"labeled","label":{"name":"devin-autofix"},"issue":{"number":42,"title":"Test issue","html_url":"https://github.com/example/repo/issues/42","body":"Test body"}}'
# With valid API key: Expect HTTP 200, {"status": "created", "session_id": "...", "session_url": "..."}
# With invalid API key: Expect HTTP 502, {"detail": "Failed to create Devin session: ..."}
```

### 4. Session Tracking (requires valid API key)
After a successful webhook trigger:
```bash
curl -s http://localhost:8000/sessions | python3 -m json.tool
# Expect: array with 1 element containing issue_number, devin_session_id, status: "running"

curl -s http://localhost:8000/sessions/active | python3 -m json.tool
# Expect: same session

curl -s http://localhost:8000/sessions/<session_id> | python3 -m json.tool
# Expect: full session detail
```

### 5. Duplicate Detection (requires valid API key)
Send the same webhook payload again for the same issue number:
```bash
# Expect: {"status": "skipped", "reason": "session already active for this issue"}
```

### 6. Scan Trigger
```bash
curl -s -X POST http://localhost:8000/scan/trigger | python3 -m json.tool
# Expect: critical_alerts: 0, high_alerts: 0 (without GITHUB_TOKEN)
# With valid API key: dependency_session_created: true (actually creates session)
# Without valid API key: dependency_session_created: false (fixed in PR #2)
```

### 7. Signature Verification
Restart container with `GITHUB_WEBHOOK_SECRET=testsecret123` in `.env`:
```bash
# Unsigned request → rejected
curl -s -w "\nHTTP_CODE:%{http_code}" -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"action":"push"}'
# Expect: HTTP 401, {"detail": "Invalid signature"}

# Valid HMAC → accepted
PAYLOAD='{"action":"push"}'
SIG=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "testsecret123" | awk '{print $2}')
curl -s -w "\nHTTP_CODE:%{http_code}" -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -d "$PAYLOAD"
# Expect: HTTP 200 (passes signature check, reaches event filtering)
```

## Known Issues / Gotchas

- **Invalid API key gives 502, not 500:** The webhook returns HTTP 502 (Bad Gateway) when the Devin API rejects the request. This is intentional — the service correctly propagates upstream failures.
- **Devin API may return unexpected status strings:** The API's `status_enum` field can contain values not in `_STATUS_MAP` (e.g., `'working'` was missing until PR #5). If you see `"Unmapped Devin status"` warnings in docker logs, add the missing value to `_STATUS_MAP` in `app/poller.py`. Without the mapping, sessions fall to `unknown`, drop out of active tracking, and break completion/success metrics.
- **Poller timing affects test results:** The poller runs on `poll_interval_seconds` (default 60s). After creating a session, it initially shows as `[RUN]` (running). After one poller cycle, the status may change depending on what the Devin API returns. If the API returns an unmapped status, the session reverts to `[???]` (unknown) and drops out of `active_sessions`.
- **Session status determines active tracking:** Only `pending` and `running` sessions are considered "active" (see `store.active()`). If status mapping fails, sessions won't be polled again in subsequent cycles because they're no longer active.
- **No GITHUB_TOKEN → Dependabot API returns 403/404:** The scan gracefully handles this by returning empty alerts. This is expected behavior, not an error.
- **Signature verification is skipped when GITHUB_WEBHOOK_SECRET is empty:** This is by design for development/testing, but means the service accepts any request in that mode.
- **Container might need a few seconds after restart before endpoints respond.** Add `sleep 2` after `docker compose restart` before curling.
- **Testing observability with varied states:** To fully test completion %, success rate, and dashboard indicators with non-trivial values, you need sessions in different states (succeeded, failed, running). This requires the Devin API to return mapped statuses. If all sessions are `unknown`, the math checks will trivially pass with 0% values.
- **Content-Type check for /dashboard:** Use `curl -s -D - http://localhost:8000/dashboard | head -5` to check headers. The `-I` (HEAD) method may return a different Content-Type than the actual GET response.

## Verifying Poller Status Mapping

After any change to `_STATUS_MAP` in `app/poller.py`, run this focused test:

1. Rebuild Docker: `docker compose down && docker compose up --build -d`
2. Create a session via webhook (use a unique issue number to avoid duplicate detection):
   ```bash
   curl -s -X POST http://localhost:8000/webhook/github \
     -H "Content-Type: application/json" -H "X-GitHub-Event: issues" \
     -d '{"action":"labeled","label":{"name":"devin-autofix"},"issue":{"number":9999,"title":"Status map test","html_url":"https://github.com/emailsiwoo/superset-demo/issues/9999","body":"test"}}'
   ```
3. Wait ~65 seconds for at least one poller cycle
4. Check assertions:
   - `docker logs <container> 2>&1 | grep -i "unmapped"` → should return nothing
   - `docker logs <container> 2>&1 | grep "Polling.*active"` → N should be >= 1
   - `curl -s http://localhost:8000/dashboard` → new session should show `[RUN]` not `[???]`
   - `curl -s http://localhost:8000/report` → `active_sessions >= 1`, `status_breakdown.running >= 1`
5. If any assertion fails, check the Devin API response for new/changed status strings and update `_STATUS_MAP`

## Testing GitHub Reporting (PR #7+)

### Endpoints
- `GET /report/markdown` — Returns full markdown status report
- `POST /report/github` — Posts report as comments on all tracked GitHub issues and PRs

### 8. Markdown Report Generation
```bash
# Check headers
curl -sI http://localhost:8000/report/markdown
# Expect: HTTP 200, Content-Type: text/plain; charset=utf-8

# Check content
curl -s http://localhost:8000/report/markdown
# Expect:
#   - Header: "## 📋 Devin Autofix — Status Report"
#   - Progress line: "**Progress:** X.X% complete (N/M sessions)"
#   - "### Bottom Line" section
#   - "### ⏳ What Is Still Running" — present if any sessions are running/pending
#   - "### ✅ What Was Fixed" — present only if succeeded sessions exist
#   - "### ❌ What Failed" — present only if failed sessions exist
#   - "### 🔍 PRs Waiting for Review" — present if any succeeded sessions have pr_url
#   - "### Session Details" table with one row per tracked session
```

### 9. GitHub Comment Posting (requires GITHUB_TOKEN)
```bash
# Post report to all tracked issues and PRs
curl -s -X POST http://localhost:8000/report/github | python3 -m json.tool
# Expect (with valid GITHUB_TOKEN):
#   report_posted: true
#   issues_commented: [list of issue numbers with issue_number > 0]
#   prs_commented: [list of PR URLs]
#   errors: [] (empty)
# NOTE: This endpoint takes ~25-30 seconds due to sequential GitHub API calls

# Verify comments actually exist on GitHub:
TOKEN=$(gh auth token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/<owner>/<repo>/issues/<N>/comments" | \
  python3 -c "import sys,json; c=json.load(sys.stdin); print(len(c), c[-1]['body'][:80] if c else 'NONE')"
# Expect: comment count increased, latest body contains "Devin Autofix — Status Report"
```

### 10. Graceful Degradation Without GITHUB_TOKEN
```bash
# Remove token and restart (MUST use down+up, not just restart — restart doesn't reload .env)
sed -i 's|^GITHUB_TOKEN=.*|GITHUB_TOKEN=|' .env
docker compose down && docker compose up -d
sleep 4

curl -s -X POST http://localhost:8000/report/github | python3 -m json.tool
# Expect:
#   HTTP 200 (not 500)
#   report_posted: false
#   issues_commented: [] (empty)
#   prs_commented: [] (empty)
#   errors: [one entry per issue + one per PR]

# Check logs for proper warnings (not crashes)
docker logs <container> 2>&1 | grep "GITHUB_TOKEN not set"
# Expect: warning lines, NO unhandled exceptions or tracebacks

# IMPORTANT: Restore token afterward
TOKEN=$(gh auth token)
sed -i "s|^GITHUB_TOKEN=.*|GITHUB_TOKEN=${TOKEN}|" .env
docker compose down && docker compose up -d
```

### 11. Poller Auto-Reporting Integration
The poller calls `report_session_completion()` when sessions transition to `succeeded` or `failed`.
```bash
# Verify import chain works in running container
docker exec <container> python3 -c "from app.poller import _poll_once; from app.reporter import report_session_completion; print('imports OK')"

# Verify code path exists
docker exec <container> python3 -c "
import inspect; from app.poller import _poll_once
src = inspect.getsource(_poll_once)
assert 'report_session_completion' in src, 'Missing call'
assert 'SessionStatus.succeeded' in src, 'Missing succeeded check'
assert 'SessionStatus.failed' in src, 'Missing failed check'
print('ALL CHECKS PASS')
"

# If sessions have transitioned, check for auto-report evidence
docker logs <container> 2>&1 | grep -E "(Posted report comment|completion report)"
```

### 12. Deduplication (Within-Call)
Each `POST /report/github` call posts exactly 1 comment per unique issue and 1 per unique PR, even if multiple sessions reference the same issue.
```bash
# Count comments BEFORE
BEFORE=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/<owner>/<repo>/issues/<N>/comments" | \
  python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

# Post twice
curl -s -X POST http://localhost:8000/report/github > /dev/null
curl -s -X POST http://localhost:8000/report/github > /dev/null

# Count comments AFTER
AFTER=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/<owner>/<repo>/issues/<N>/comments" | \
  python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

echo "New comments: $((AFTER - BEFORE))"
# Expect: exactly 2 (one per call — within-call dedup works)
# If >2: within-call dedup is broken (multiple sessions posted to same issue)
# If <2: posting silently failed
```

## Reporting Gotchas

- **`docker compose restart` does NOT reload .env:** You must use `docker compose down && docker compose up` to pick up env var changes. This is standard Docker behavior but easily forgotten during graceful-degradation tests.
- **POST /report/github is slow (~25-30s):** It makes sequential HTTP calls to the GitHub API (one per issue + one per PR). Use a long timeout (30-60s) when curling.
- **Scheduled scan sessions have issue_number=0:** These are correctly skipped by `post_report_to_github()` (only posts to `issue_number > 0`). Don't count them in expected `issues_commented`.
- **Cross-call deduplication is NOT implemented (by design):** Each `POST /report/github` call posts a fresh report. Only within-call dedup exists (via `seen_issues`/`seen_prs` sets).
- **PR comments use the GitHub Issues API:** GitHub treats PR comments as issue comments. To verify a comment on PR #12, query `/repos/.../issues/12/comments` (not `/pulls/12/comments`).
