from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class RunbookError(ValueError):
    """Raised when the local allowlisted runbook is missing or malformed."""


_RUNBOOK_PATH = Path(__file__).with_name("runbook.yaml")


def load_runbook(path: Path = _RUNBOOK_PATH) -> dict[str, Any]:
    """Load and minimally validate the action allowlist."""
    try:
        with path.open("r", encoding="utf-8") as runbook_file:
            document = yaml.safe_load(runbook_file)
    except OSError as exc:
        raise RunbookError(f"Could not read runbook: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RunbookError(f"Runbook is invalid YAML: {exc}") from exc

    if not isinstance(document, dict) or document.get("version") != 1:
        raise RunbookError("Runbook must be an object with version 1")
    actions = document.get("actions")
    if not isinstance(actions, dict) or not actions:
        raise RunbookError("Runbook must define at least one action")
    for name, definition in actions.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
            or not isinstance(definition, dict)
            or not isinstance(definition.get("description"), str)
            or not (
                definition.get("rollback") is None
                or isinstance(definition.get("rollback"), dict)
            )
            or not isinstance(definition.get("parameters"), dict)
            or not isinstance(definition.get("preconditions"), list)
        ):
            raise RunbookError(f"Runbook action {name!r} is malformed")
        if any(
            not isinstance(condition, str)
            for condition in definition["preconditions"]
        ):
            raise RunbookError(f"Runbook action {name!r} has malformed preconditions")
        for parameter, specification in definition["parameters"].items():
            if (
                not isinstance(parameter, str)
                or not isinstance(specification, dict)
                or specification.get("type") != "string"
                or not isinstance(specification.get("pattern"), str)
            ):
                raise RunbookError(
                    f"Runbook parameter {name}.{parameter} is malformed"
                )
            try:
                re.compile(specification["pattern"])
            except re.error as exc:
                raise RunbookError(
                    f"Runbook parameter {name}.{parameter} has an invalid pattern"
                ) from exc
    for name, definition in actions.items():
        rollback = definition.get("rollback")
        if rollback is None:
            continue
        if (
            set(rollback) != {"action", "parameters"}
            or
            not isinstance(rollback.get("action"), str)
            or rollback["action"] not in actions
            or not isinstance(rollback.get("parameters"), dict)
        ):
            raise RunbookError(f"Runbook action {name!r} has a malformed rollback")
        rollback_action = actions[rollback["action"]]
        rollback_parameters = rollback["parameters"]
        if set(rollback_parameters) != set(rollback_action["parameters"]):
            raise RunbookError(f"Runbook action {name!r} has invalid rollback parameters")
        for parameter, specification in rollback_action["parameters"].items():
            value = rollback_parameters[parameter]
            if (
                not isinstance(value, str)
                or not re.fullmatch(specification["pattern"], value)
            ):
                raise RunbookError(
                    f"Runbook action {name!r} has invalid rollback parameter {parameter!r}"
                )
    return document


def validate_action(
    name: str,
    parameters: Any,
    *,
    runbook: dict[str, Any] | None = None,
) -> tuple[dict[str, str] | None, str | None]:
    """Validate an action name and exact parameter shape against the allowlist."""
    document = runbook if runbook is not None else load_runbook()
    actions = document["actions"]
    definition = actions.get(name)
    if definition is None:
        return None, f"Action {name!r} is not in the approved runbook"
    if not isinstance(parameters, dict):
        return None, "Action parameters must be an object"

    specifications = definition["parameters"]
    unexpected = set(parameters) - set(specifications)
    missing = set(specifications) - set(parameters)
    if unexpected:
        return None, f"Unsupported action parameter(s): {', '.join(sorted(unexpected))}"
    if missing:
        return None, f"Missing action parameter(s): {', '.join(sorted(missing))}"

    validated: dict[str, str] = {}
    for parameter, specification in specifications.items():
        value = parameters[parameter]
        if not isinstance(value, str) or not re.fullmatch(specification["pattern"], value):
            return None, f"Action parameter {parameter!r} has an invalid value"
        validated[parameter] = value
    return validated, None
