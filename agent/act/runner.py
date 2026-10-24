from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from agent.act import action_api, approval, killswitch
from agent.act.runbook import load_runbook, validate_action


DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def run_action(
    investigation: Mapping[str, Any],
    action_name: str,
    parameters: Any,
    *,
    dry_run: bool = True,
    approval_response: str | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    api_client: action_api.ActionAPIClient | None = None,
    kill_switch_value: str | None = None,
) -> dict[str, Any]:
    """Propose or execute a runbook action after all required safety gates."""
    if not isinstance(investigation, Mapping):
        raise ValueError("investigation must be a mapping")
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if (
        isinstance(confidence_threshold, bool)
        or not isinstance(confidence_threshold, (int, float))
        or not math.isfinite(confidence_threshold)
        or not 0.0 <= confidence_threshold <= 1.0
    ):
        raise ValueError("confidence_threshold must be a finite number between 0 and 1")

    runbook = load_runbook()
    validated_parameters, parameter_error = validate_action(
        action_name,
        parameters,
        runbook=runbook,
    )
    if parameter_error:
        return {"status": "rejected", "error": parameter_error}
    assert validated_parameters is not None

    diagnosis = investigation.get("diagnosis")
    review = investigation.get("review")
    confidence = diagnosis.get("confidence") if isinstance(diagnosis, Mapping) else None
    if not isinstance(diagnosis, Mapping):
        diagnosis = {}
    if not isinstance(review, Mapping):
        review = {}
    conditions = {
        "investigation_status_is_diagnosis": (
            investigation.get("status") == "diagnosis",
            "Action requires an investigation with status 'diagnosis'",
        ),
        "diagnosis_is_not_escalated": (
            diagnosis.get("escalated") is False,
            "Escalated diagnoses cannot authorize actions",
        ),
        "reviewer_verdict_is_agree": (
            review.get("status") == "reviewed" and review.get("verdict") == "agree",
            "Action requires an agreeing reviewer verdict",
        ),
        "confidence_meets_threshold": (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and math.isfinite(confidence)
            and confidence >= confidence_threshold,
            f"Diagnosis confidence must meet threshold {confidence_threshold:.3f}",
        ),
        "service_matches_diagnosed_component": (
            action_name != "restart_service"
            or (
                isinstance(diagnosis.get("component"), str)
                and validated_parameters["service"].casefold()
                == diagnosis["component"].casefold()
            ),
            "Restart target must match the diagnosed component",
        ),
    }
    precondition_failures: list[str] = []
    for condition in runbook["actions"][action_name]["preconditions"]:
        if condition not in conditions:
            return {
                "status": "blocked",
                "error": f"Unsupported runbook precondition: {condition}",
            }
        passed, message = conditions[condition]
        if not passed:
            precondition_failures.append(message)
    if precondition_failures:
        return {
            "status": "blocked",
            "error": "; ".join(precondition_failures),
            "precondition_failures": precondition_failures,
        }

    proposal = {
        "action": action_name,
        "description": runbook["actions"][action_name]["description"],
        "rollback": runbook["actions"][action_name]["rollback"],
        "parameters": validated_parameters,
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "reviewer_verdict": review["verdict"],
        "reviewer_reason": review.get("reason", ""),
        "dry_run": dry_run,
    }
    if dry_run:
        return {
            "status": "dry_run",
            "proposal": proposal,
            "message": "Dry run only; no approval was requested and no API call was made",
        }

    approval_result = approval.request_approval(proposal, approval_response)
    if not approval_result["approved"]:
        return {
            "status": "approval_required",
            "proposal": proposal,
            "approval": approval_result,
        }

    try:
        killswitch.check_kill_switch(kill_switch_value)
    except killswitch.KillSwitchActive as exc:
        return {
            "status": "kill_switch_blocked",
            "proposal": proposal,
            "approval": approval_result,
            "error": str(exc),
        }

    api_result = action_api.execute_action(
        action_name,
        validated_parameters,
        client=api_client,
    )
    return {
        "status": "submitted",
        "proposal": proposal,
        "approval": approval_result,
        "api_response": api_result,
        "message": (
            "The Action API was called; recovery has not been verified. "
            "Do not report the incident resolved until verification completes."
        ),
    }
