from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from agent.act.runner import run_action
from agent.act.verify import verify_recovery
from agent.backends.offline import OfflineBackend
from agent.core.loop import investigate
from agent.core.validate import validate_diagnosis
from agent.tools._common import BackendError
from agent.tools.docker_tools import container_health, deploy_history
from agent.tools.jaeger import find_error_traces, list_services
from agent.tools.logs import get_logs
from agent.tools.prom import service_red
from agent.triage import triage


ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURES_DIR = ROOT / "fixtures"
DEFAULT_LABELS_PATH = ROOT / "labels.yaml"
DEFAULT_OUTPUT_PATH = ROOT / "results.json"
MAX_TRACE_SERVICES = 5
MAX_LOG_SERVICES = 3

ActionSelector = Callable[
    [Mapping[str, Any], Mapping[str, Any]],
    tuple[str, Mapping[str, Any]] | None,
]
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LABEL_FIELDS = {
    "incident_id",
    "expected_component",
    "expected_cause_class",
    "acceptable_actions",
    "expected_escalation",
    "expected_recovery",
}


def _case_id(bundle_path: Path, bundle: Mapping[str, Any]) -> str:
    root_id = bundle.get("incident_id")
    if root_id is not None:
        if not isinstance(root_id, str) or not root_id.strip():
            raise ValueError(f"Invalid incident_id in bundle {bundle_path}")
        case_id = root_id.strip()
        if not _ID_PATTERN.fullmatch(case_id):
            raise ValueError(f"Unsafe incident_id in bundle {bundle_path}: {case_id!r}")
        return case_id
    metadata = bundle.get("incident")
    if isinstance(metadata, Mapping):
        value = metadata.get("id")
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid incident.id in bundle {bundle_path}")
            case_id = value.strip()
            if not _ID_PATTERN.fullmatch(case_id):
                raise ValueError(f"Unsafe incident.id in bundle {bundle_path}: {case_id!r}")
            return case_id
    case_id = bundle_path.parent.name
    if not _ID_PATTERN.fullmatch(case_id):
        raise ValueError(f"Unsafe incident bundle directory name: {case_id!r}")
    return case_id


