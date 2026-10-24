from __future__ import annotations

import os


class KillSwitchActive(RuntimeError):
    """Raised when action execution is disabled by the operator kill switch."""


def check_kill_switch(value: str | None = None) -> None:
    """Fail closed on an enabled or malformed action kill-switch setting."""
    configured = os.getenv("INCIDENT_ZERO_KILL_SWITCH") if value is None else value
    if configured is None or configured.strip().casefold() in {"0", "false", "no", "off"}:
        return
    if configured.strip().casefold() in {"1", "true", "yes", "on"}:
        raise KillSwitchActive("Action execution is blocked by INCIDENT_ZERO_KILL_SWITCH")
    raise ValueError(
        "INCIDENT_ZERO_KILL_SWITCH must be one of: 0, 1, false, true, no, yes, off, on"
    )