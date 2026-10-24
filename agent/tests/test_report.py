from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.report import build_report, render_markdown, write_report


def sample_investigation() -> dict[str, Any]:
    return {
        "status": "diagnosis",
        "diagnosis": {
            "component": "checkout",
            "cause": "Checkout error rate increased",
            "confidence": 0.91,
            "evidence_ids": ["ev-001"],
            "escalated": False,
            "findings": [
                {
                    "claim": "Checkout error rate increased",
                    "evidence_ids": ["ev-001"],
                    "quotes": ['"error_rate":0.35'],
                }
            ],
        },
        "review": {
            "status": "reviewed",
            "verdict": "agree",
            "reason": "The telemetry supports the component and finding",
        },
        "evidence": [
            {
                "evidence_id": "ev-001",
                "tool": "service_red",
                "arguments": {"service": "checkout"},
                "raw_output": {
                    "ok": True,
                    "data": {"service": "checkout", "error_rate": 0.35},
                },
                "timestamp": "2026-10-24T04:00:00Z",
            }
        ],
    }


class IncidentReportTests(unittest.TestCase):
    def test_report_contains_all_required_sections_and_timeline(self) -> None:
        action = {
            "status": "submitted",
            "proposal": {
                "action": "restart_service",
                "parameters": {"service": "checkout"},
            },
            "approval": {"status": "approved", "approved": True},
            "api_response": {"accepted": True},
        }
        recovery = {
            "status": "recovery_verified",
            "recovered": True,
            "verification": {"status": "verified"},
        }
        report = build_report(
            "Checkout errors are elevated",
            sample_investigation(),
            action_result=action,
            recovery_verification=recovery,
            prevention_suggestions=["Add a checkout error-rate alert"],
            report_id="incident-test-001",
            created_at=datetime(2026, 10, 24, 4, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(report["report_id"], "incident-test-001")
        self.assertEqual(report["incident"]["description"], "Checkout errors are elevated")
        self.assertEqual(report["diagnosis"]["component"], "checkout")
        self.assertEqual(report["evidence"][0]["evidence_id"], "ev-001")
        self.assertEqual(report["confidence"], 0.91)
        self.assertEqual(report["reviewer"]["verdict"], "agree")
        self.assertEqual(report["proposed_action"], action["proposal"])
        self.assertEqual(report["human_approval"], action["approval"])
        self.assertEqual(report["action_result"], action)
        self.assertEqual(report["recovery_verification"], recovery)
        self.assertEqual(report["prevention_suggestions"], ["Add a checkout error-rate alert"])
        self.assertTrue(any(event["event"] == "Evidence collected" for event in report["timeline"]))
        self.assertTrue(any(event["event"] == "Action processed" for event in report["timeline"]))
        self.assertTrue(
            any(event["event"] == "Recovery verification completed" for event in report["timeline"])
        )

    def test_markdown_includes_all_required_headings_and_escapes_report_text(self) -> None:
        report = build_report(
            "checkout | errors\n``` injected",
            sample_investigation(),
            prevention_suggestions=["Alert on checkout latency"],
            report_id="incident-markdown",
            created_at=datetime(2026, 10, 24, 4, 1, tzinfo=timezone.utc),
        )

        markdown = render_markdown(report)
        for heading in (
            "## Timeline",
            "## Suspected Root Cause",
            "## Evidence",
            "## Confidence and Reviewer Verdict",
            "## Proposed Action",
            "## Human Approval",
            "## Action Result",
            "## Recovery Verification",
            "## Prevention Suggestions",
        ):
            self.assertIn(heading, markdown)
        self.assertIn("checkout \\| errors", markdown)
        self.assertNotIn("``` injected", markdown)
        self.assertIn("Alert on checkout latency", markdown)

    def test_write_report_creates_json_and_markdown_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            paths = write_report(
                "Checkout is failing",
                sample_investigation(),
                report_id="incident-write-test",
                output_dir=output_dir,
                created_at=datetime(2026, 10, 24, 4, 1, tzinfo=timezone.utc),
            )

            self.assertTrue(paths["json"].is_file())
            self.assertTrue(paths["markdown"].is_file())
            saved = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(saved["incident"]["description"], "Checkout is failing")
            self.assertIn("## Evidence", paths["markdown"].read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                write_report(
                    "Different incident",
                    sample_investigation(),
                    report_id="incident-write-test",
                    output_dir=output_dir,
                )

    def test_rejects_unsafe_report_ids_and_non_json_values(self) -> None:
        with self.assertRaises(ValueError):
            build_report(
                "Incident",
                sample_investigation(),
                report_id="..\\outside",
            )
        invalid_investigation = sample_investigation()
        invalid_investigation["evidence"][0]["raw_output"]["number"] = float("nan")
        with self.assertRaises(ValueError):
            build_report("Incident", invalid_investigation)


if __name__ == "__main__":
    unittest.main()
