# Devin Autofix Service

A Dockerized automation tool that watches for GitHub issues labeled **`devin-autofix`** and automatically creates [Devin](https://devin.ai) sessions to investigate, fix, and open pull requests.

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
```

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/emailsiwoo/devin-autofix.git
cd devin-autofix
cp .env.example .env
# Edit .env with your actual values
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

The service starts on **http://localhost:8000**.

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

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/github` | GitHub webhook receiver |
| `GET` | `/health` | Health check |
| `GET` | `/report` | Summary report of automation status |
| `GET` | `/sessions` | List all tracked sessions |
| `GET` | `/sessions/active` | List active (pending/running) sessions |
| `GET` | `/sessions/{session_id}` | Get details for a specific session |

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
# Health check
curl http://localhost:8000/health

# Full report
curl http://localhost:8000/report

# All sessions
curl http://localhost:8000/sessions
```

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
