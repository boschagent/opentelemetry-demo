from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import patch

from eval import run_all


def diagnosis_result() -> dict[str, Any]:
    diagnosis = {
        "component": "checkout",
        "cause": "Checkout connection refused",
        "confidence": 0.9,
        "evidence_ids": ["ev-001"],
        "escalated": False,
        "findings": [
            {
                "claim": "Checkout connection refused",
                "evidence_ids": ["ev-001"],
                "quotes": ["checkout connection refused"],
            }
        ],
    }
    return {
        "status": "diagnosis",
        "diagnosis": diagnosis,
        "evidence": [
            {
                "evidence_id": "ev-001",
                "raw_output": {"message": "checkout connection refused"},
            }
        ],
    }


class EvaluationTests(unittest.TestCase):
    def _dataset(self, root: Path) -> tuple[Path, Path]:
        fixtures = root / "fixtures"
        case_dir = fixtures / "practice-01"
        case_dir.mkdir(parents=True)
        (case_dir / "bundle.json").write_text(
            json.dumps(
                {
                    "incident": {
                        "id": "practice-01",
                        "description": "Checkout requests are failing",
                    }
                }
            ),
            encoding="utf-8",
        )
        labels = root / "labels.yaml"
        labels.write_text(
            "version: 1\n"
            "incidents:\n"
            "  - incident_id: practice-01\n"
            "    expected_component: checkout\n"
            "    expected_cause_class: connection refused\n"
            "    acceptable_actions: [restart_service]\n"
            "    expected_escalation: false\n",
            encoding="utf-8",
        )
        return fixtures, labels

    def test_agent_runs_before_labels_are_loaded_and_metrics_are_scored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixtures, labels = self._dataset(Path(directory))
            order: list[str] = []

            def fake_investigate(
                incident: str,
                triage_result: dict[str, Any],
                **kwargs: Any,
            ) -> dict[str, Any]:
                self.assertEqual(incident, "Checkout requests are failing")
                self.assertEqual(triage_result, {})
                order.append("investigate")
                self.assertNotIn("labels", kwargs)
                return diagnosis_result()

            real_load_labels = run_all._load_labels

            def track_load_labels(path: Path) -> dict[str, dict[str, Any]]:
                order.append("load_labels")
                return real_load_labels(path)

            with (
                patch.object(run_all, "collect_telemetry", return_value=({}, {})),
                patch.object(run_all, "investigate", side_effect=fake_investigate),
                patch.object(run_all, "_load_labels", side_effect=track_load_labels),
            ):
                result = run_all.evaluate(fixtures_dir=fixtures, labels_path=labels)

        self.assertEqual(order, ["investigate", "load_labels"])
        self.assertEqual(result["metrics"]["diagnosis_accuracy"]["accuracy"], 1.0)
        self.assertEqual(result["metrics"]["evidence_validity"]["accuracy"], 1.0)
        self.assertEqual(result["metrics"]["escalation_accuracy"]["accuracy"], 1.0)
        self.assertEqual(result["metrics"]["action_accuracy"]["evaluated"], 0)
        self.assertIsNone(result["metrics"]["action_accuracy"]["accuracy"])
        self.assertIsNone(result["cases"][0]["action_correct"])

    def test_action_scoring_uses_only_a_dry_run_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixtures, labels = self._dataset(Path(directory))
            action_call: dict[str, Any] = {}

            def fake_run_action(
                investigation: dict[str, Any],
                action_name: str,
                parameters: dict[str, Any],
                *,
                dry_run: bool,
            ) -> dict[str, Any]:
                self.assertEqual(investigation["status"], "diagnosis")
                action_call.update(
                    {"action_name": action_name, "parameters": parameters, "dry_run": dry_run}
                )
                return {
                    "status": "dry_run",
                    "proposal": {"action": action_name, "parameters": parameters},
                }

            with (
                patch.object(run_all, "collect_telemetry", return_value=({}, {})),
                patch.object(run_all, "investigate", return_value=diagnosis_result()),
                patch.object(run_all, "run_action", side_effect=fake_run_action),
            ):
                result = run_all.evaluate(
                    fixtures_dir=fixtures,
                    labels_path=labels,
                    action_selector=lambda *_: (
                        "restart_service",
                        {"service": "checkout"},
                    ),
                )

        self.assertTrue(action_call["dry_run"])
        self.assertEqual(result["metrics"]["action_accuracy"]["accuracy"], 1.0)

    def test_missing_optional_predictions_are_not_evaluated(self) -> None:
        result = run_all._score(
            [
                {
                    "incident_id": "practice-01",
                    "investigation": {"status": "escalated", "diagnosis": None},
                    "action_result": None,
                    "bundle_recovery": None,
                }
            ],
            {
                "practice-01": {
                    "expected_component": "checkout",
                    "acceptable_actions": ["restart_service"],
                    "expected_escalation": True,
                    "expected_recovery": True,
                }
            },
        )
        row = result["cases"][0]
        self.assertIsNone(row["action_correct"])
        self.assertIsNone(row["evidence_valid"])
        self.assertIsNone(row["recovery_correct"])
        self.assertEqual(result["metrics"]["action_accuracy"]["evaluated"], 0)
        self.assertEqual(result["metrics"]["recovery_accuracy"]["evaluated"], 0)

    def test_invalid_evidence_quotes_fail_evidence_validity(self) -> None:
        investigation = diagnosis_result()
        investigation["diagnosis"]["findings"][0]["quotes"] = ["not in the raw output"]
        result = run_all._score(
            [
                {
                    "incident_id": "practice-01",
                    "investigation": investigation,
                    "action_result": None,
                }
            ],
            {"practice-01": {}},
        )
        self.assertFalse(result["cases"][0]["evidence_valid"])
        self.assertEqual(result["metrics"]["evidence_validity"]["accuracy"], 0.0)

    def test_recovery_scoring_uses_recorded_health_observations(self) -> None:
        health_before = {
            "ok": True,
            "data": {
                "services": [
                    {
                        "service": "checkout",
                        "error_rate": {"baseline": 0.01, "current": 0.2},
                        "p95_latency_ms": {"baseline": 100, "current": 500},
                    }
                ]
            },
        }
        health_after = {
            "ok": True,
            "data": {
                "services": [
                    {
                        "service": "checkout",
                        "error_rate": {"baseline": 0.01, "current": 0.01},
                        "p95_latency_ms": {"baseline": 100, "current": 110},
                    }
                ]
            },
        }
        result = run_all._score(
            [
                {
                    "incident_id": "practice-01",
                    "investigation": diagnosis_result(),
                    "action_result": None,
                    "bundle_recovery": {
                        "service": "checkout",
                        "before_health": health_before,
                        "after_health": health_after,
                    },
                }
            ],
            {"practice-01": {"expected_recovery": True}},
        )
        self.assertTrue(result["cases"][0]["recovery_prediction"])
        self.assertTrue(result["cases"][0]["recovery_correct"])
        self.assertEqual(result["metrics"]["recovery_accuracy"]["accuracy"], 1.0)

    def test_answer_key_rejects_duplicate_ids_and_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / "labels.yaml"
            labels.write_text(
                "version: 1\nincidents:\n"
                "  - incident_id: same\n"
                "  - incident_id: same\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate answer key"):
                run_all._load_labels(labels)
            labels.write_text(
                "version: 1\nincidents:\n"
                "  - incident_id: case-1\n"
                "    expected_cause: guessed\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported answer-key field"):
                run_all._load_labels(labels)

    def test_cli_refuses_to_overwrite_the_answer_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / "labels.yaml"
            original = "version: 1\nincidents: []\n"
            labels.write_text(original, encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                exit_code = run_all.main(
                    ["--labels", str(labels), "--output", str(labels)]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(labels.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
