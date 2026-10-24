from __future__ import annotations

import re
from typing import Any

from agent.backends.live import LiveBackend
from agent.tools._common import BackendError, TelemetryBackend, ToolResult, failure, success
from agent.tools.sanitize import redact

_live = LiveBackend()
_LOOKBACK = re.compile(r"[1-9][0-9]*(?:ms|s|m|h|d|w|y)\Z")


def _backend(backend: TelemetryBackend | None) -> TelemetryBackend:
    return backend if backend is not None else _live


def _tag_map(tags: Any) -> dict[str, Any]:
    if not isinstance(tags, list):
        return {}
    return {
        item["key"]: item.get("value")
        for item in tags
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def _span_error(span: dict[str, Any]) -> tuple[bool, str | None]:
    tags = _tag_map(span.get("tags"))
    error = str(tags.get("error", "")).lower() == "true"
    error = error or str(tags.get("otel.status_code", "")).upper() == "ERROR"
    message = (
        tags.get("error.message")
        or tags.get("exception.message")
        or tags.get("otel.status_description")
    )
    logs = span.get("logs", [])
    for event in logs if isinstance(logs, list) else []:
        fields = _tag_map(event.get("fields")) if isinstance(event, dict) else {}
        if str(fields.get("event", "")).lower() == "error" or str(fields.get("level", "")).lower() == "error":
            error = True
            message = message or fields.get("message") or fields.get("error")
    return error, str(message) if message is not None else None


def _span_service(span: dict[str, Any], processes: dict[str, Any]) -> str:
    process = processes.get(span.get("processID"), {})
    if isinstance(process, dict):
        tags = _tag_map(process.get("tags"))
        service = tags.get("service.name") or tags.get("service_name")
        if service:
            return str(service)
    return "unknown"


def _duration_ms(span: dict[str, Any]) -> float | None:
    duration = span.get("duration")
    try:
        return float(duration) / 1000.0
    except (TypeError, ValueError):
        return None


def _span_kind(span: dict[str, Any]) -> str:
    tags = _tag_map(span.get("tags"))
    kind = tags.get("span.kind") or tags.get("otel.kind") or ""
    return str(kind).casefold()


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    spans = [span for span in trace.get("spans", []) if isinstance(span, dict)]
    processes = trace.get("processes", {})
    if not isinstance(processes, dict):
        processes = {}
    by_id = {str(span.get("spanID")): span for span in spans}
    parents: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    for span in spans:
        span_id = str(span.get("spanID", ""))
        references = span.get("references", [])
        for reference in references if isinstance(references, list) else []:
            if not isinstance(reference, dict) or reference.get("refType") != "CHILD_OF":
                continue
            parent_id = str(reference.get("spanID", ""))
            parents[span_id] = parent_id
            children.setdefault(parent_id, []).append(span_id)
    errors = {span_id: _span_error(span) for span_id, span in by_id.items()}
    roots = [span for span in spans if str(span.get("spanID", "")) not in parents]
    root = min(roots, key=lambda span: int(span.get("startTime", 0))) if roots else None
    deepest = []
    for span_id, (is_error, message) in errors.items():
        if not is_error:
            continue
        pending = list(children.get(span_id, []))
        descendant_has_error = False
        visited = set()
        while pending:
            child_id = pending.pop()
            if child_id in visited:
                continue
            visited.add(child_id)
            if errors.get(child_id, (False, None))[0]:
                descendant_has_error = True
                break
            pending.extend(children.get(child_id, []))
        if not descendant_has_error:
            span = by_id[span_id]
            deepest.append(
                {
                    "span_id": span_id,
                    "service": _span_service(span, processes),
                    "operation": span.get("operationName"),
                    "error_message": message,
                }
            )
    edges = []
    for child_id, parent_id in parents.items():
        parent, child = by_id.get(parent_id), by_id.get(child_id)
        if parent is None or child is None:
            continue
        if _span_kind(parent) != "client" or _span_kind(child) != "server":
            continue
        parent_ms, child_ms = _duration_ms(parent), _duration_ms(child)
        if parent_ms is None or child_ms is None:
            continue
        edges.append(
            {
                "from": {
                    "service": _span_service(parent, processes),
                    "operation": parent.get("operationName"),
                },
                "to": {
                    "service": _span_service(child, processes),
                    "operation": child.get("operationName"),
                },
                "client_duration_ms": parent_ms,
                "server_duration_ms": child_ms,
                "network_gap_ms": parent_ms - child_ms,
            }
        )
    trace_duration = None
    start_times = [
        float(span["startTime"])
        for span in spans
        if isinstance(span.get("startTime"), (int, float))
        and isinstance(span.get("duration"), (int, float))
    ]
    end_times = [
        float(span["startTime"]) + float(span["duration"])
        for span in spans
        if isinstance(span.get("startTime"), (int, float))
        and isinstance(span.get("duration"), (int, float))
    ]
    if start_times and end_times:
        trace_duration = (max(end_times) - min(start_times)) / 1000.0
    return {
        "trace_id": trace.get("traceID"),
        "root_operation": root.get("operationName") if root else None,
        "duration_ms": trace_duration,
        "spans": [
            {
                "span_id": span.get("spanID"),
                "parent_span_id": parents.get(str(span.get("spanID"))),
                "service": _span_service(span, processes),
                "operation": span.get("operationName"),
                "duration_ms": _duration_ms(span),
                "error": errors.get(str(span.get("spanID")), (False, None))[0],
                "error_message": errors.get(str(span.get("spanID")), (False, None))[1],
            }
            for span in spans
        ],
        "deepest_error_span": deepest[0] if deepest else None,
        "deepest_error_spans": deepest,
        "edges": edges,
    }


def list_services(backend: TelemetryBackend | None = None) -> ToolResult:
    source = "Jaeger GET /api/v3/services"
    try:
        services = _backend(backend).jaeger_services()
        safe, removed = redact(services)
        return success(
            f"Jaeger returned {len(safe)} service(s)",
            {"services": safe, "redacted_items": removed},
            source,
        )
    except BackendError as exc:
        return failure(f"Jaeger service lookup failed: {exc}", source)


def find_error_traces(
    service: str,
    lookback: str = "15m",
    limit: int = 20,
    backend: TelemetryBackend | None = None,
) -> ToolResult:
    source = (
        f"Jaeger GET /api/v3/trace-summaries?query.serviceName={service}"
        f"&lookback={lookback}&query.searchDepth={limit}, then GET /api/v3/traces/{{traceId}}"
    )
    try:
        if not isinstance(service, str) or not service.strip() or "flagd" in service.casefold():
            raise ValueError("service must be a non-flagd service name")
        if not isinstance(lookback, str) or not _LOOKBACK.fullmatch(lookback):
            raise ValueError("lookback must be a positive Jaeger duration, such as '15m'")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        traces = _backend(backend).jaeger_traces(service, lookback, limit)
        errors = []
        for trace in traces:
            processes = trace.get("processes", {})
            processes = processes if isinstance(processes, dict) else {}
            spans = [span for span in trace.get("spans", []) if isinstance(span, dict)]
            roots = []
            referenced = {
                str(reference.get("spanID"))
                for candidate in spans
                for reference in (
                    candidate.get("references", [])
                    if isinstance(candidate.get("references"), list)
                    else []
                )
                if isinstance(reference, dict) and reference.get("refType") == "CHILD_OF"
            }
            roots = [span for span in spans if str(span.get("spanID")) not in referenced]
            root_op = roots[0].get("operationName") if roots else None
            trace_duration = _trace_summary(trace)["duration_ms"]
            for span in spans:
                is_error, message = _span_error(span)
                if not is_error:
                    continue
                errors.append(
                    {
                        "trace_id": trace.get("traceID"),
                        "root_operation": root_op,
                        "error_span": {
                            "service": _span_service(span, processes),
                            "operation": span.get("operationName"),
                            "message": message,
                        },
                        "duration_ms": trace_duration,
                    }
                )
        safe, removed = redact(errors)
        return success(
            f"Found {len(safe)} error span(s) in recent {service} traces",
            {"traces": safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Jaeger error-trace lookup failed: {exc}", source)


def get_trace(trace_id: str, backend: TelemetryBackend | None = None) -> ToolResult:
    source = f"Jaeger GET /api/v3/traces/{trace_id}"
    try:
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")
        trace = _backend(backend).jaeger_trace(trace_id)
        summary = _trace_summary(trace)
        safe, removed = redact(summary)
        return success(
            f"Summarized trace {trace_id} with {len(safe['spans'])} span(s)",
            {**safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Jaeger trace lookup failed: {exc}", source)