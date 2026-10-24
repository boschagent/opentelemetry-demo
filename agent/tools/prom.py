from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from typing import Any

from agent.backends.live import LiveBackend, utc_before, utc_now
from agent.tools._common import (
    BackendError,
    TelemetryBackend,
    ToolResult,
    failure,
    success,
    validate_minutes,
)
from agent.tools.sanitize import redact

DEFAULT_STEP = "15s"
_live = LiveBackend()


def _backend(backend: TelemetryBackend | None) -> TelemetryBackend:
    return backend if backend is not None else _live


def _range_series(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result", [])
    if data.get("resultType") != "matrix" or not isinstance(result, list):
        raise BackendError("Prometheus range query did not return a matrix")
    return [series for series in result if isinstance(series, dict)]


def _numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _summarize_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for item in series:
        points = item.get("values", [])
        if not isinstance(points, list):
            points = []
        numeric_points = [
            [point[0], value]
            for point in points
            if isinstance(point, (list, tuple))
            and len(point) >= 2
            and (value := _numeric(point[1])) is not None
        ]
        values = [point[1] for point in numeric_points]
        summaries.append(
            {
                "metric": item.get("metric", {}),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "last": values[-1] if values else None,
                "values": numeric_points,
            }
        )
    return summaries


def prom_query(
    promql: str,
    minutes: int = 15,
    step: str = DEFAULT_STEP,
    backend: TelemetryBackend | None = None,
) -> ToolResult:
    source = f"Prometheus range query (last {minutes}m, step={step}): {promql}"
    try:
        validate_minutes(minutes)
        if not isinstance(promql, str) or not promql.strip():
            raise ValueError("promql must be a non-empty string")
        if not isinstance(step, str) or not re.fullmatch(
            r"[1-9][0-9]*(?:ms|s|m|h|d|w|y)", step
        ):
            raise ValueError("step must be a positive Prometheus duration")
        start, end = utc_before(minutes), utc_now()
        data = _backend(backend).prom_query_range(promql, start, end, step)
        series = _range_series(data)
        summaries = _summarize_series(series)
        safe, removed = redact({"result_type": data.get("resultType"), "series": summaries})
        return success(
            f"Prometheus returned {len(summaries)} time series",
            {**safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Prometheus query failed: {exc}", source)


def _query(
    backend: TelemetryBackend,
    query: str,
    start: Any,
    end: Any,
    step: str,
) -> list[dict[str, Any]]:
    response = backend.prom_query_range(query, start, end, step)
    return _range_series(response)


def _mean_for_window(
    series: list[dict[str, Any]], cutoff: float | None = None, after: bool = False
) -> dict[str, float]:
    values_by_service: dict[str, list[float]] = {}
    for item in series:
        metric = item.get("metric", {})
        if not isinstance(metric, dict):
            continue
        service = metric.get("service_name") or metric.get("service")
        if not isinstance(service, str):
            continue
        points = item.get("values", [])
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            timestamp, value = _numeric(point[0]), _numeric(point[1])
            if timestamp is None or value is None:
                continue
            if cutoff is not None and ((timestamp >= cutoff) != after):
                continue
            values_by_service.setdefault(service, []).append(value)
    return {
        service: sum(values) / len(values)
        for service, values in values_by_service.items()
        if values
    }


def _relative_delta(current: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return (current - baseline) / abs(baseline) * 100.0


def service_red(
    service: str | None = None,
    minutes: int = 10,
    backend: TelemetryBackend | None = None,
) -> ToolResult:
    source = "Prometheus RED queries for calls, errors, and p95 latency"
    try:
        validate_minutes(minutes, maximum=120)
        if service is not None and (not isinstance(service, str) or not service.strip()):
            raise ValueError("service must be a non-empty string when provided")
        client = _backend(backend)
        end = utc_now()
        start = end - timedelta(minutes=minutes + 3)
        selector = f"service_name={json.dumps(service)}" if service else ""
        calls_selector = f"{{{selector}}}" if selector else ""
        calls = f"sum by (service_name) (rate(traces_span_metrics_calls_total{calls_selector}[5m]))"
        error_selector = 'status_code="STATUS_CODE_ERROR"'
        if selector:
            error_selector += f",{selector}"
        errors = (
            "sum by (service_name) "
            f"(rate(traces_span_metrics_calls_total{{{error_selector}}}[5m]))"
        )
        latency = (
            "histogram_quantile(0.95, "
            "sum by (service_name, le) "
            f"(rate(traces_span_metrics_duration_milliseconds_bucket{calls_selector}[5m])))"
        )
        source = (
            f"Prometheus range queries (last {minutes + 3}m, step={DEFAULT_STEP}): "
            f"{calls}; {errors}; {latency}"
        )
        response = {
            "request_rate": _query(client, calls, start, end, DEFAULT_STEP),
            "error_rate": _query(client, errors, start, end, DEFAULT_STEP),
            "p95_latency_ms": _query(client, latency, start, end, DEFAULT_STEP),
        }
        baseline_cutoff = (end - timedelta(minutes=3)).timestamp()
        records: dict[str, dict[str, Any]] = {}
        for metric_name, series in response.items():
            current = _mean_for_window(series, baseline_cutoff, after=True)
            baseline = _mean_for_window(series, baseline_cutoff, after=False)
            services = set(current) | set(baseline)
            for name in services:
                record = records.setdefault(name, {"service": name})
                current_value = current.get(name, 0.0)
                baseline_value = baseline.get(name, 0.0)
                record[metric_name] = {
                    "current": current_value,
                    "baseline": baseline_value,
                    "delta": current_value - baseline_value,
                    "delta_percent": _relative_delta(current_value, baseline_value),
                }
        ordered = sorted(
            records.values(),
            key=lambda row: (
                row.get("error_rate", {}).get("delta", 0.0),
                row.get("p95_latency_ms", {}).get("delta", 0.0),
                row.get("request_rate", {}).get("delta", 0.0),
            ),
            reverse=True,
        )
        safe, removed = redact(
            {
                "comparison": {
                    "current_window_minutes": 3,
                    "baseline_window_minutes": minutes,
                },
                "services": ordered,
            }
        )
        return success(
            f"Calculated RED metrics for {len(ordered)} service(s)",
            {**safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Service RED calculation failed: {exc}", source)


def container_resources(
    minutes: int = 10,
    backend: TelemetryBackend | None = None,
) -> ToolResult:
    source = "Prometheus GET /api/v1/label/__name__/values"
    try:
        validate_minutes(minutes, maximum=120)
        client = _backend(backend)
        names = [name for name in client.prom_metric_names() if name.startswith("container_")]
        cpu_name = next(
            (
                name
                for name in (
                    "container_cpu_usage_nanoseconds_total",
                    "container_cpu_usage_seconds_total",
                    "container_cpu_user_seconds_total",
                    "container_cpu_utilization_ratio",
                )
                if name in names
            ),
            None,
        )
        memory_name = next(
            (
                name
                for name in (
                    "container_memory_working_set_bytes",
                    "container_memory_usage_total_bytes",
                    "container_memory_usage_bytes",
                )
                if name in names
            ),
            None,
        )
        if not cpu_name and not memory_name:
            return failure("No supported container CPU or memory metrics were found", source)
        start, end = utc_before(minutes), utc_now()
        metrics: dict[str, Any] = {"discovered_metrics": names}
        if cpu_name == "container_cpu_usage_nanoseconds_total":
            cpu_query = f"rate({cpu_name}[1m]) / 10000000"
        elif cpu_name in {"container_cpu_usage_seconds_total", "container_cpu_user_seconds_total"}:
            cpu_query = f"rate({cpu_name}[1m]) * 100"
        elif cpu_name == "container_cpu_utilization_ratio":
            cpu_query = f"{cpu_name} * 100"
        else:
            cpu_query = None
        queries = [query for query in (cpu_query, memory_name) if query]
        source = (
            f"Prometheus metric discovery and range queries (last {minutes}m, "
            f"step={DEFAULT_STEP}): {'; '.join(queries)}"
        )
        for key, metric_query in (
            ("cpu_percent", cpu_query),
            ("memory_bytes", memory_name if memory_name else None),
        ):
            if metric_query:
                data = client.prom_query_range(metric_query, start, end, DEFAULT_STEP)
                metrics[key] = _summarize_series(_range_series(data))
        safe, removed = redact(metrics)
        return success(
            f"Collected container resource metrics for {minutes} minute(s)",
            {**safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Container resource query failed: {exc}", source)