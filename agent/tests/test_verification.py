from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest.mock import patch
from typing import Any

from agent.act.verify import verify_action_recovery, verify_recovery


def health(
    *,
    error_baseline: float = 0.01,
    error_current: float = 0.01,
    latency_baseline: float = 100.0,
    latency_current: float = 100.0,
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "data": {
            "services": [
                {
                    "service": "checkout",
                    "error_rate": {
                        "baseline": error_baseline,
                        "current": error_current,
                    },
                    "p95_latency_ms": {
                        "baseline": latency_baseline,
                        "current": latency_current,
                    },
                }
            ]
        },
    }


def submitted_action() -> dict[str, Any]:
    return {
        "status": "submitted",
        "proposal": {
            "action": "restart_service",
            "parameters": {"service": "checkout"},
        },
        "api_response": {"accepted": True},
    }


class RecoveryVerificationTests(unittest.TestCase):
    def test_verifies_recovery_when_metrics_return_to_baseline_tolerance(self) -> None:
        result = verify_recovery(
            health(error_current=0.35, latency_current=400),
            health(error_current=0.015, latency_current=120),
            "checkout",
        )

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["recovered"])
        self.assertTrue(result["checks"]["error_rate"]["passed"])
        self.assertTrue(result["checks"]["p95_latency_ms"]["passed"])

    def test_reports_not_recovered_when_a_metric_remains_elevated(self) -> None:
        result = verify_recovery(
            health(error_current=0.35, latency_current=400),
            health(error_current=0.2, latency_current=110),
            "checkout",
        )

        self.assertEqual(result["status"], "not_recovered")
        self.assertFalse(result["recovered"])
        self.assertTrue(result["checks"]["p95_latency_ms"]["passed"])
        self.assertFalse(result["checks"]["error_rate"]["passed"])

    def test_missing_service_or_failed_telemetry_is_unverified(self) -> None:
        missing = verify_recovery(
            health(),
            {"ok": True, "data": {"services": []}},
            "checkout",
        )
        failed = verify_recovery(health(), health(ok=False), "checkout")

        self.assertEqual(missing["status"], "unverified")
        self.assertFalse(missing["recovered"])
        self.assertEqual(failed["status"], "unverified")
        self.assertFalse(failed["recovered"])

    def test_invalid_values_do_not_claim_recovery(self) -> None:
        before = health()
        after = health()
        after["data"]["services"][0]["error_rate"]["current"] = float("nan")
        result = verify_recovery(before, after, "checkout")

        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["recovered"])

    def test_missing_metric_is_unverified(self) -> None:
        before = health()
        del before["data"]["services"][0]["p95_latency_ms"]["baseline"]
        result = verify_recovery(before, health(), "checkout")

        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["recovered"])

    def test_action_success_is_not_recovery_when_metrics_remain_elevated(self) -> None:
        result = verify_action_recovery(
            submitted_action(),
            health(error_current=0.35, latency_current=400),
            health(error_current=0.2, latency_current=300),
        )

        self.assertEqual(result["status"], "recovery_failed")
        self.assertFalse(result["recovered"])
        self.assertTrue(result["escalated"])
        self.assertTrue(result["stop_further_actions"])
        self.assertEqual(result["rollback"]["status"], "unavailable")

    def test_recovery_verification_requires_submitted_action(self) -> None:
        result = verify_action_recovery(
            {"status": "dry_run"},
            health(),
            health(),
        )

        self.assertEqual(result["status"], "not_applicable")
        self.assertFalse(result["recovered"])

    def test_does_not_verify_when_api_response_succeeded_without_health_data(self) -> None:
        result = verify_action_recovery(
            submitted_action(),
            health(error_current=0.35, latency_current=400),
            {"ok": False, "summary": "Prometheus unavailable"},
        )

        self.assertEqual(result["status"], "recovery_failed")
        self.assertEqual(result["verification"]["status"], "unverified")
        self.assertFalse(result["recovered"])

    def test_approved_configured_rollback_is_submitted_and_rechecked(self) -> None:
        runbook = {
            "version": 1,
            "actions": {
                "restart_service": {
                    "description": "Restart",
                    "rollback": {
                        "action": "restore_service",
                        "parameters": {"service": "checkout"},
                    },
                    "parameters": {
                        "service": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$",
                        }
                    },
                    "preconditions": [],
                },
                "restore_service": {
                    "description": "Restore",
                    "rollback": None,
                    "parameters": {
                        "service": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$",
                        }
                    },
                    "preconditions": [],
                },
            },
        }

        class FakeAPI:
            def __init__(self) -> None:
                self.actions: list[tuple[str, Mapping[str, Any]]] = []

            def execute_action(self, *, action: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
                self.actions.append((action, parameters))
                return {"accepted": True}

        api = FakeAPI()
        with patch("agent.act.runbook.load_runbook", return_value=runbook):
            result = verify_action_recovery(
                submitted_action(),
                health(error_current=0.35, latency_current=400),
                health(error_current=0.2, latency_current=300),
                rollback_approval_response="yes",
                health_after_rollback=health(error_current=0.02, latency_current=110),
                api_client=api,
                kill_switch_value="off",
            )

        self.assertEqual(result["status"], "recovery_verified_after_rollback")
        self.assertTrue(result["recovered"])
        self.assertEqual(api.actions, [("restore_service", {"service": "checkout"})])
        self.assertEqual(result["rollback"]["verification"]["status"], "verified")

    def test_configured_rollback_requires_explicit_approval(self) -> None:
        runbook = {
            "version": 1,
            "actions": {
                "restart_service": {
                    "description": "Restart",
                    "rollback": {
                        "action": "restore_service",
                        "parameters": {"service": "checkout"},
                    },
                    "parameters": {},
                    "preconditions": [],
                },
                "restore_service": {
                    "description": "Restore",
                    "rollback": None,
                    "parameters": {"service": {"type": "string", "pattern": ".+"}},
                    "preconditions": [],
                },
            },
        }

        with patch("agent.act.runbook.load_runbook", return_value=runbook):
            result = verify_action_recovery(
                submitted_action(),
                health(error_current=0.35, latency_current=400),
                health(error_current=0.2, latency_current=300),
                rollback_approval_response=None,
                api_client=object(),
                kill_switch_value="off",
            )

        self.assertEqual(result["status"], "recovery_failed")
        self.assertEqual(result["rollback"]["status"], "approval_required")
        self.assertTrue(result["stop_further_actions"])


if __name__ == "__main__":
    unittest.main()
