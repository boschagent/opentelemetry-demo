from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from agent.core.ledger import EvidenceLedger
from agent.core.validate import validate_diagnosis


def evidence_fixture() -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": "ev-001",
            "tool": "service_red",
            "arguments": {"service": "payment"},
            "raw_output": {
                "ok": True,
                "summary": "Payment has elevated errors",
                "data": {"service": "payment", "error_rate": 0.25},
                "source": "service_red",
            },
            "timestamp": "2026-01-01T00:00:00Z",
        }
    ]


def diagnosis_fixture() -> dict[str, Any]:
    return {
        "component": "payment",
        "cause": "Payment has an elevated error rate",
        "confidence": 0.85,
        "evidence_ids": ["ev-001"],
        "escalated": False,
        "findings": [
            {
                "claim": "Payment has an elevated error rate",
                "evidence_ids": ["ev-001"],
                "quotes": ['"error_rate":0.25'],
            }
        ],
    }


class EvidenceLedgerTests(unittest.TestCase):
    def test_records_only_successful_results_with_unique_ids_and_timestamp(self) -> None:
        ledger = EvidenceLedger()
        self.assertIsNone(
            ledger.record(
                "service_red",
                {"service": "payment"},
                {"ok": False, "summary": "unavailable"},
            )
        )
        first = ledger.record(
            "service_red",
            {"service": "payment"},
            {"ok": True, "data": {"error_rate": 0.25}},
        )
        second = ledger.record(
            "container_health",
            {},
            {"ok": True, "data": {"healthy": True}},
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first["evidence_id"], "ev-001")
        self.assertEqual(second["evidence_id"], "ev-002")
        self.assertEqual(first["tool"], "service_red")
        self.assertEqual(first["arguments"], {"service": "payment"})
        self.assertEqual(first["raw_output"]["data"]["error_rate"], 0.25)
        self.assertTrue(first["timestamp"].endswith("Z"))
        datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))

    def test_ledger_returns_snapshots_instead_of_mutable_internal_entries(self) -> None:
        ledger = EvidenceLedger()
        entry = ledger.record("list_services", {}, {"ok": True, "data": []})
        assert entry is not None
        entry["raw_output"]["ok"] = False
        self.assertTrue(ledger.entries[0]["raw_output"]["ok"])


class DiagnosisValidationTests(unittest.TestCase):
    def test_accepts_a_supported_diagnosis(self) -> None:
        diagnosis, errors = validate_diagnosis(diagnosis_fixture(), evidence_fixture())

        self.assertEqual(errors, [])
        self.assertIsNotNone(diagnosis)
        assert diagnosis is not None
        self.assertEqual(diagnosis["component"], "payment")
        self.assertEqual(diagnosis["evidence_ids"], ["ev-001"])

    def test_accepts_escalation_without_unsupported_findings(self) -> None:
        diagnosis = {
            "component": "unknown",
            "cause": "Insufficient evidence to identify a cause",
            "confidence": 0.1,
            "evidence_ids": [],
            "escalated": True,
            "findings": [],
        }
        validated, errors = validate_diagnosis(diagnosis, [])

        self.assertEqual(errors, [])
        self.assertIsNotNone(validated)
        assert validated is not None
        self.assertTrue(validated["escalated"])

    def test_rejects_unknown_evidence_ids(self) -> None:
        diagnosis = diagnosis_fixture()
        diagnosis["evidence_ids"] = ["ev-999"]
        diagnosis["findings"][0]["evidence_ids"] = ["ev-999"]
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("unknown evidence ID" in error for error in errors))

    def test_rejects_quotes_not_found_in_cited_raw_output(self) -> None:
        diagnosis = diagnosis_fixture()
        diagnosis["findings"][0]["quotes"] = ['"error_rate":0.95']
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("quote does not match" in error for error in errors))

    def test_rejects_numeric_claims_that_disagree_with_raw_output(self) -> None:
        diagnosis = diagnosis_fixture()
        diagnosis["cause"] = "Payment has an error rate of 0.95"
        diagnosis["findings"][0]["claim"] = "Payment has an error rate of 0.95"
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("number absent" in error for error in errors))

    def test_rejects_component_not_present_in_cited_raw_output(self) -> None:
        diagnosis = diagnosis_fixture()
        diagnosis["component"] = "checkout"
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("component does not appear" in error for error in errors))

    def test_rejects_missing_or_additional_schema_fields(self) -> None:
        diagnosis = diagnosis_fixture()
        del diagnosis["confidence"]
        diagnosis["arbitrary"] = "unsupported"
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("Missing required" in error for error in errors))
        self.assertTrue(any("Unsupported diagnosis field" in error for error in errors))

    def test_rejects_boolean_confidence_and_unhashable_evidence_ids(self) -> None:
        diagnosis = diagnosis_fixture()
        diagnosis["confidence"] = True
        diagnosis["evidence_ids"] = [{}]
        validated, errors = validate_diagnosis(diagnosis, evidence_fixture())

        self.assertIsNone(validated)
        self.assertTrue(any("confidence" in error for error in errors))
        self.assertTrue(any("evidence_ids" in error for error in errors))

    def test_rejects_non_json_diagnosis_text(self) -> None:
        diagnosis, errors = validate_diagnosis("not JSON", evidence_fixture())

        self.assertIsNone(diagnosis)
        self.assertTrue(any("valid JSON" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
