from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ActionAPIConfigurationError(RuntimeError):
    """Raised when the organisers' Action API integration is not configured."""


class ActionAPIClient(Protocol):
    """Internal adapter contract; implement only after organisers provide API docs."""

    def execute_action(
        self,
        *,
        action: str,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def execute_action(
    action: str,
    parameters: Mapping[str, Any],
    *,
    client: ActionAPIClient | None = None,
) -> dict[str, Any]:
    """Submit an approved action through an explicitly configured API adapter."""
    if client is None:
        raise ActionAPIConfigurationError(
            "Live actions are unavailable until the organisers' Action API "
            "documentation and client adapter are configured"
        )
    result = client.execute_action(action=action, parameters=dict(parameters))
    if not isinstance(result, Mapping):
        raise TypeError("Action API client must return a mapping")
    return dict(result)