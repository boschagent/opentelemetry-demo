from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


class EvidenceLedger:
    """Collect successful tool outputs as immutable, citable evidence entries."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def record(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        result: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            return None

        evidence_id = f"ev-{len(self._entries) + 1:03d}"
        entry = {
            "evidence_id": evidence_id,
            "tool": tool,
            "arguments": deepcopy(dict(arguments)),
            "raw_output": deepcopy(dict(result)),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._entries.append(entry)
        return deepcopy(entry)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return deepcopy(self._entries)