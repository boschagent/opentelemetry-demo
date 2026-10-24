from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.backends.live import LiveBackend
from agent.tools._common import (
    BackendError,
    TelemetryBackend,
    ToolResult,
    failure,
    success,
    validate_minutes,
)
from agent.tools.prom import container_resources
from agent.tools.sanitize import redact

_live = LiveBackend()


def _backend(backend: TelemetryBackend | None) -> TelemetryBackend:
    return backend if backend is not None else _live


def _identifier(row: dict[str, Any]) -> str | None:
    value = row.get("ID") or row.get("Id") or row.get("Names") or row.get("Name")
    return str(value) if value else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001-01-01"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _inspect_containers(
    backend: TelemetryBackend, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    identifiers = [identifier for row in rows if (identifier := _identifier(row))]
    inspected = backend.docker_inspect(identifiers)
    result = []
    for row in rows:
        identifier = _identifier(row)
        if identifier is None:
            continue
        detail = next(
            (
                item for item in inspected
                if str(item.get("Id", "")).startswith(identifier)
                or identifier.lstrip("/") in [
                    str(name).lstrip("/")
                    for name in item.get("Names", [item.get("Name", "")])
                ]
            ),
            None,
        )
        if detail is None:
            raise BackendError(f"docker inspect returned no details for {identifier!r}")
        result.append(detail)
    return result


def _container_record(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("State", {})
    config = item.get("Config", {})
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    labels = config.get("Labels", {})
    labels = labels if isinstance(labels, dict) else {}
    health = state.get("Health", {})
    health_status = health.get("Status") if isinstance(health, dict) else None
    status = state.get("Status", "unknown")
    return {
        "id": item.get("Id"),
        "name": str(item.get("Name", "")).lstrip("/"),
        "image": config.get("Image"),
        "compose_project": labels.get("com.docker.compose.project"),
        "compose_service": labels.get("com.docker.compose.service"),
        "state": status,
        "health": health_status,
        "restart_count": item.get("RestartCount", 0),
        "started_at": state.get("StartedAt"),
        "created_at": item.get("Created"),
        "oom_killed": state.get("OOMKilled", False),
        "exit_code": state.get("ExitCode"),
        "problem": status != "running" or (health_status is not None and health_status != "healthy"),
    }


def container_health(backend: TelemetryBackend | None = None) -> ToolResult:
    source = "docker ps -a --format '{{json .}}', docker inspect, and Prometheus container_* resource metrics"
    try:
        client = _backend(backend)
        rows = client.docker_ps()
        identifiers = [identifier for row in rows if (identifier := _identifier(row))]
        records = [_container_record(item) for item in _inspect_containers(client, rows)]
        problems = [item for item in records if item["problem"] or item["restart_count"]]
        resources = container_resources(backend=client)
        inspect_command = f"docker inspect {' '.join(identifiers)}" if identifiers else "docker inspect (no containers)"
        source = f"docker ps -a --format '{{{{json .}}}}'; {inspect_command}; {resources['source']}"
        safe, removed = redact({"containers": records, "attention": problems})
        return success(
            f"Inspected {len(records)} container(s); {len(problems)} need attention",
            {
                **safe,
                "resource_metrics": resources,
                "redacted_items": removed,
            },
            source,
        )
    except BackendError as exc:
        return failure(f"Container health check failed: {exc}", source)


def _read_action_log(path: Path, cutoff: datetime) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BackendError(f"Could not read action log {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackendError(f"Invalid action log JSON on line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise BackendError(f"Action log line {line_number} must be a JSON object")
        timestamp = _parse_time(event.get("timestamp") or event.get("ts"))
        if timestamp and timestamp >= cutoff:
            events.append({"kind": "agent_action", **event})
    return events


def deploy_history(
    minutes: int = 120,
    backend: TelemetryBackend | None = None,
    action_log: str | Path | None = None,
) -> ToolResult:
    source = "docker inspect creation/start timestamps and optional agent action log"
    try:
        validate_minutes(minutes, maximum=10080)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        client = _backend(backend)
        rows = client.docker_ps()
        identifiers = [identifier for row in rows if (identifier := _identifier(row))]
        events = []
        for item in _inspect_containers(client, rows):
            state = item.get("State", {})
            state = state if isinstance(state, dict) else {}
            name = str(item.get("Name", "")).lstrip("/")
            config = item.get("Config", {})
            image = config.get("Image") if isinstance(config, dict) else None
            restart_count = item.get("RestartCount", 0)
            for event_name, raw_time in (
                ("created", item.get("Created")),
                ("restarted" if restart_count else "started", state.get("StartedAt")),
                ("finished", state.get("FinishedAt")),
            ):
                timestamp = _parse_time(raw_time)
                if timestamp and timestamp >= cutoff:
                    events.append(
                        {
                            "kind": event_name,
                            "container": name,
                            "timestamp": timestamp.isoformat(),
                            "image": image,
                        }
                    )
        log_path = Path(action_log) if action_log is not None else (
            Path(os.environ["ACTION_LOG_PATH"]) if os.getenv("ACTION_LOG_PATH") else None
        )
        if log_path is not None:
            events.extend(_read_action_log(log_path, cutoff))
        inspect_command = f"docker inspect {' '.join(identifiers)}" if identifiers else "docker inspect (no containers)"
        source = f"docker ps -a --format '{{{{json .}}}}'; {inspect_command}"
        if log_path is not None:
            source += f"; read {log_path}"
        events.sort(key=lambda event: event.get("timestamp", ""))
        safe, removed = redact({"events": events, "window_minutes": minutes})
        return success(
            f"Found {len(events)} deployment or restart event(s) in the last {minutes} minute(s)",
            {**safe, "redacted_items": removed},
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Deployment-history lookup failed: {exc}", source)