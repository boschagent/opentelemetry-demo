from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


DEFAULT_ERROR_RATE_TOLERANCE = 0.01
DEFAULT_LATENCY_RELATIVE_TOLERANCE = 0.2
DEFAULT_LATENCY_ABSOLUTE_TOLERANCE_MS = 50.0


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _metrics_for_service(
    health: Mapping[str, Any],
    service: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    if health.get("ok") is False:
        return None, "Health telemetry query failed"
    data = health.get("data", health)
    if not isinstance(data, Mapping):
        return None, "Health telemetry data is not an object"
    services = data.get("services")
    if not isinstance(services, list):
        return None, "Health telemetry does not contain a services list"
    matches = [
        item
        for item in services
        if isinstance(item, Mapping) and item.get("service") == service
    ]
    if len(matches) != 1:
        return None, f"Expected one health record for {service!r}, found {len(matches)}"
    return matches[0], None


def verify_recovery(
    before_health: Mapping[str, Any],
    after_health: Mapping[str, Any],
    service: str,
    *,
    error_rate_tolerance: float = DEFAULT_ERROR_RATE_TOLERANCE,
    latency_relative_tolerance: float = DEFAULT_LATENCY_RELATIVE_TOLERANCE,
    latency_absolute_tolerance_ms: float = DEFAULT_LATENCY_ABSOLUTE_TOLERANCE_MS,
) -> dict[str, Any]:
    """Compare post-action RED metrics with the pre-action historical baseline."""
    if not isinstance(before_health, Mapping) or not isinstance(after_health, Mapping):
        raise ValueError("before_health and after_health must be mappings")
    if not isinstance(service, str) or not service.strip():
        raise ValueError("service must be a non-empty string")
    for name, value in (
        ("error_rate_tolerance", error_rate_tolerance),
        ("latency_relative_tolerance", latency_relative_tolerance),
        ("latency_absolute_tolerance_ms", latency_absolute_tolerance_ms),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} must be a finite non-negative number")

    before, before_error = _metrics_for_service(before_health, service)
    after, after_error = _metrics_for_service(after_health, service)
    if before_error or after_error:
        return {
            "status": "unverified",
            "recovered": False,
            "service": service,
            "checks": {},
            "errors": [error for error in (before_error, after_error) if error],
        }
    assert before is not None and after is not None

    checks: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    recovered = True
    unverified = False
    for metric_name, absolute_tolerance in (
        ("error_rate", float(error_rate_tolerance)),
        ("p95_latency_ms", float(latency_absolute_tolerance_ms)),
    ):
        before_metric = before.get(metric_name)
        after_metric = after.get(metric_name)
        if not isinstance(before_metric, Mapping) or not isinstance(after_metric, Mapping):
            errors.append(f"Missing {metric_name} values before or after action")
            recovered = False
            unverified = True
            continue

        baseline = _finite_number(before_metric.get("baseline"))
        pre_action = _finite_number(before_metric.get("current"))
        post_action = _finite_number(after_metric.get("current"))
        if baseline is None or pre_action is None or post_action is None:
            errors.append(f"Invalid or missing numeric values for {metric_name}")
            recovered = False
            unverified = True
            continue
        if baseline < 0 or pre_action < 0 or post_action < 0:
            errors.append(f"{metric_name} values must not be negative")
            recovered = False
            unverified = True
            continue

        if metric_name == "error_rate":
            allowed_maximum = baseline + absolute_tolerance
        else:
            relative_tolerance = abs(baseline) * float(latency_relative_tolerance)
            allowed_maximum = baseline + max(absolute_tolerance, relative_tolerance)
        passed = post_action <= allowed_maximum
        checks[metric_name] = {
            "baseline": baseline,
            "before": pre_action,
            "after": post_action,
            "allowed_maximum": allowed_maximum,
            "passed": passed,
        }
        if not passed:
            recovered = False
            errors.append(
                f"{metric_name} remains above its baseline tolerance "
                f"({post_action} > {allowed_maximum})"
            )

    return {
        "status": "verified" if recovered else "unverified" if unverified else "not_recovered",
        "recovered": recovered,
        "service": service,
        "checks": checks,
        "errors": errors,
    }


def verify_action_recovery(
    action_result: Mapping[str, Any],
    before_health: Mapping[str, Any],
    after_health: Mapping[str, Any],
    *,
    rollback_approval_response: str | None = None,
    health_after_rollback: Mapping[str, Any] | None = None,
    api_client: Any = None,
    kill_switch_value: str | None = None,
    error_rate_tolerance: float = DEFAULT_ERROR_RATE_TOLERANCE,
    latency_relative_tolerance: float = DEFAULT_LATENCY_RELATIVE_TOLERANCE,
    latency_absolute_tolerance_ms: float = DEFAULT_LATENCY_ABSOLUTE_TOLERANCE_MS,
) -> dict[str, Any]:
    """Verify an action and, only when configured and explicitly approved, roll it back."""
    if not isinstance(action_result, Mapping):
        raise ValueError("action_result must be a mapping")
    if action_result.get("status") != "submitted":
        return {
            "status": "not_applicable",
            "recovered": False,
            "errors": ["Only a submitted action can be recovery-verified"],
        }
    proposal = action_result.get("proposal")
    if not isinstance(proposal, Mapping):
        return {
            "status": "unverified",
            "recovered": False,
            "errors": ["Submitted action result does not contain a proposal"],
        }
    parameters = proposal.get("parameters")
    service = parameters.get("service") if isinstance(parameters, Mapping) else None
    if not isinstance(service, str):
        return {
            "status": "unverified",
            "recovered": False,
            "errors": ["Submitted action does not identify a service to verify"],
        }

    verification = verify_recovery(
        before_health,
        after_health,
        service,
        error_rate_tolerance=error_rate_tolerance,
        latency_relative_tolerance=latency_relative_tolerance,
        latency_absolute_tolerance_ms=latency_absolute_tolerance_ms,
    )
    if verification["recovered"]:
        return {
            "status": "recovery_verified",
            "recovered": True,
            "verification": verification,
            "rollback": None,
            "escalated": False,
        }

    from agent.act import action_api, approval, killswitch
    from agent.act.runbook import load_runbook, validate_action

    runbook = load_runbook()
    action_name = proposal.get("action")
    action_definition = (
        runbook["actions"].get(action_name)
        if isinstance(action_name, str)
        else None
    )
    rollback = action_definition.get("rollback") if isinstance(action_definition, Mapping) else None
    if not isinstance(rollback, Mapping):
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {
                "status": "unavailable",
                "reason": "The approved runbook defines no rollback for this action",
            },
            "escalated": True,
            "stop_further_actions": True,
        }

    rollback_action = rollback.get("action")
    rollback_parameters = rollback.get("parameters")
    if not isinstance(rollback_action, str):
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {"status": "unavailable", "reason": "Runbook rollback is malformed"},
            "escalated": True,
            "stop_further_actions": True,
        }
    validated_parameters, parameter_error = validate_action(
        rollback_action,
        rollback_parameters,
        runbook=runbook,
    )
    if parameter_error:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {"status": "unavailable", "reason": parameter_error},
            "escalated": True,
            "stop_further_actions": True,
        }
    assert validated_parameters is not None
    rollback_proposal = {
        "action": rollback_action,
        "parameters": validated_parameters,
        "reason": f"Recovery verification failed for {service}",
    }
    rollback_approval = approval.request_approval(
        rollback_proposal,
        rollback_approval_response,
    )
    if not rollback_approval["approved"]:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {
                "status": "approval_required",
                "proposal": rollback_proposal,
                "approval": rollback_approval,
            },
            "escalated": True,
            "stop_further_actions": True,
        }

    try:
        killswitch.check_kill_switch(kill_switch_value)
    except killswitch.KillSwitchActive as exc:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {"status": "kill_switch_blocked", "error": str(exc)},
            "escalated": True,
            "stop_further_actions": True,
        }
    except ValueError as exc:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {"status": "blocked", "error": str(exc)},
            "escalated": True,
            "stop_further_actions": True,
        }

    try:
        api_result = action_api.execute_action(
            rollback_action,
            validated_parameters,
            client=api_client,
        )
    except action_api.ActionAPIConfigurationError as exc:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {"status": "failed", "error": str(exc)},
            "escalated": True,
            "stop_further_actions": True,
        }

    if health_after_rollback is None:
        return {
            "status": "recovery_failed",
            "recovered": False,
            "verification": verification,
            "rollback": {
                "status": "submitted",
                "approval": rollback_approval,
                "api_response": api_result,
                "verification": "not_run",
            },
            "escalated": True,
            "stop_further_actions": True,
        }

    rollback_verification = verify_recovery(
        before_health,
        health_after_rollback,
        service,
        error_rate_tolerance=error_rate_tolerance,
        latency_relative_tolerance=latency_relative_tolerance,
        latency_absolute_tolerance_ms=latency_absolute_tolerance_ms,
    )
    return {
        "status": "recovery_verified_after_rollback"
        if rollback_verification["recovered"]
        else "recovery_failed",
        "recovered": rollback_verification["recovered"],
        "verification": verification,
        "rollback": {
            "status": "verified" if rollback_verification["recovered"] else "failed",
            "approval": rollback_approval,
            "api_response": api_result,
            "verification": rollback_verification,
        },
        "escalated": not rollback_verification["recovered"],
        "stop_further_actions": True,
    }