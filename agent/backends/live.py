from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.tools._common import BackendError


class LiveBackend:
    """Read-only adapters for Prometheus, Jaeger, and the local Docker CLI."""

    def __init__(
        self,
        prometheus_url: str | None = None,
        jaeger_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.prometheus_url = (prometheus_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")).rstrip("/")
        self.jaeger_url = (
            jaeger_url or os.getenv("JAEGER_URL", "http://localhost:8080/jaeger/ui")
        ).rstrip("/")
        self.timeout = timeout

    def _get_json(self, url: str) -> Any:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendError(f"GET {url} failed: {exc}") from exc
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise BackendError(f"GET {url} returned an API error: {payload.get('error', 'unknown error')}")
        return payload

    def _prometheus(self, endpoint: str, params: dict[str, str]) -> Any:
        url = f"{self.prometheus_url}/api/v1/{endpoint}?{urllib.parse.urlencode(params)}"
        payload = self._get_json(url)
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise BackendError(f"Prometheus returned an invalid response for {endpoint}")
        return payload.get("data", {})

    def prom_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        params = {"query": query}
        if at is not None:
            params["time"] = str(at.timestamp())
        data = self._prometheus("query", params)
        if not isinstance(data, dict):
            raise BackendError("Prometheus instant query data was not an object")
        return data

    def prom_query_range(
        self, query: str, start: datetime, end: datetime, step: str
    ) -> dict[str, Any]:
        data = self._prometheus(
            "query_range",
            {
                "query": query,
                "start": str(start.timestamp()),
                "end": str(end.timestamp()),
                "step": step,
            },
        )
        if not isinstance(data, dict):
            raise BackendError("Prometheus range query data was not an object")
        return data

    def prom_metric_names(self) -> list[str]:
        data = self._prometheus("label/__name__/values", {})
        if not isinstance(data, list) or not all(isinstance(name, str) for name in data):
            raise BackendError("Prometheus metric-name response was invalid")
        return data

    def jaeger_services(self) -> list[str]:
        payload = self._get_json(f"{self.jaeger_url}/api/v3/services")
        services = payload.get("services") if isinstance(payload, dict) else None
        if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
            raise BackendError("Jaeger services response was invalid")
        return services

    def jaeger_traces(self, service: str, lookback: str, limit: int) -> list[dict[str, Any]]:
        match = re.fullmatch(r"([1-9][0-9]*)(ms|s|m|h|d|w|y)", lookback)
        if match is None:
            raise BackendError(f"Unsupported Jaeger lookback {lookback!r}")
        amount, unit = int(match.group(1)), match.group(2)
        seconds_per_unit = {
            "ms": 0.001,
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 604800,
            "y": 31536000,
        }
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=amount * seconds_per_unit[unit])
        params = urllib.parse.urlencode(
            {
                "query.serviceName": service,
                "query.startTimeMin": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "query.startTimeMax": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "query.searchDepth": str(limit),
            }
        )
        payload = self._get_json(f"{self.jaeger_url}/api/v3/trace-summaries?{params}")
        summaries = payload.get("summaries") if isinstance(payload, dict) else None
        if not isinstance(summaries, list) or not all(isinstance(item, dict) for item in summaries):
            raise BackendError("Jaeger trace-summary response was invalid")
        traces = []
        for summary in summaries:
            if summary.get("errorSpanCount", 0) <= 0:
                continue
            trace_id = summary.get("traceId")
            if isinstance(trace_id, str) and trace_id:
                traces.append(self.jaeger_trace(trace_id))
        return traces

    def jaeger_trace(self, trace_id: str) -> dict[str, Any]:
        payload = self._get_json(
            f"{self.jaeger_url}/api/v3/traces/{urllib.parse.quote(trace_id, safe='')}"
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict) and isinstance(result.get("resourceSpans"), list):
            return jaeger_trace_from_otlp(result, trace_id)
        traces = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(traces, list) and traces and isinstance(traces[0], dict):
            return traces[0]
        if isinstance(result, dict) and isinstance(result.get("spans"), list):
            return result
        raise BackendError(f"Jaeger returned no trace with id {trace_id}")

    def docker_ps(self) -> list[dict[str, Any]]:
        result = self._docker(["ps", "-a", "--format", "{{json .}}"])
        containers = []
        for line in result.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BackendError(f"docker ps returned invalid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise BackendError("docker ps returned a non-object row")
            containers.append(item)
        return containers

    def docker_inspect(self, identifiers: list[str]) -> list[dict[str, Any]]:
        if not identifiers:
            return []
        output = self._docker(["inspect", *identifiers])
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise BackendError(f"docker inspect returned invalid JSON: {exc}") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise BackendError("docker inspect response was invalid")
        return payload

    def docker_logs(self, container: str, minutes: int) -> list[str]:
        try:
            result = subprocess.run(
                ["docker", "logs", "--since", f"{minutes}m", "--timestamps", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"docker logs failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise BackendError(f"docker logs failed: {detail}")
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        return output.splitlines()

    def _docker(self, arguments: list[str]) -> str:
        try:
            result = subprocess.run(
                ["docker", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"docker {' '.join(arguments[:2])} failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise BackendError(f"docker {' '.join(arguments[:2])} failed: {detail}")
        return result.stdout


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_before(minutes: int) -> datetime:
    return utc_now() - timedelta(minutes=minutes)


def _otlp_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in value:
            return value[key]
    return json.dumps(value, sort_keys=True)


def _otlp_attributes(attributes: Any) -> list[dict[str, Any]]:
    if not isinstance(attributes, list):
        return []
    return [
        {"key": item["key"], "value": _otlp_value(item.get("value"))}
        for item in attributes
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    ]


def jaeger_trace_from_otlp(result: dict[str, Any], trace_id: str) -> dict[str, Any]:
    processes: dict[str, Any] = {}
    spans = []
    kind_names = {
        0: "unspecified",
        1: "internal",
        2: "server",
        3: "client",
        4: "producer",
        5: "consumer",
    }
    for resource_index, resource_span in enumerate(result["resourceSpans"]):
        if not isinstance(resource_span, dict):
            continue
        process_id = f"resource-{resource_index}"
        resource = resource_span.get("resource", {})
        resource_attributes = _otlp_attributes(
            resource.get("attributes", []) if isinstance(resource, dict) else []
        )
        if not any(tag["key"] == "service.name" for tag in resource_attributes):
            resource_attributes.append({"key": "service.name", "value": "unknown"})
        processes[process_id] = {"tags": resource_attributes}
        scope_spans = resource_span.get("scopeSpans", resource_span.get("instrumentationLibrarySpans", []))
        for scope in scope_spans if isinstance(scope_spans, list) else []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans", []):
                if not isinstance(span, dict):
                    continue
                try:
                    start_ns = int(span.get("startTimeUnixNano", 0))
                    end_ns = int(span.get("endTimeUnixNano", start_ns))
                except (TypeError, ValueError):
                    raise BackendError("Jaeger OTLP span has an invalid timestamp")
                tags = _otlp_attributes(span.get("attributes", []))
                status = span.get("status", {})
                status_code = status.get("code") if isinstance(status, dict) else None
                is_error = status_code in (2, "2", "STATUS_CODE_ERROR")
                if is_error:
                    tags.extend(
                        [
                            {"key": "error", "value": True},
                            {"key": "otel.status_code", "value": "ERROR"},
                        ]
                    )
                    message = status.get("message")
                    if isinstance(message, str) and message:
                        tags.append({"key": "error.message", "value": message})
                kind_value = span.get("kind", 0)
                try:
                    kind = kind_names.get(int(kind_value), str(kind_value))
                except (TypeError, ValueError):
                    kind = str(kind_value)
                tags.append({"key": "span.kind", "value": kind})
                logs = []
                events = span.get("events", [])
                for event in events if isinstance(events, list) else []:
                    if not isinstance(event, dict):
                        continue
                    fields = _otlp_attributes(event.get("attributes", []))
                    event_name = event.get("name")
                    if isinstance(event_name, str):
                        fields.append({"key": "event", "value": event_name})
                    logs.append(
                        {
                            "name": event_name,
                            "timestamp": event.get("timeUnixNano"),
                            "fields": fields,
                        }
                    )
                span_id = str(span.get("spanId", ""))
                parent_id = span.get("parentSpanId")
                spans.append(
                    {
                        "spanID": span_id,
                        "operationName": span.get("name"),
                        "processID": process_id,
                        "startTime": start_ns // 1000,
                        "duration": max(0, end_ns - start_ns) // 1000,
                        "references": (
                            [{"refType": "CHILD_OF", "spanID": str(parent_id)}]
                            if isinstance(parent_id, str) and parent_id
                            else []
                        ),
                        "tags": tags,
                        "logs": logs,
                    }
                )
    return {"traceID": trace_id, "processes": processes, "spans": spans}