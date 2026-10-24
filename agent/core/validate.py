from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


_DIAGNOSIS_FIELDS = {
    "component",
    "cause",
    "confidence",
    "evidence_ids",
    "escalated",
    "findings",
}
_FINDING_FIELDS = {"claim", "evidence_ids", "quotes"}
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?%?"
)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_map(evidence: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        entry["evidence_id"]: entry
        for entry in evidence
        if isinstance(entry, Mapping)
        and isinstance(entry.get("evidence_id"), str)
        and isinstance(entry.get("raw_output"), Mapping)
    }


def _quoted_value_exists(quote: str, entries: Sequence[Mapping[str, Any]]) -> bool:
    for entry in entries:
        raw_output = entry.get("raw_output")
        if not isinstance(raw_output, Mapping):
            continue
        serialized = json.dumps(
            raw_output,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if quote in serialized:
            return True
    return False


def _claim_numbers_match(claim: str, entries: Sequence[Mapping[str, Any]]) -> bool:
    claimed_numbers = _NUMBER_PATTERN.findall(claim)
    if not claimed_numbers:
        return True
    evidence_numbers: set[str] = set()
    for entry in entries:
        raw_output = entry.get("raw_output")
        if isinstance(raw_output, Mapping):
            serialized = json.dumps(
                raw_output,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            evidence_numbers.update(_NUMBER_PATTERN.findall(serialized))
    return all(number in evidence_numbers for number in claimed_numbers)


def validate_diagnosis(
    diagnosis: str | Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and validate the provisional JSON diagnosis schema against evidence."""
    errors: list[str] = []
    if isinstance(diagnosis, str):
        try:
            parsed = json.loads(diagnosis)
        except json.JSONDecodeError as exc:
            return None, [f"Diagnosis is not valid JSON: {exc.msg}"]
    elif isinstance(diagnosis, Mapping):
        parsed = dict(diagnosis)
    else:
        return None, ["Diagnosis must be a JSON object"]

    if not isinstance(parsed, dict):
        return None, ["Diagnosis must be a JSON object"]

    missing = _DIAGNOSIS_FIELDS - parsed.keys()
    unknown = parsed.keys() - _DIAGNOSIS_FIELDS
    if missing:
        errors.append(f"Missing required diagnosis field(s): {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"Unsupported diagnosis field(s): {', '.join(sorted(unknown))}")

    component = parsed.get("component")
    cause = parsed.get("cause")
    confidence = parsed.get("confidence")
    evidence_ids = parsed.get("evidence_ids")
    escalated = parsed.get("escalated")
    findings = parsed.get("findings")

    if not _non_empty_string(component):
        errors.append("component must be a non-empty string")
    if not _non_empty_string(cause):
        errors.append("cause must be a non-empty string")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        errors.append("confidence must be a finite number between 0 and 1")
    if not isinstance(escalated, bool):
        errors.append("escalated must be a boolean")

    evidence_by_id = _evidence_map(evidence)
    root_ids_valid = isinstance(evidence_ids, list) and all(
        isinstance(item, str) and item in evidence_by_id for item in evidence_ids
    )
    if not isinstance(evidence_ids, list) or any(
        not isinstance(item, str) or not item for item in evidence_ids
    ):
        errors.append("evidence_ids must be a list of non-empty strings")
        root_ids_valid = False
    elif not root_ids_valid:
        missing_ids = sorted(
            {item for item in evidence_ids if isinstance(item, str) and item not in evidence_by_id}
        )
        errors.append(f"Diagnosis references unknown evidence ID(s): {', '.join(missing_ids)}")
    if (
        isinstance(evidence_ids, list)
        and all(isinstance(item, str) for item in evidence_ids)
        and len(set(evidence_ids)) != len(evidence_ids)
    ):
        errors.append("evidence_ids must not contain duplicates")
    if root_ids_valid and not evidence_ids and escalated is not True:
        errors.append("A non-escalated diagnosis must cite at least one evidence ID")

    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    if not findings and escalated is not True:
        errors.append("A non-escalated diagnosis must include at least one supported finding")

    finding_evidence_ids: set[str] = set()
    valid_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        if not isinstance(finding, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        missing_finding = _FINDING_FIELDS - finding.keys()
        unknown_finding = finding.keys() - _FINDING_FIELDS
        if missing_finding:
            errors.append(
                f"{prefix} is missing field(s): {', '.join(sorted(missing_finding))}"
            )
        if unknown_finding:
            errors.append(
                f"{prefix} has unsupported field(s): {', '.join(sorted(unknown_finding))}"
            )

        claim = finding.get("claim")
        finding_ids = finding.get("evidence_ids")
        quotes = finding.get("quotes")
        if not _non_empty_string(claim):
            errors.append(f"{prefix}.claim must be a non-empty string")
        if not isinstance(finding_ids, list) or not finding_ids or any(
            not isinstance(item, str) or item not in evidence_by_id for item in finding_ids
        ):
            errors.append(f"{prefix}.evidence_ids must cite existing evidence")
            finding_ids = []
        if (
            isinstance(finding_ids, list)
            and all(isinstance(item, str) for item in finding_ids)
            and len(set(finding_ids)) != len(finding_ids)
        ):
            errors.append(f"{prefix}.evidence_ids must not contain duplicates")
        if not isinstance(quotes, list) or not quotes or any(
            not _non_empty_string(quote) for quote in quotes
        ):
            errors.append(f"{prefix}.quotes must contain at least one non-empty quote")
            quotes = []

        cited_entries = [
            evidence_by_id[item] for item in finding_ids if item in evidence_by_id
        ]
        for quote in quotes:
            if isinstance(quote, str) and not _quoted_value_exists(quote, cited_entries):
                errors.append(f"{prefix} quote does not match its cited raw evidence: {quote!r}")
        if isinstance(claim, str) and not _claim_numbers_match(claim, cited_entries):
            errors.append(f"{prefix}.claim contains a number absent from its cited evidence")
        finding_evidence_ids.update(
            item for item in finding_ids if isinstance(item, str)
        )
        valid_findings.append(
            {
                "claim": claim,
                "evidence_ids": finding_ids,
                "quotes": quotes,
            }
        )

    if root_ids_valid and finding_evidence_ids != set(evidence_ids):
        errors.append("evidence_ids must match the evidence cited by the findings")

    if (
        _non_empty_string(component)
        and component.casefold() != "unknown"
        and root_ids_valid
        and evidence_ids
    ):
        component_is_grounded = any(
            component.casefold()
            in json.dumps(
                evidence_by_id[evidence_id]["raw_output"],
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).casefold()
            for evidence_id in evidence_ids
        )
        if not component_is_grounded:
            errors.append("component does not appear in the cited raw evidence")

    if (
        _non_empty_string(cause)
        and not (escalated is True and not findings)
        and not any(
            isinstance(finding, Mapping)
            and isinstance(finding.get("claim"), str)
            and finding["claim"].casefold() == cause.casefold()
            for finding in findings
        )
    ):
        errors.append("cause must be stated as a supported finding")

    if errors:
        return None, errors
    return {
        "component": component,
        "cause": cause,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "escalated": escalated,
        "findings": valid_findings,
    }, []