def _incident_description(bundle: Mapping[str, Any], case_id: str) -> str:
    metadata = bundle.get("incident")
    if isinstance(metadata, Mapping):
        description = metadata.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    description = bundle.get("incident_description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return f"Investigate recorded incident {case_id}"


def _successful_data(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if result.get("ok") is not True:
        return {}
    data = result.get("data")
    return data if isinstance(data, Mapping) else {}


def _services_for_followup(
    red_result: Mapping[str, Any],
    initial_triage: Mapping[str, Any],
    available_services: Sequence[str],
) -> list[str]:
    services: list[str] = []
    red_data = _successful_data(red_result)
    red_rows = red_data.get("services", [])
    if isinstance(red_rows, list):
        for row in red_rows:
            if isinstance(row, Mapping):
                name = row.get("service")
                if isinstance(name, str) and name and name not in services:
                    services.append(name)
    suspects = initial_triage.get("suspects", [])
    if isinstance(suspects, list):
        for suspect in suspects:
            if isinstance(suspect, Mapping):
                name = suspect.get("service")
                if isinstance(name, str) and name and name not in services:
                    services.insert(0, name)
    for name in available_services:
        if name not in services:
            services.append(name)
    return services[:MAX_TRACE_SERVICES]


def collect_telemetry(
    incident: str,
    backend: OfflineBackend,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect bounded read-only telemetry and perform deterministic triage."""
    telemetry: dict[str, Any] = {
        "service_red": service_red(backend=backend),
        "container_health": container_health(backend=backend),
        "deploy_history": deploy_history(minutes=10080, backend=backend),
    }
    telemetry["services"] = list_services(backend=backend)
    service_data = _successful_data(telemetry["services"])
    available_services = service_data.get("services", [])
    if not isinstance(available_services, list):
        available_services = []
    available_services = [
        service
        for service in available_services
        if isinstance(service, str) and "flagd" not in service.casefold()
    ]

    initial_triage = triage(incident, telemetry)
    followup_services = _services_for_followup(
        telemetry["service_red"],
        initial_triage,
        available_services,
    )
    traces: list[dict[str, Any]] = []
    logs: dict[str, dict[str, Any]] = {}
    followup_failures: list[str] = []
    for service in followup_services:
        result = find_error_traces(service, backend=backend)
        if result.get("ok") is True:
            data = _successful_data(result)
            found = data.get("traces", [])
            if isinstance(found, list):
                traces.extend(item for item in found if isinstance(item, dict))
        else:
            followup_failures.append(
                f"Jaeger traces for {service}: {result.get('summary', 'tool failed')}"
            )
    telemetry["traces"] = {
        "ok": True,
        "summary": f"Collected error traces for {len(followup_services)} services",
        "data": {"traces": traces},
        "source": "offline Jaeger",
    }

    for service in followup_services[:MAX_LOG_SERVICES]:
        result = get_logs(service, backend=backend)
        logs[service] = dict(result)
    telemetry["logs"] = logs
    triage_result = triage(incident, telemetry)
    if telemetry["services"].get("ok") is not True:
        followup_failures.append(
            f"Jaeger services: {telemetry['services'].get('summary', 'tool failed')}"
        )
    triage_result["unavailable_sources"] = sorted(
        set(triage_result["unavailable_sources"]) | set(followup_failures)
    )
    return telemetry, triage_result


def _load_bundles(fixtures_dir: Path) -> list[tuple[str, str, OfflineBackend, dict[str, Any]]]:
    if not fixtures_dir.exists():
        return []
    if not fixtures_dir.is_dir():
        raise ValueError(f"Fixtures path is not a directory: {fixtures_dir}")
    bundle_paths = sorted(fixtures_dir.glob("*/bundle.json"))
    cases = []
    seen_ids: set[str] = set()
    for bundle_path in bundle_paths:
        try:
            backend = OfflineBackend(bundle_path)
        except BackendError as exc:
            raise ValueError(f"Could not load bundle {bundle_path}: {exc}") from exc
        bundle = backend.bundle
        case_id = _case_id(bundle_path, bundle)
        if case_id in seen_ids:
            raise ValueError(f"Duplicate incident bundle ID: {case_id}")
        seen_ids.add(case_id)
        cases.append(
            (case_id, _incident_description(bundle, case_id), backend, bundle)
        )
    return cases


def _run_agents(
    cases: Sequence[tuple[str, str, OfflineBackend, dict[str, Any]]],
    *,
    completion_fn: Callable[..., Any] | None,
    model: str | None,
    action_selector: ActionSelector | None,
) -> list[dict[str, Any]]:
    """Run every agent before the answer-key file is opened."""
    outputs: list[dict[str, Any]] = []
    for case_id, incident, backend, bundle in cases:
        telemetry, triage_result = collect_telemetry(incident, backend)
        investigation = investigate(
            incident,
            triage_result,
            backend=backend,
            model=model,
            completion_fn=completion_fn,
        )
        action_result: dict[str, Any] | None = None
        if action_selector is not None and isinstance(investigation, Mapping):
            selected = action_selector(investigation, triage_result)
            if selected is not None:
                action_name, parameters = selected
                action_result = run_action(
                    investigation,
                    action_name,
                    parameters,
                    dry_run=True,
                )
        outputs.append(
            {
                "incident_id": case_id,
                "incident": incident,
                "telemetry": telemetry,
                "triage": triage_result,
                "investigation": investigation,
                "action_result": action_result,
                "bundle_recovery": bundle.get("recovery"),
            }
        )
    return outputs


def _load_labels(path: Path) -> dict[str, dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read answer key {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Answer key is invalid YAML: {exc}") from exc
    if not isinstance(document, Mapping) or document.get("version") != 1:
        raise ValueError("Answer key must be an object with version 1")
    incidents = document.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("Answer key incidents must be a list")
    labels: dict[str, dict[str, Any]] = {}
    for index, label in enumerate(incidents):
        if not isinstance(label, Mapping):
            raise ValueError(f"Answer key incident at index {index} must be an object")
        if any(not isinstance(key, str) for key in label):
            raise ValueError(f"Answer key incident at index {index} has a non-string field")
        case_id = label.get("incident_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Answer key incident at index {index} needs incident_id")
        case_id = case_id.strip()
        if not _ID_PATTERN.fullmatch(case_id):
            raise ValueError(f"Unsafe incident_id in answer key: {case_id!r}")
        if case_id in labels:
            raise ValueError(f"Duplicate answer key incident_id: {case_id}")
        unknown_fields = set(label) - _LABEL_FIELDS
        if unknown_fields:
            raise ValueError(
                f"{case_id} has unsupported answer-key field(s): "
                f"{', '.join(sorted(unknown_fields))}"
            )
        for key in ("expected_component", "expected_cause_class"):
            if key in label and not isinstance(label[key], str):
                raise ValueError(f"{case_id}.{key} must be a string")
            if key in label and not label[key].strip():
                raise ValueError(f"{case_id}.{key} must not be empty")
        for key in ("acceptable_actions",):
            value = label.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"{case_id}.{key} must be a list of strings")
            if any(not item.strip() for item in value):
                raise ValueError(f"{case_id}.{key} cannot contain empty strings")
        for key in ("expected_escalation", "expected_recovery"):
            if key in label and not isinstance(label[key], bool):
                raise ValueError(f"{case_id}.{key} must be a boolean")
        normalized_label = {**label, "incident_id": case_id}
        for key in ("expected_component", "expected_cause_class"):
            if key in normalized_label:
                normalized_label[key] = normalized_label[key].strip()
        labels[case_id] = normalized_label
    return labels


def _diagnosis_matches(
    investigation: Any,
    label: Mapping[str, Any],
) -> bool | None:
    expected_component = label.get("expected_component")
    expected_cause = label.get("expected_cause_class")
    if expected_component is None and expected_cause is None:
        return None
    if not isinstance(investigation, Mapping):
        return False
    diagnosis = investigation.get("diagnosis")
    if not isinstance(diagnosis, Mapping):
        return False
    if isinstance(expected_component, str):
        actual_component = diagnosis.get("component")
        if not isinstance(actual_component, str) or (
            actual_component.casefold() != expected_component.casefold()
        ):
            return False
    if isinstance(expected_cause, str):
        claims = [diagnosis.get("cause", "")]
        findings = diagnosis.get("findings", [])
        if isinstance(findings, list):
            claims.extend(
                finding.get("claim", "")
                for finding in findings
                if isinstance(finding, Mapping)
            )
        combined = " ".join(str(claim) for claim in claims).casefold()
        if expected_cause.casefold() not in combined:
            return False
    return True


def _evidence_is_valid(investigation: Any) -> bool:
    if not isinstance(investigation, Mapping):
        return False
    diagnosis = investigation.get("diagnosis")
    evidence = investigation.get("evidence")
    if not isinstance(diagnosis, Mapping) or not isinstance(evidence, list):
        return False
    validated, errors = validate_diagnosis(diagnosis, evidence)
    return validated is not None and not errors


def _action_prediction(output: Mapping[str, Any]) -> str | None:
    action_result = output.get("action_result")
    if not isinstance(action_result, Mapping):
        return None
    proposal = action_result.get("proposal")
    if not isinstance(proposal, Mapping):
        return None
    action = proposal.get("action")
    return action if isinstance(action, str) else None


def _recovery_prediction(output: Mapping[str, Any], label: Mapping[str, Any]) -> bool | None:
    if "expected_recovery" not in label:
        return None
    recovery = output.get("bundle_recovery")
    if not isinstance(recovery, Mapping):
        return None
    before = recovery.get("before_health")
    after = recovery.get("after_health")
    service = recovery.get("service")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    diagnosis = output.get("investigation", {}).get("diagnosis") if isinstance(
        output.get("investigation"), Mapping
    ) else None
    if not isinstance(service, str) and isinstance(diagnosis, Mapping):
        service = diagnosis.get("component")
    if not isinstance(service, str):
        return None
    result = verify_recovery(before, after, service)
    if result["status"] not in {"verified", "not_recovered"}:
        return None
    return result["recovered"]


def _score(
    outputs: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    diagnosis_scores: list[bool] = []
    action_scores: list[bool] = []
    evidence_scores: list[bool] = []
    escalation_scores: list[bool] = []
    recovery_scores: list[bool] = []
    for output in outputs:
        case_id = output["incident_id"]
        label = labels.get(case_id)
        if label is None:
            rows.append(
                {
                    "incident_id": case_id,
                    "error": output.get("error"),
                    "label_status": "missing",
                }
            )
            continue
        investigation = output.get("investigation")
        diagnosis_correct = _diagnosis_matches(investigation, label)
        if diagnosis_correct is not None:
            diagnosis_scores.append(diagnosis_correct)

        acceptable_actions = label.get("acceptable_actions", [])
        action_correct: bool | None = None
        action_prediction = _action_prediction(output)
        if acceptable_actions and action_prediction is not None:
            action_correct = action_prediction in acceptable_actions
            action_scores.append(action_correct)

        evidence_valid: bool | None = None
        if isinstance(investigation, Mapping) and investigation.get("diagnosis") is not None:
            evidence_valid = _evidence_is_valid(investigation)
            evidence_scores.append(evidence_valid)

        escalation_correct: bool | None = None
        if "expected_escalation" in label:
            escalated = (
                isinstance(investigation, Mapping)
                and investigation.get("status") == "escalated"
            )
            escalation_correct = escalated == label["expected_escalation"]
            escalation_scores.append(escalation_correct)

        recovery_correct: bool | None = None
        recovery_prediction = _recovery_prediction(output, label)
        if "expected_recovery" in label:
            if recovery_prediction is not None:
                recovery_correct = recovery_prediction == label["expected_recovery"]
                recovery_scores.append(recovery_correct)

        rows.append(
            {
                "incident_id": case_id,
                "label_status": "scored",
                "error": output.get("error"),
                "diagnosis_correct": diagnosis_correct,
                "action_prediction": action_prediction,
                "action_status": (
                    output["action_result"].get("status")
                    if isinstance(output.get("action_result"), Mapping)
                    else None
                ),
                "action_correct": action_correct,
                "evidence_valid": evidence_valid,
                "escalation_correct": escalation_correct,
                "recovery_prediction": recovery_prediction,
                "recovery_correct": recovery_correct,
                "investigation_status": (
                    investigation.get("status")
                    if isinstance(investigation, Mapping)
                    else None
                ),
            }
        )

    def rate(values: Sequence[bool]) -> dict[str, Any]:
        return {
            "correct": sum(values),
            "evaluated": len(values),
            "accuracy": sum(values) / len(values) if values else None,
        }

    return {
        "cases": rows,
        "metrics": {
            "diagnosis_accuracy": rate(diagnosis_scores),
            "action_accuracy": rate(action_scores),
            "evidence_validity": rate(evidence_scores),
            "escalation_accuracy": rate(escalation_scores),
            "recovery_accuracy": rate(recovery_scores),
        },
        "evaluated_incidents": sum(row["label_status"] == "scored" for row in rows),
        "unlabeled_incidents": sum(row["label_status"] == "missing" for row in rows),
    }


def evaluate(
    *,
    fixtures_dir: Path | str = DEFAULT_FIXTURES_DIR,
    labels_path: Path | str = DEFAULT_LABELS_PATH,
    completion_fn: Callable[..., Any] | None = None,
    model: str | None = None,
    action_selector: ActionSelector | None = None,
) -> dict[str, Any]:
    """Run all offline cases first; only afterward load human answer labels."""
    fixtures_path = Path(fixtures_dir)
    cases = _load_bundles(fixtures_path)
    outputs = _run_agents(
        cases,
        completion_fn=completion_fn,
        model=model,
        action_selector=action_selector,
    )
    labels = _load_labels(Path(labels_path))
    case_ids = {case_id for case_id, _, _, _ in cases}
    unexpected_labels = sorted(set(labels) - case_ids)
    result = _score(outputs, labels)
    result["dataset"] = {
        "fixtures_dir": str(fixtures_path),
        "answer_key_path": str(labels_path),
        "bundle_count": len(cases),
        "label_count": len(labels),
        "labels_without_bundle": unexpected_labels,
    }
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Incident Zero on offline incident bundles")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--model", default=None)
    arguments = parser.parse_args(argv)

    try:
        fixtures_path = arguments.fixtures.resolve()
        output_path = arguments.output.resolve()
        if output_path == arguments.labels.resolve():
            raise ValueError("Evaluation output must not overwrite the answer key")
        if output_path.is_relative_to(fixtures_path):
            raise ValueError("Evaluation output must not overwrite or enter the fixture dataset")
        result = evaluate(
            fixtures_dir=fixtures_path,
            labels_path=arguments.labels,
            model=arguments.model,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
    return 1 if result["dataset"]["labels_without_bundle"] else 0


if __name__ == "__main__":
    raise SystemExit(main())