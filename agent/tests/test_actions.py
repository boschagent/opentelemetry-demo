from __future__ import annotations

import unittest
from typing import Any

from agent.act.action_api import ActionAPIConfigurationError
from agent.act.approval import request_approval
from agent.act.killswitch import KillSwitchActive, check_kill_switch
from agent.act.runbook import validate_action
from agent.act.runner import run_action


def approved_investigation(
    *,
    status: str = "diagnosis",
    escalated: bool = False,
    confidence: float = 0.9,
    verdict: str = "agree",
) -> dict[str, Any]:
    return {
        "status": status,
        "diagnosis": {
            "component": "checkout",
            "cause": "Elevated checkout errors",
            "confidence": confidence,
            "escalated": escalated,
        },
        "review": {
            "status": "reviewed",
            "verdict": verdict,
            "reason": "Evidence supports the diagnosis",
        },
    }


class FakeActionAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_action(
        self,
        *,
        action: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"action": action, "parameters": parameters})
        return {"accepted": True, "request_id": "test-request"}


class RunbookTests(unittest.TestCase):
    def test_rejects_unknown_action_and_invalid_parameters(self) -> None:
        parameters, error = validate_action("delete_database", {})
        self.assertIsNone(parameters)
        self.assertIn("not in the approved runbook", error or "")

        parameters, error = validate_action("restart_service", {"service": "../checkout"})
        self.assertIsNone(parameters)
        self.assertIn("invalid value", error or "")

    def test_rejects_extra_parameters(self) -> None:
        parameters, error = validate_action(
            "restart_service",
            {"service": "checkout", "command": "unsafe"},
        )
        self.assertIsNone(parameters)
        self.assertIn("Unsupported", error or "")


class ApprovalTests(unittest.TestCase):
    def test_requires_exact_yes(self) -> None:
        proposal = {"action": "restart_service"}
        self.assertFalse(request_approval(proposal, "Yes")["approved"])
        self.assertFalse(request_approval(proposal, "yes ")["approved"])
        self.assertTrue(request_approval(proposal, "yes")["approved"])


class KillSwitchTests(unittest.TestCase):
    def test_known_values_are_parsed_and_malformed_value_fails_closed(self) -> None:
        check_kill_switch("false")
        with self.assertRaises(KillSwitchActive):
            check_kill_switch("ON")
        with self.assertRaises(ValueError):
            check_kill_switch("sometimes")


class ActionRunnerTests(unittest.TestCase):
    def test_dry_run_returns_proposal_without_approval_or_api_call(self) -> None:
        api = FakeActionAPI()
        result = run_action(
            approved_investigation(),
            "restart_service",
            {"service": "checkout"},
            api_client=api,
        )

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["proposal"]["parameters"], {"service": "checkout"})
        self.assertEqual(api.calls, [])

    def test_requires_non_escalated_diagnosis_and_agreeing_review(self) -> None:
        for investigation in (
            approved_investigation(status="escalated"),
            approved_investigation(escalated=True),
            approved_investigation(verdict="disagree"),
            approved_investigation(confidence=0.69),
        ):
            result = run_action(
                investigation,
                "restart_service",
                {"service": "checkout"},
                dry_run=False,
                approval_response="yes",
                kill_switch_value="off",
                api_client=FakeActionAPI(),
            )
            self.assertEqual(result["status"], "blocked")

    def test_restart_requires_target_to_match_diagnosed_component(self) -> None:
        result = run_action(
            approved_investigation(),
            "restart_service",
            {"service": "payment"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("must match", result["error"])

    def test_live_action_requires_explicit_approval(self) -> None:
        api = FakeActionAPI()
        result = run_action(
            approved_investigation(),
            "restart_service",
            {"service": "checkout"},
            dry_run=False,
            approval_response="no",
            kill_switch_value="off",
            api_client=api,
        )

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(api.calls, [])

    def test_kill_switch_blocks_call_even_after_approval(self) -> None:
        api = FakeActionAPI()
        result = run_action(
            approved_investigation(),
            "restart_service",
            {"service": "checkout"},
            dry_run=False,
            approval_response="yes",
            kill_switch_value="true",
            api_client=api,
        )

        self.assertEqual(result["status"], "kill_switch_blocked")
        self.assertEqual(api.calls, [])

    def test_live_execution_uses_client_and_does_not_claim_recovery(self) -> None:
        api = FakeActionAPI()
        result = run_action(
            approved_investigation(),
            "restart_service",
            {"service": "checkout"},
            dry_run=False,
            approval_response="yes",
            kill_switch_value="off",
            api_client=api,
        )

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(api.calls, [{"action": "restart_service", "parameters": {"service": "checkout"}}])
        self.assertIn("recovery has not been verified", result["message"])
        self.assertNotIn("recovery_verified", result)

    def test_live_execution_fails_when_api_client_is_not_configured(self) -> None:
        with self.assertRaises(ActionAPIConfigurationError):
            run_action(
                approved_investigation(),
                "restart_service",
                {"service": "checkout"},
                dry_run=False,
                approval_response="yes",
                kill_switch_value="off",
            )


if __name__ == "__main__":
    unittest.main()
