from __future__ import annotations

import re
from collections import Counter
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
from agent.tools.sanitize import redact

DENYLIST = frozenset({"flagd", "flagd-ui"})
_live = LiveBackend()
_LEVEL = re.compile(r"\b(trace|debug|info|warn(?:ing)?|error|fatal|critical)\b", re.IGNORECASE)


def _backend(backend: TelemetryBackend | None) -> TelemetryBackend:
    return backend if backend is not None else _live


def _container_for_service(backend: TelemetryBackend, service: str) -> str:
    matches = []
    for row in backend.docker_ps():
        names = row.get("Names") or row.get("Name") or ""
        image = row.get("Image") or ""
        candidates = [part.strip().lstrip("/") for part in str(names).split(",")]
        candidates.append(str(image).split(":")[0].rsplit("/", 1)[-1])
        if any(service.casefold() == candidate.casefold() for candidate in candidates):
            identifier = row.get("ID") or row.get("Id") or row.get("Names") or row.get("Name")
            if identifier:
                matches.append(str(identifier))
    if not matches:
        raise BackendError(f"no Docker container matches service {service!r}")
    if len(matches) > 1:
        raise BackendError(f"multiple Docker containers match service {service!r}")
    return matches[0]


def get_logs(
    service: str,
    minutes: int = 10,
    grep: str | None = None,
    level: str | None = None,
    max_lines: int = 60,
    backend: TelemetryBackend | None = None,
) -> ToolResult:
    source = f"docker logs --since {minutes}m --timestamps <{service} container>"
    try:
        validate_minutes(minutes, maximum=1440)
        if not isinstance(service, str) or not service.strip() or any(
            blocked in service.casefold() for blocked in DENYLIST
        ):
            raise ValueError(f"service {service!r} is not permitted for log collection")
        if isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 1 <= max_lines <= 1000:
            raise ValueError("max_lines must be between 1 and 1000")
        if level and (
            not isinstance(level, str)
            or level.casefold() not in {"trace", "debug", "info", "warn", "warning", "error", "fatal", "critical"}
        ):
            raise ValueError("level must be a known log level")
        if grep is not None and not isinstance(grep, str):
            raise ValueError("grep must be a string when provided")
        client = _backend(backend)
        container = _container_for_service(client, service)
        source = f"docker logs --since {minutes}m --timestamps {container}"
        lines = client.docker_logs(container, minutes)
        safe, removed = redact(lines)
        filtered = []
        for line in safe:
            if grep and grep.casefold() not in line.casefold():
                continue
            if level:
                match = _LEVEL.search(line)
                requested = level.casefold()
                observed = match.group(1).casefold() if match else ""
                if requested == "warn":
                    requested = "warning"
                if observed == "warn":
                    observed = "warning"
                if not match or observed != requested:
                    continue
            filtered.append(line)
        counts = Counter(filtered)
        first_seen = {line: index for index, line in enumerate(filtered)}
        severity = {
            "fatal": 0,
            "critical": 0,
            "error": 1,
            "warning": 2,
            "warn": 2,
            "info": 3,
            "debug": 4,
            "trace": 5,
        }
        records = [
            {"line": line, "count": count, "level": (_LEVEL.search(line).group(1).casefold() if _LEVEL.search(line) else None)}
            for line, count in counts.items()
        ]
        records.sort(
            key=lambda record: (
                severity.get(record["level"], 6),
                -record["count"],
                first_seen[record["line"]],
            )
        )
        records = records[:max_lines]
        return success(
            f"Collected {len(filtered)} matching log line(s) from {service}",
            {
                "service": service,
                "container": container,
                "lines": records,
                "lines_returned": len(records),
                "redacted_lines": removed,
            },
            source,
        )
    except (BackendError, ValueError) as exc:
        return failure(f"Log collection failed: {exc}", source)