from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SERVICE_LOG_PATTERN = re.compile(r"\b(?:service|container)[=: ]+([a-zA-Z0-9_.-]+)", re.IGNORECASE)
_CACHE_ERROR = re.compile(
    r"(?:\b(valkey|redis|cache)\b.{0,100}\b(connection refused|unavailable|timeout|failed)\b"
    r"|\b(connection refused|unavailable|timeout|failed)\b.{0,100}\b(valkey|redis|cache)\b)",
    re.IGNORECASE,
)
_CONNECTION_ERROR = re.compile(r"\b(connection refused|unavailable|deadline exceeded|connection reset)\b", re.IGNORECASE)

_SCORES = {
    "container_not_running": 100,
    "container_unhealthy": 90,
    "oom_killed": 85,
    "restart_count_increased": 65,
    "deepest_error_span": 55,
    "error_span_observed": 35,
    "error_rate_increased": 40,
    "high_error_rate": 30,
    "latency_increased": 25,
    "high_cpu": 25,
    "memory_increasing": 25,
    "connection_failure": 25,
    "network_latency_gap": 20,
    "recent_deployment": 15,
    "high_request_rate": 10,
    "cache_connection_failure": 45,
    "traffic_spike": 35,
    "latency_increased_across_services": 20,
}


def _tool_data(value: Any, source_name: str, failures: list[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        failures.append(f"{source_name}: expected a mapping")
        return {}
    if "ok" in value:
        if value.get("ok") is not True:
            failures.append(f"{source_name}: {value.get('summary', 'tool failed')}")
            return {}
        data = value.get("data", {})
        if not isinstance(data, Mapping):
            failures.append(f"{source_name}: tool data was not a mapping")
            return {}
        return data
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _service_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip().lstrip("/")
    if not name or "flagd" in name.casefold():
        return None
    return name


def _add_signal(
    suspects: dict[str, dict[str, Any]],
    service: str | None,
    code: str,
    *,
    detail: Mapping[str, Any] | None = None,
    score: int | None = None,
) -> None:
    service_name = _service_name(service)
    if service_name is None:
        return
    suspect = suspects.setdefault(
        service_name,
        {"service": service_name, "score": 0, "signals": [], "evidence": []},
    )
    if code not in suspect["signals"]:
        suspect["signals"].append(code)
        suspect["score"] += score if score is not None else _SCORES[code]
    if detail:
        suspect["evidence"].append(dict(detail))


def _service_metrics(data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = data.get("services", [])
    if not isinstance(rows, list):
        return {}
    return {
        name: row
        for row in rows
        if isinstance(row, Mapping)
        and (name := _service_name(row.get("service"))) is not None
    }


def _cpu_and_memory(
    resource_metrics: Any, suspects: dict[str, dict[str, Any]], failures: list[str]
) -> None:
    data = _tool_data(resource_metrics, "resource_metrics", failures)
    for key, signal, threshold in (
        ("cpu_percent", "high_cpu", 80.0),
    ):
        for series in _list(data.get(key)):
            if not isinstance(series, Mapping):
                continue
            labels = series.get("metric", {})
            labels = labels if isinstance(labels, Mapping) else {}
            service = _service_name(
                labels.get("service_name")
                or labels.get("container_name")
                or labels.get("container")
            )
            value = _number(series.get("last"))
            if service and value is not None and value >= threshold:
                _add_signal(
                    suspects,
                    service,
                    signal,
                    detail={"metric": key, "value": value, "threshold": threshold},
                )
    memory_threshold = 5.0
    for series in _list(data.get("memory_bytes")):
        if not isinstance(series, Mapping):
            continue
        labels = series.get("metric", {})
        labels = labels if isinstance(labels, Mapping) else {}
        service = _service_name(
            labels.get("service_name")
            or labels.get("container_name")
            or labels.get("container")
        )
        points = _list(series.get("values"))
        values = [
            _number(point[1])
            for point in points
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        values = [value for value in values if value is not None and value >= 0]
        if len(values) < 3 or values[0] <= 0:
            continue
        monotonic = all(left <= right for left, right in zip(values, values[1:]))
        growth_percent = (values[-1] - values[0]) / values[0] * 100
        if service and monotonic and growth_percent >= memory_threshold:
            _add_signal(
                suspects,
                service,
                "memory_increasing",
                detail={
                    "metric": "memory_bytes",
                    "first": values[0],
                    "last": values[-1],
                    "growth_percent": round(growth_percent, 2),
                    "threshold_percent": memory_threshold,
                },
            )


def _container_signals(
    health: Mapping[str, Any],
    suspects: dict[str, dict[str, Any]],
    ignored_containers: list[dict[str, str]],
) -> None:
    for container in _list(health.get("containers")):
        if not isinstance(container, Mapping):
            continue
        project = container.get("compose_project")
        if isinstance(project, str) and project.casefold() != "opentelemetry-demo":
            name = _service_name(container.get("name") or container.get("service"))
            if name:
                ignored_containers.append({"service": name, "compose_project": project})
            continue
        service = _service_name(container.get("name") or container.get("service"))
        if service is None:
            continue
        state = str(container.get("state", "unknown")).casefold()
        detail = {
            "state": state,
            "health": container.get("health"),
            "restart_count": container.get("restart_count"),
            "oom_killed": container.get("oom_killed"),
        }
        if state != "running":
            _add_signal(suspects, service, "container_not_running", detail=detail)
        if container.get("health") not in (None, "healthy"):
            _add_signal(suspects, service, "container_unhealthy", detail=detail)
        if container.get("oom_killed") is True:
            _add_signal(suspects, service, "oom_killed", detail=detail)
        restart_count = _number(container.get("restart_count"))
        if restart_count is not None and restart_count > 0:
            _add_signal(
                suspects,
                service,
                "restart_count_increased",
                detail=detail,
                score=min(_SCORES["restart_count_increased"] * int(restart_count), 95),
            )


def _trace_signals(
    traces: Any, suspects: dict[str, dict[str, Any]], failures: list[str]
) -> None:
    if isinstance(traces, Mapping) and "ok" in traces:
        data = _tool_data(traces, "traces", failures)
        trace_list = _list(data.get("traces")) or ([data] if data else [])
    elif isinstance(traces, list):
        trace_list = traces
    elif isinstance(traces, Mapping):
        groups = traces.values()
        trace_list = []
        for group in groups:
            data = _tool_data(group, "traces", failures)
            if data:
                trace_list.extend(_list(data.get("traces")) or [data])
    elif traces is None:
        trace_list = []
    else:
        groups = [traces]
        trace_list = []
        for group in groups:
            data = _tool_data(group, "traces", failures)
            if data:
                trace_list.extend(_list(data.get("traces")) or [data])
    for trace in trace_list:
        if not isinstance(trace, Mapping):
            continue
        deepest = trace.get("deepest_error_span")
        if isinstance(deepest, Mapping):
            service = _service_name(deepest.get("service"))
            if service:
                _add_signal(
                    suspects,
                    service,
                    "deepest_error_span",
                    detail={
                        "trace_id": trace.get("trace_id"),
                        "operation": deepest.get("operation"),
                        "error_message": deepest.get("error_message"),
                    },
                )
        for deepest in _list(trace.get("deepest_error_spans")):
            if isinstance(deepest, Mapping):
                service = _service_name(deepest.get("service"))
                if service:
                    _add_signal(
                        suspects,
                        service,
                        "deepest_error_span",
                        detail={
                            "trace_id": trace.get("trace_id"),
                            "operation": deepest.get("operation"),
                            "error_message": deepest.get("error_message"),
                        },
                    )
        error_span = trace.get("error_span")
        if isinstance(error_span, Mapping):
            service = _service_name(error_span.get("service"))
            message = str(error_span.get("message") or "")
            if service:
                _add_signal(
                    suspects,
                    service,
                    "error_span_observed",
                    detail={
                        "trace_id": trace.get("trace_id"),
                        "operation": error_span.get("operation"),
                        "error_message": message or None,
                    },
                )
                if _CONNECTION_ERROR.search(message):
                    _add_signal(
                        suspects,
                        service,
                        "connection_failure",
                        detail={
                            "trace_id": trace.get("trace_id"),
                            "operation": error_span.get("operation"),
                            "error_message": message,
                        },
                    )
        spans = _list(trace.get("spans"))
        for span in spans:
            if not isinstance(span, Mapping) or span.get("error") is not True:
                continue
            message = str(span.get("error_message") or "")
            service = _service_name(span.get("service"))
            if service and _CONNECTION_ERROR.search(message):
                _add_signal(
                    suspects,
                    service,
                    "connection_failure",
                    detail={
                        "trace_id": trace.get("trace_id"),
                        "operation": span.get("operation"),
                        "error_message": message,
                    },
                )
        for edge in _list(trace.get("edges")):
            if not isinstance(edge, Mapping):
                continue
            parent, child = edge.get("from", {}), edge.get("to", {})
            parent = parent if isinstance(parent, Mapping) else {}
            child = child if isinstance(child, Mapping) else {}
            caller_duration = _number(edge.get("client_duration_ms"))
            server_duration = _number(edge.get("server_duration_ms"))
            gap = _number(edge.get("network_gap_ms"))
            if caller_duration is None or server_duration is None or gap is None:
                continue
            if gap >= 50 and caller_duration >= max(100, server_duration * 1.5):
                caller = _service_name(parent.get("service"))
                callee = _service_name(child.get("service"))
                detail = {
                    "trace_id": trace.get("trace_id"),
                    "from": caller,
                    "to": callee,
                    "client_duration_ms": caller_duration,
                    "server_duration_ms": server_duration,
                    "network_gap_ms": gap,
                }
                _add_signal(suspects, caller, "network_latency_gap", detail=detail)
                _add_signal(suspects, callee, "network_latency_gap", detail=detail)


def _log_signals(
    logs: Any, suspects: dict[str, dict[str, Any]], failures: list[str]
) -> None:
    if not isinstance(logs, Mapping):
        return
    if "ok" in logs:
        data = _tool_data(logs, "logs", failures)
        service = _service_name(data.get("service"))
        log_groups = {service or "logs": data}
    else:
        log_groups = logs
    for service_key, tool_result in log_groups.items():
        log_data = _tool_data(tool_result, f"logs[{service_key}]", failures)
        if not isinstance(log_data, Mapping):
            continue
        service = _service_name(log_data.get("service") or service_key)
        for record in _list(log_data.get("lines")):
            line = record.get("line", "") if isinstance(record, Mapping) else record
            if not isinstance(line, str):
                continue
            if _CACHE_ERROR.search(line):
                target_match = _SERVICE_LOG_PATTERN.search(line)
                target = target_match.group(1) if target_match else "valkey-cart"
                _add_signal(
                    suspects,
                    target,
                    "cache_connection_failure",
                    detail={"reported_by": service, "message": line},
                )
            if _CONNECTION_ERROR.search(line) and service:
                _add_signal(
                    suspects,
                    service,
                    "connection_failure",
                    detail={"message": line},
                )


def _deployment_signals(
    deployment: Mapping[str, Any], suspects: dict[str, dict[str, Any]]
) -> None:
    for event in _list(deployment.get("events")):
        if not isinstance(event, Mapping):
            continue
        service = _service_name(event.get("container") or event.get("service"))
        if service:
            _add_signal(
                suspects,
                service,
                "recent_deployment",
                detail={
                    "kind": event.get("kind"),
                    "timestamp": event.get("timestamp"),
                    "image": event.get("image"),
                },
            )


def _fingerprint(suspects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    symptoms = [
        {
            "service": suspect["service"],
            "signals": sorted(set(suspect["signals"])),
        }
        for suspect in suspects
    ]
    serialized = json.dumps(symptoms, sort_keys=True, separators=(",", ":"))
    return {
        "id": hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16],
        "symptoms": symptoms,
    }


def triage(incident: str, telemetry: Mapping[str, Any]) -> dict[str, Any]:
    """Rank telemetry-backed suspects without an LLM or incident-name matching.

    `telemetry` accepts Step 4 results under `service_red`, `container_health`,
    `resource_metrics`, `traces`, `logs`, and `deploy_history`. A tool's normal
    `{ok, summary, data, source}` wrapper or its `data` payload can be supplied.
    """
    if not isinstance(incident, str) or not incident.strip():
        raise ValueError("incident must be a non-empty description")
    if not isinstance(telemetry, Mapping):
        raise ValueError("telemetry must be a mapping of telemetry results")

    suspects: dict[str, dict[str, Any]] = {}
    unavailable_sources: list[str] = []
    ignored_containers: list[dict[str, str]] = []
    red = _tool_data(telemetry.get("service_red"), "service_red", unavailable_sources)
    health_result = _tool_data(
        telemetry.get("container_health"), "container_health", unavailable_sources
    )
    resource_result = health_result.get("resource_metrics")
    if resource_result is None:
        resource_result = telemetry.get("resource_metrics")
    _cpu_and_memory(resource_result, suspects, unavailable_sources)
    _container_signals(health_result, suspects, ignored_containers)
    _trace_signals(telemetry.get("traces"), suspects, unavailable_sources)
    _log_signals(telemetry.get("logs"), suspects, unavailable_sources)
    deployment = _tool_data(
        telemetry.get("deploy_history"), "deploy_history", unavailable_sources
    )
    _deployment_signals(deployment, suspects)

    metrics_by_service = _service_metrics(red)
    for service, metrics in metrics_by_service.items():
        error = metrics.get("error_rate", {})
        request = metrics.get("request_rate", {})
        latency = metrics.get("p95_latency_ms", {})
        error = error if isinstance(error, Mapping) else {}
        request = request if isinstance(request, Mapping) else {}
        latency = latency if isinstance(latency, Mapping) else {}
        error_current = _number(error.get("current"))
        error_baseline = _number(error.get("baseline"))
        request_current = _number(request.get("current"))
        request_baseline = _number(request.get("baseline"))
        latency_current = _number(latency.get("current"))
        latency_baseline = _number(latency.get("baseline"))
        error_delta = _number(error.get("delta"))
        latency_delta_percent = _number(latency.get("delta_percent"))

        if error_delta is not None and error_delta > 0 and error_current is not None:
            _add_signal(
                suspects,
                service,
                "error_rate_increased",
                detail={
                    "current_errors_per_second": error_current,
                    "baseline_errors_per_second": error_baseline,
                    "delta_per_second": error_delta,
                },
            )
        if (
            error_current is not None
            and request_current is not None
            and request_current > 0
            and error_current / request_current >= 0.05
        ):
            _add_signal(
                suspects,
                service,
                "high_error_rate",
                detail={
                    "error_percent": round(error_current / request_current * 100, 2),
                    "threshold_percent": 5.0,
                },
            )
        if latency_delta_percent is not None and latency_delta_percent >= 20:
            _add_signal(
                suspects,
                service,
                "latency_increased",
                detail={
                    "current_p95_ms": latency_current,
                    "baseline_p95_ms": latency_baseline,
                    "delta_percent": latency_delta_percent,
                },
            )
        if (
            request_current is not None
            and request_baseline is not None
            and request_baseline > 0
            and request_current >= request_baseline * 2
        ):
            _add_signal(
                suspects,
                service,
                "high_request_rate",
                detail={
                    "current_per_second": request_current,
                    "baseline_per_second": request_baseline,
                },
            )

    traffic_candidates = {
        service
        for service, suspect in suspects.items()
        if service.casefold() in {"frontend", "frontend-proxy", "frontend-web"}
        and "high_request_rate" in suspect["signals"]
        and (
            (metrics := metrics_by_service.get(service)) is not None
            and _number(
                (metrics.get("error_rate") or {}).get("current")
                if isinstance(metrics.get("error_rate"), Mapping)
                else None
            )
            is not None
            and _number(
                (metrics.get("request_rate") or {}).get("current")
                if isinstance(metrics.get("request_rate"), Mapping)
                else None
            )
            is not None
            and _number(metrics["request_rate"]["current"]) > 0
            and _number(metrics["error_rate"]["current"])
            / _number(metrics["request_rate"]["current"])
            < 0.05
        )
    }
    latency_affected = [
        service
        for service, suspect in suspects.items()
        if "latency_increased" in suspect["signals"]
    ]
    if traffic_candidates and len(latency_affected) >= 3:
        for service in traffic_candidates:
            _add_signal(
                suspects,
                service,
                "traffic_spike",
                detail={"latency_affected_services": sorted(latency_affected)},
            )
        for service in latency_affected:
            _add_signal(
                suspects,
                service,
                "latency_increased_across_services",
                detail={"affected_services": sorted(latency_affected)},
            )

    ranked = sorted(
        suspects.values(),
        key=lambda suspect: (-suspect["score"], suspect["service"].casefold()),
    )
    for suspect in ranked:
        suspect["signals"].sort()
    return {
        "incident": incident.strip(),
        "fingerprint": _fingerprint(ranked),
        "suspects": ranked,
        "unavailable_sources": sorted(set(unavailable_sources)),
        "ignored_containers": sorted(
            ignored_containers, key=lambda item: (item["compose_project"], item["service"])
        ),
    }


def triage_incident(incident: str, telemetry: Mapping[str, Any]) -> dict[str, Any]:
    """Descriptive alias for callers that prefer an explicit function name."""
    return triage(incident, telemetry)