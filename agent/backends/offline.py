from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.tools._common import BackendError
from agent.backends.live import jaeger_trace_from_otlp


class OfflineBackend:
    """Serve telemetry responses from a JSON incident bundle.

    Bundle sections are `prometheus.range_queries`, `prometheus.queries`,
    `prometheus.metric_names`, `jaeger.services`, `jaeger.traces`,
    `jaeger.traces_by_service`, and `docker.containers`, `docker.inspect`,
    `docker.logs`. Query fixtures are keyed by the exact query/identifier.
    """

    def __init__(self, bundle: str | Path | dict[str, Any]) -> None:
        if isinstance(bundle, dict):
            self.bundle = bundle
            self.bundle_path = "<in-memory bundle>"
            return
        path = Path(bundle)
        if path.is_dir():
            path = path / "bundle.json"
        try:
            self.bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackendError(f"Could not load offline bundle {path}: {exc}") from exc
        if not isinstance(self.bundle, dict):
            raise BackendError("Offline bundle root must be a JSON object")
        self.bundle_path = str(path)

    def _section(self, name: str) -> dict[str, Any]:
        value = self.bundle.get(name, {})
        if not isinstance(value, dict):
            raise BackendError(f"Offline bundle section {name!r} must be an object")
        return value

    def _lookup(self, section: dict[str, Any], key: str, description: str) -> Any:
        if key not in section:
            raise BackendError(f"Offline bundle has no {description} fixture for {key!r}")
        return section[key]

    def prom_query(self, query: str, at: Any = None) -> dict[str, Any]:
        result = self._lookup(self._section("prometheus").get("queries", {}), query, "Prometheus query")
        if not isinstance(result, dict):
            raise BackendError("Prometheus query fixture must be an object")
        return result

    def prom_query_range(self, query: str, start: Any, end: Any, step: str) -> dict[str, Any]:
        result = self._lookup(
            self._section("prometheus").get("range_queries", {}),
            query,
            "Prometheus range query",
        )
        if not isinstance(result, dict):
            raise BackendError("Prometheus range-query fixture must be an object")
        return result

    def prom_metric_names(self) -> list[str]:
        result = self._section("prometheus").get("metric_names", [])
        if not isinstance(result, list) or not all(isinstance(name, str) for name in result):
            raise BackendError("Prometheus metric_names fixture must be a list of strings")
        return result

    def jaeger_services(self) -> list[str]:
        result = self._section("jaeger").get("services", [])
        if not isinstance(result, list) or not all(isinstance(name, str) for name in result):
            raise BackendError("Jaeger services fixture must be a list of strings")
        return result

    def jaeger_traces(self, service: str, lookback: str, limit: int) -> list[dict[str, Any]]:
        section = self._section("jaeger")
        by_service = section.get("traces_by_service", {})
        if isinstance(by_service, dict) and service in by_service:
            traces = by_service[service]
        else:
            traces = section.get("traces", [])
        if isinstance(traces, dict):
            trace_items = list(traces.items())
        elif isinstance(traces, list):
            trace_items = [(None, item) for item in traces]
        else:
            raise BackendError("Jaeger traces fixture must be a list of objects")
        if not all(isinstance(item, dict) for _, item in trace_items):
            raise BackendError("Jaeger traces fixture must contain objects")
        traces = [
            self._normalize_trace(trace, trace_id if isinstance(trace_id, str) else None)
            for trace_id, trace in trace_items
        ]
        if not (isinstance(by_service, dict) and service in by_service):
            traces = [
                trace for trace in traces
                if any(
                    isinstance(process, dict)
                    and any(
                        isinstance(tag, dict)
                        and tag.get("key") in {"service.name", "service_name"}
                        and tag.get("value") == service
                        for tag in (
                            process.get("tags", [])
                            if isinstance(process.get("tags", []), list)
                            else []
                        )
                    )
                    for process in (
                        trace.get("processes", {}).values()
                        if isinstance(trace.get("processes"), dict)
                        else []
                    )
                )
            ]
        return traces[:limit]

    def jaeger_trace(self, trace_id: str) -> dict[str, Any]:
        traces = self._section("jaeger").get("traces", {})
        if isinstance(traces, dict):
            result = self._lookup(traces, trace_id, "Jaeger trace")
        elif isinstance(traces, list):
            result = next(
                (
                    item for item in traces
                    if isinstance(item, dict) and item.get("traceID") == trace_id
                ),
                None,
            )
            if result is None:
                raise BackendError(f"Offline bundle has no Jaeger trace {trace_id!r}")
        else:
            raise BackendError("Jaeger traces fixture must be a list or object")
        if not isinstance(result, dict):
            raise BackendError("Jaeger trace fixture must be an object")
        return self._normalize_trace(result, trace_id)

    @staticmethod
    def _normalize_trace(
        trace: dict[str, Any], trace_id: str | None = None
    ) -> dict[str, Any]:
        result = trace.get("result")
        if isinstance(result, dict) and isinstance(result.get("resourceSpans"), list):
            normalized_id = result.get("traceID") or trace.get("traceID") or trace_id
            if not isinstance(normalized_id, str) or not normalized_id:
                raise BackendError("OTLP Jaeger trace fixture is missing its trace ID")
            return jaeger_trace_from_otlp(result, normalized_id)
        return trace

    def docker_ps(self) -> list[dict[str, Any]]:
        result = self._section("docker").get("containers", [])
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise BackendError("Docker containers fixture must be a list of objects")
        return result

    def docker_inspect(self, identifiers: list[str]) -> list[dict[str, Any]]:
        fixtures = self._section("docker").get("inspect", {})
        if not isinstance(fixtures, dict):
            raise BackendError("Docker inspect fixture must be an object")
        results = []
        for identifier in identifiers:
            item = fixtures.get(identifier)
            if item is None:
                item = next(
                    (
                        candidate for candidate in fixtures.values()
                        if isinstance(candidate, dict)
                        and (candidate.get("Id") == identifier
                             or identifier in candidate.get("Names", []))
                    ),
                    None,
                )
            if item is None:
                raise BackendError(f"Offline bundle has no Docker inspect fixture for {identifier!r}")
            if isinstance(item, list):
                results.extend(item)
            elif isinstance(item, dict):
                results.append(item)
            else:
                raise BackendError("Docker inspect fixture entries must be objects")
        return results

    def docker_logs(self, container: str, minutes: int) -> list[str]:
        logs = self._section("docker").get("logs", {})
        result = self._lookup(logs, container, "Docker logs")
        if not isinstance(result, list) or not all(isinstance(line, str) for line in result):
            raise BackendError("Docker logs fixture must be a list of strings")
        return result