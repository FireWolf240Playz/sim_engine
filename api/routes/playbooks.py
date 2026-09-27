"""GET /playbooks — the named incident playbooks (name + description).

A cheap, dependency-free lookup so the frontend can build its incident
picker without running a single simulation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import PlaybookListResponse
from sim_core.playbooks import list_playbooks

router = APIRouter(tags=["playbooks"])


@router.get("/playbooks", response_model=PlaybookListResponse)
def playbooks() -> dict[str, Any]:
    """Every built-in playbook, in registry order."""
    items = [{"name": p.name, "description": p.description} for p in list_playbooks()]
    return {"playbooks": items, "count": len(items)}
