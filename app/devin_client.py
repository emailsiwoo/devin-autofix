from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("autofix.devin")

HEADERS = {
    "Authorization": f"Bearer {settings.devin_api_key}",
    "Content-Type": "application/json",
}


async def create_session(prompt: str) -> dict[str, Any]:
    """Create a new Devin session and return the API response."""
    url = f"{settings.devin_api_base}/sessions"
    payload = {"prompt": prompt}
    logger.info("Creating Devin session via %s", url)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=HEADERS)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        logger.info("Devin session created: %s", data.get("session_id"))
        return data


async def get_session(session_id: str) -> dict[str, Any]:
    """Fetch the current status of a Devin session."""
    url = f"{settings.devin_api_base}/sessions/{session_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
