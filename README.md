# Devin Autofix Service

A Dockerized automation tool that watches for GitHub issues labeled **`devin-autofix`** and automatically creates [Devin](https://devin.ai) sessions to investigate, fix, and open pull requests. It also runs a **daily scheduled scan** (default: 8 AM UTC) to detect critical vulnerabilities and outdated dependencies, creating Devin sessions to remediate them.

# Why Devin?

Traditional automation tools can execute predefined workflows, but they generally cannot investigate an unfamiliar codebase, determine an appropriate fix, validate the solution, and create a pull request autonomously.

Devin can.

Instead of automating individual tasks, this project demonstrates how engineering maintenance workflows can be automated end-to-end using an autonomous software engineering agent.

# Business Impact

Engineering teams spend significant time maintaining software rather than building new products.

This project demonstrates how Devin can transform repository maintenance into an autonomous workflow by:

* Reducing manual investigation and remediation work
* Improving repository health and security posture
* Accelerating dependency and vulnerability management
* Allowing engineers to focus on feature development and customer value
* Providing leadership visibility through reporting and observability



## Architecture

```
GitHub Issue (labeled "devin-autofix")
        │
        ▼
  GitHub Webhook ──► POST /webhook/github
        │
        ▼
  Verify signature ──► Create Devin session via API
        │
        ▼
  Background poller ──► Track status, detect PRs
        │
        ▼
  Observability endpoints (/report, /sessions, /health)


Daily Scheduler (8 AM UTC)
        │
        ▼
  Fetch Dependabot alerts (critical/high)
        │
        ▼
  Create Devin session for vulnerability fixes
        │
        ▼
  Create Devin session for dependency upgrades
```

## Quick Start
## Prerequisites

Before running this project, ensure you have:

- Docker Desktop (Docker Engine + Docker Compose)
- A Devin API Key
- A Devin Organization ID

### 1. Clone and configure

```bash
git clone https://github.com/emailsiwoo/devin-autofix.git
cd devin-autofix
cp .env.example .env
# Edit .env with your actual values
```
Update `.env` with your own credentials:

```env
DEVIN_API_KEY=your_api_key
DEVIN_ORG_ID=your_org_id
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

The service starts on **http://localhost:8000**.

Optional: Testing Live GitHub Webhooks
- If you would like to receive real GitHub webhook events while running locally, expose the service with a tunneling tool such as ngrok:
ngrok http 8000
https://<your-ngrok-url>/webhook/github

### 3. Set up the GitHub webhook

1. Go to **Settings → Webhooks** in your target repository (`emailsiwoo/superset-demo`).
2. Set **Payload URL** to your public endpoint, e.g. `https://your-server.com/webhook/github`.
3. Set **Content type** to `application/json`.
4. Set **Secret** to the same value as `GITHUB_WEBHOOK_SECRET` in your `.env`.
5. Under **events**, select **Issues** only.
6. Save.

## Environment Variables

| Variable | Description |
|---|---|
| `DEVIN_API_KEY` | API key for the Devin API |
| `DEVIN_ORG_ID` | Your Devin organization ID |
| `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures |
| `TARGET_REPO` | Target GitHub repo (default: `emailsiwoo/superset-demo`) |
| `TRIGGER_LABEL` | Label that triggers automation (default: `devin-autofix`) |
| `POLL_INTERVAL_SECONDS` | How often to poll session status (default: `60`) |
| `GITHUB_TOKEN` | GitHub PAT for Dependabot alerts API (needs `security_events` scope) |
| `SCAN_HOUR_UTC` | Hour (0-23) for the daily scan in UTC (default: `8`) |
| `SCAN_MINUTE_UTC` | Minute (0-59) for the daily scan in UTC (default: `0`) |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/github` | GitHub webhook receiver |
| `GET` | `/health` | Health check |
| `GET` | `/report` | JSON summary with BLUF, completion %, success rate, status breakdown |
| `GET` | `/dashboard` | Human-readable plain-text dashboard (curl-friendly) |
| `GET` | `/sessions` | List all tracked sessions |
| `GET` | `/sessions/active` | List active (pending/running) sessions |
| `GET` | `/sessions/{session_id}` | Get details for a specific session |
| `POST` | `/scan/trigger` | Manually trigger the daily vulnerability & dependency scan |

## Simulating the Workflow

You can test the webhook locally without GitHub by sending a simulated payload:

```bash
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -H "X-Hub-Signature-256: " \
  -d '{
    "action": "labeled",
    "label": {"name": "devin-autofix"},
    "issue": {
      "number": 1,
      "title": "Test issue",
      "html_url": "https://github.com/emailsiwoo/superset-demo/issues/1",
      "body": "This is a test issue for the autofix service."
    }
  }'
```

> **Note:** Signature verification is skipped when `GITHUB_WEBHOOK_SECRET` is empty.

Then check the status:

```bash
# Quick dashboard (human-readable, curl-friendly)
curl http://localhost:8000/dashboard

# JSON report with BLUF summary, completion %, and status breakdown
curl http://localhost:8000/report

# Health check
curl http://localhost:8000/health

# All sessions
curl http://localhost:8000/sessions
```

### Example dashboard output

```
============================================================
  DEVIN AUTOFIX — STATUS DASHBOARD
============================================================

  BOTTOM LINE: 75.0% complete | 2 passed | 1 failed | 1 active
  SUCCESS RATE: 66.7% of completed sessions

------------------------------------------------------------
  Target repo:    emailsiwoo/superset-demo
  Trigger label:  devin-autofix
  Scan schedule:  08:00 UTC daily
------------------------------------------------------------

  PROGRESS: [######################--------] 75.0% (3/4)

  SESSIONS:
  --------------------------------------------------------
  STATUS   ID             TITLE                          PR
  --------------------------------------------------------
  [PASS]   ..abc12345     Fix login redirect             Yes
  [PASS]   ..def67890     [scheduled] Daily dependency.. -
  [FAIL]   ..ghi24680     Update stale CSS imports       -
  [RUN]    ..jkl13579     Fix API rate limiting          -
  --------------------------------------------------------

  PULL REQUESTS (1):
    - https://github.com/emailsiwoo/superset-demo/pull/42

============================================================
```

## Scheduled Daily Scan

The service automatically runs a vulnerability and dependency scan every day at 8 AM UTC (configurable via `SCAN_HOUR_UTC` / `SCAN_MINUTE_UTC`).

The scan:
1. Fetches **critical** and **high** severity Dependabot alerts from the target repo.
2. If any are found, creates a Devin session to upgrade the affected dependencies.
3. Always creates a general dependency upgrade session to check for outdated packages.

You can also trigger the scan manually:

```bash
curl -X POST http://localhost:8000/scan/trigger
```

> **Note:** The Dependabot alerts API requires a `GITHUB_TOKEN` with `security_events` scope. Without it, the scan still runs the general dependency upgrade check.

## Session Tracking

Each triggered session tracks:

- Issue number, title, and URL
- Devin session ID and URL
- Current status (`pending`, `running`, `succeeded`, `failed`)
- PR URL (detected automatically when Devin opens one)
- Created/updated timestamps
- Success/failure state

Session data is persisted to a JSON file in a Docker volume so it survives container restarts.

## Development

Run locally without Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
