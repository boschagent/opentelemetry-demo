from __future__ import annotations

import re
from typing import Any

_FORBIDDEN_KEY = re.compile(r"feature_flag|flagd", re.IGNORECASE)
_FORBIDDEN_LINE = re.compile(r"feature.?flag|flagd|openfeature", re.IGNORECASE)


def redact(obj: Any) -> tuple[Any, int]:
    """Recursively remove feature-flag/flagd attributes, events, and log lines."""
    removed = 0

    def clean(value: Any) -> Any:
        nonlocal removed
        if isinstance(value, dict):
            for field in (
                "key",
                "name",
                "event",
                "service",
                "service_name",
                "service.name",
                "container",
                "container_name",
                "container.name",
                "job",
            ):
                field_value = value.get(field)
                if isinstance(field_value, str) and (
                    _FORBIDDEN_KEY.search(field_value)
                    or (field in {"event", "name"} and _FORBIDDEN_LINE.search(field_value))
                ):
                    removed += 1
                    return _DROP
            attribute_key = value.get("key")
            attribute_value = value.get("value")
            if (
                attribute_key in {"service.name", "service_name", "container.name", "container_name"}
                and isinstance(attribute_value, str)
                and _FORBIDDEN_KEY.search(attribute_value)
            ):
                removed += 1
                return _DROP
            result = {}
            for key, child in value.items():
                if _FORBIDDEN_KEY.search(str(key)):
                    removed += 1
                    continue
                cleaned = clean(child)
                if cleaned is _DROP:
                    if key == "metric":
                        return _DROP
                    continue
                result[key] = cleaned
            return result
        if isinstance(value, list):
            result = []
            for child in value:
                cleaned = clean(child)
                if cleaned is _DROP:
                    continue
                result.append(cleaned)
            return result
        if isinstance(value, str) and _FORBIDDEN_LINE.search(value):
            removed += 1
            return _DROP
        return value

    cleaned = clean(obj)
    return (None if cleaned is _DROP else cleaned), removed


class _Drop:
    pass


_DROP = _Drop()