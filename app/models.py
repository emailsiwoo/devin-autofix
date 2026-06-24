from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

DATA_FILE = Path(os.getenv("DATA_DIR", "/data")) / "sessions.json"


class SessionStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    unknown = "unknown"


class TrackedSession(BaseModel):
    issue_number: int
    issue_title: str
    issue_url: str
    devin_session_id: str
    devin_session_url: str
    status: SessionStatus = SessionStatus.pending
    pr_url: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success: Optional[bool] = None


class SessionStore:
    """In-memory session store with optional JSON file persistence."""

    def __init__(self) -> None:
        self._sessions: dict[str, TrackedSession] = {}
        self._load()

    def _load(self) -> None:
        if DATA_FILE.exists():
            try:
                raw = json.loads(DATA_FILE.read_text())
                for sid, data in raw.items():
                    self._sessions[sid] = TrackedSession(**data)
            except Exception:
                pass

    def _save(self) -> None:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(
            json.dumps({sid: s.model_dump() for sid, s in self._sessions.items()}, indent=2)
        )

    def add(self, session: TrackedSession) -> None:
        self._sessions[session.devin_session_id] = session
        self._save()

    def get(self, session_id: str) -> Optional[TrackedSession]:
        return self._sessions.get(session_id)

    def all(self) -> list[TrackedSession]:
        return list(self._sessions.values())

    def active(self) -> list[TrackedSession]:
        return [s for s in self._sessions.values() if s.status in (SessionStatus.pending, SessionStatus.running)]

    def update(self, session_id: str, **kwargs: object) -> Optional[TrackedSession]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        for key, value in kwargs.items():
            setattr(session, key, value)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return session


store = SessionStore()
