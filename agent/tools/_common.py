from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, TypedDict


class ToolResult(TypedDict):
    ok: bool
    summary: str
    data: dict[str, Any]
    source: str


class TelemetryBackend(Protocol):
    def prom_query(self, query: str, at: datetime | None = None) -> dict[str, Any]: ...

    def prom_query_range(
        self, query: str, start: datetime, end: datetime, step: str
    ) -> dict[str, Any]: ...

    def prom_metric_names(self) -> list[str]: ...

    def jaeger_services(self) -> list[str]: ...

    def jaeger_traces(self, service: str, lookback: str, limit: int) -> list[dict[str, Any]]: ...

    def jaeger_trace(self, trace_id: str) -> dict[str, Any]: ...

    def docker_ps(self) -> list[dict[str, Any]]: ...

    def docker_inspect(self, identifiers: list[str]) -> list[dict[str, Any]]: ...

    def docker_logs(self, container: str, minutes: int) -> list[str]: ...

class BackendError(RuntimeError):
    pass


def success(summary: str, data: dict[str, Any], source: str) -> ToolResult:
    return {"ok": True, "summary": summary, "data": data, "source": source}


def failure(summary: str, source: str) -> ToolResult:
    return {"ok": False, "summary": summary, "data": {}, "source": source}


def validate_minutes(minutes: int, *, maximum: int = 1440) -> None:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= maximum:
        raise ValueError(f"minutes must be an integer from 1 to {maximum}")
