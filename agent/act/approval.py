from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def request_approval(
    proposal: Mapping[str, Any],
    response: str | None,
) -> dict[str, Any]:
    """Record explicit human approval; only the exact affirmative word is accepted."""
    if response != "yes":
        return {
            "approved": False,
            "status": "rejected",
            "reason": 'Approval requires the exact response "yes"',
            "proposal": dict(proposal),
        }
    return {
        "approved": True,
        "status": "approved",
        "reason": "Explicit human approval received",
        "proposal": dict(proposal),
    }