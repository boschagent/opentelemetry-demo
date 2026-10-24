from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any


_DEFAULT_INCIDENTS_DIR = Path(__file__).resolve().parent.parent / "incidents"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_json_value(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON-serializable values") from exc


def _report_id(value: str | None, timestamp: datetime) -> str:
    if value is None:
        return f"incident-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("report_id must be 1-80 safe filename characters")
    return value


def _markdown_text(value: Any) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)
    else:
        rendered = str(value)
    return (
        rendered.replace("\r", " ")
        .replace("\n", " ")
        .replace("|", "\\|")
        .replace("```", "` ` `")
    )


def _build_timeline(
    investigation: Mapping[str, Any],
    action_result: Mapping[str, Any] | None,
    recovery_verification: Mapping[str, Any] | None,
    created_at: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"timestamp": created_at, "event": "Report created"}]
    evidence = investigation.get("evidence", [])
    if isinstance(evidence, list):
        for entry in evidence:
            if isinstance(entry, Mapping):
                events.append(
                    {
                        "timestamp": entry.get("timestamp"),
                        "event": "Evidence collected",
                        "evidence_id": entry.get("evidence_id"),
                        "tool": entry.get("tool"),
                    }
                )

    diagnosis = investigation.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        events.append(
            {
                "timestamp": None,
                "event": "Diagnosis produced",
                "status": investigation.get("status"),
                "component": diagnosis.get("component"),
            }
        )
    review = investigation.get("review")
    if isinstance(review, Mapping):
        events.append(
            {
                "timestamp": None,
                "event": "Diagnosis reviewed",
                "verdict": review.get("verdict"),
                "status": review.get("status"),
            }
        )
    if action_result is not None:
        approval = action_result.get("approval")
        events.append(
            {
                "timestamp": None,
                "event": "Action processed",
                "status": action_result.get("status"),
                "action": (
                    action_result.get("proposal", {}).get("action")
                    if isinstance(action_result.get("proposal"), Mapping)
                    else None
                ),
                "approval_status": (
                    approval.get("status") if isinstance(approval, Mapping) else None
                ),
            }
        )
    if recovery_verification is not None:
        events.append(
            {
                "timestamp": None,
                "event": "Recovery verification completed",
                "status": recovery_verification.get("status"),
                "recovered": recovery_verification.get("recovered"),
            }
        )

    events.sort(
        key=lambda event: (
            event.get("timestamp") is None,
            str(event.get("timestamp") or ""),
        )
    )
    return events


def build_report(
    incident_description: str,
    investigation: Mapping[str, Any],
    *,
    action_result: Mapping[str, Any] | None = None,
    recovery_verification: Mapping[str, Any] | None = None,
    prevention_suggestions: Sequence[str] = (),
    report_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable incident report from completed workflow results."""
    if not isinstance(incident_description, str) or not incident_description.strip():
        raise ValueError("incident_description must be a non-empty string")
    if not isinstance(investigation, Mapping):
        raise ValueError("investigation must be a mapping")
    if action_result is not None and not isinstance(action_result, Mapping):
        raise ValueError("action_result must be a mapping when provided")
    if recovery_verification is not None and not isinstance(recovery_verification, Mapping):
        raise ValueError("recovery_verification must be a mapping when provided")
    if isinstance(prevention_suggestions, (str, bytes)) or not isinstance(
        prevention_suggestions, Sequence
    ):
        raise ValueError("prevention_suggestions must be a sequence of strings")
    if any(not isinstance(item, str) or not item.strip() for item in prevention_suggestions):
        raise ValueError("prevention_suggestions must contain non-empty strings")

    timestamp = created_at if created_at is not None else _utc_now()
    if not isinstance(timestamp, datetime):
        raise ValueError("created_at must be a datetime")
    if timestamp.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    timestamp_text = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    diagnosis = investigation.get("diagnosis")
    diagnosis_data = dict(diagnosis) if isinstance(diagnosis, Mapping) else None
    review = investigation.get("review")
    review_data = dict(review) if isinstance(review, Mapping) else None
    evidence = investigation.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("investigation.evidence must be a list")

    report: dict[str, Any] = {
        "report_id": _report_id(report_id, timestamp),
        "created_at": timestamp_text,
        "incident": {"description": incident_description.strip()},
        "timeline": _build_timeline(
            investigation,
            action_result,
            recovery_verification,
            timestamp_text,
        ),
        "diagnosis": diagnosis_data,
        "evidence": evidence,
        "confidence": diagnosis_data.get("confidence") if diagnosis_data else None,
        "reviewer": review_data,
        "proposed_action": (
            action_result.get("proposal")
            if isinstance(action_result, Mapping)
            else None
        ),
        "human_approval": (
            action_result.get("approval")
            if isinstance(action_result, Mapping)
            else None
        ),
        "action_result": dict(action_result) if action_result is not None else None,
        "recovery_verification": (
            dict(recovery_verification) if recovery_verification is not None else None
        ),
        "prevention_suggestions": list(prevention_suggestions),
    }
    _validate_json_value(report, "report")
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render an incident report as Markdown without interpreting report text."""
    _validate_json_value(report, "report")
    timeline = report.get("timeline", [])
    evidence = report.get("evidence", [])
    suggestions = report.get("prevention_suggestions", [])
    diagnosis = report.get("diagnosis")
    reviewer = report.get("reviewer")
    proposed_action = report.get("proposed_action")
    approval = report.get("human_approval")
    action_result = report.get("action_result")
    verification = report.get("recovery_verification")

    def json_block(value: Any) -> str:
        serialized = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
        return "```json\n" + serialized.replace("```", "` ` `") + "\n```"

    lines = [
        f"# Incident Report: {_markdown_text(report.get('report_id'))}",
        "",
        f"- **Created:** {_markdown_text(report.get('created_at'))}",
        f"- **Incident:** {_markdown_text(report.get('incident', {}).get('description') if isinstance(report.get('incident'), Mapping) else None)}",
        "",
        "## Timeline",
        "",
    ]
    if isinstance(timeline, list) and timeline:
        lines.extend(
            f"- {_markdown_text(event.get('timestamp'))} -- {_markdown_text(event.get('event'))}: "
            f"{_markdown_text({key: value for key, value in event.items() if key not in {'timestamp', 'event'}})}"
            for event in timeline
            if isinstance(event, Mapping)
        )
    else:
        lines.append("- No timeline events recorded.")

    lines.extend(
        [
            "",
            "## Suspected Root Cause",
            "",
            json_block(diagnosis),
            "",
            "## Evidence",
            "",
            json_block(evidence),
            "",
            "## Confidence and Reviewer Verdict",
            "",
            f"- **Confidence:** {_markdown_text(report.get('confidence'))}",
            f"- **Reviewer:** {_markdown_text(reviewer)}",
            "",
            "## Proposed Action",
            "",
            json_block(proposed_action),
            "",
            "## Human Approval",
            "",
            json_block(approval),
            "",
            "## Action Result",
            "",
            json_block(action_result),
            "",
            "## Recovery Verification",
            "",
            json_block(verification),
            "",
            "## Prevention Suggestions",
            "",
        ]
    )
    if isinstance(suggestions, list) and suggestions:
        lines.extend(f"- {_markdown_text(suggestion)}" for suggestion in suggestions)
    else:
        lines.append("- No prevention suggestions recorded.")
    return "\n".join(lines) + "\n"


def write_report(
    incident_description: str,
    investigation: Mapping[str, Any],
    *,
    action_result: Mapping[str, Any] | None = None,
    recovery_verification: Mapping[str, Any] | None = None,
    prevention_suggestions: Sequence[str] = (),
    report_id: str | None = None,
    output_dir: Path | str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Path]:
    """Write paired JSON and Markdown reports under the incidents directory."""
    report = build_report(
        incident_description,
        investigation,
        action_result=action_result,
        recovery_verification=recovery_verification,
        prevention_suggestions=prevention_suggestions,
        report_id=report_id,
        created_at=created_at,
    )
    destination = Path(output_dir) if output_dir is not None else _DEFAULT_INCIDENTS_DIR
    destination.mkdir(parents=True, exist_ok=True)
    identifier = report["report_id"]
    json_path = destination / f"{identifier}.json"
    markdown_path = destination / f"{identifier}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"Incident report {identifier!r} already exists")

    json_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    try:
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
    except OSError:
        json_path.unlink(missing_ok=True)
        raise
    return {"json": json_path, "markdown": markdown_path}