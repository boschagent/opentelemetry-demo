from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from typing import Any

from agent.backends.offline import OfflineBackend
from agent.core.llm import LLMRequestError
from agent.core.loop import MAX_TOOL_RESULT_CHARS, _serialize_result, _tool_definitions, investigate


def response(content: str | None = None, calls: list[dict[str, Any]] | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ]
    }


def tool_call(call_id: str, name: str, arguments: Any) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def diagnosis_content(
    *,
    component: str = "unknown",
    cause: str = "Insufficient evidence to identify a cause",
    confidence: float = 0.1,
    evidence_ids: list[str] | None = None,
    findings: list[dict[str, Any]] | None = None,
    escalated: bool = True,
) -> str:
    return json.dumps(
        {
            "component": component,
            "cause": cause,
            "confidence": confidence,
            "evidence_ids": evidence_ids or [],
            "escalated": escalated,
            "findings": findings or [],
        }
    )


def reviewer_response(verdict: str = "agree", reason: str = "Evidence supports the diagnosis") -> dict:
    return response(json.dumps({"verdict": verdict, "reason": reason}))


class InvestigationLoopTests(unittest.TestCase):
    def test_truncated_tool_result_retains_its_evidence_id_within_size_limit(self) -> None:
        serialized = _serialize_result(
            {
                "evidence_id": "ev-001",
                "result": {"ok": True, "data": {"payload": "x" * MAX_TOOL_RESULT_CHARS}},
            }
        )
        parsed = json.loads(serialized)

        self.assertLessEqual(len(serialized), MAX_TOOL_RESULT_CHARS)
        self.assertEqual(parsed["evidence_id"], "ev-001")
        self.assertTrue(parsed["truncated"])

    def test_executes_allowlisted_tool_and_returns_result_for_follow_up(self) -> None:
        backend = OfflineBackend(
            {
                "prometheus": {
                    "range_queries": {
                        "up": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {"service_name": "checkout"},
                                    "values": [[1, "1"], [2, "1"]],
                                }
                            ],
                        }
                    }
                }
            }
        )
        queue = [
            response(
                calls=[tool_call("call-1", "prom_query", '{"promql":"up","minutes":2}')]
            ),
            response(
                diagnosis_content(
                    component="checkout",
                    cause="Checkout is responding",
                    confidence=0.8,
                    evidence_ids=["ev-001"],
                    findings=[
                        {
                            "claim": "Checkout is responding",
                            "evidence_ids": ["ev-001"],
                            "quotes": ['"checkout"'],
                        }
                    ],
                    escalated=False,
                )
            ),
            reviewer_response(),
        ]
        seen_messages: list[list[dict[str, Any]]] = []

        def fake_completion(**kwargs: Any) -> dict:
            seen_messages.append(list(kwargs["messages"]))
            return queue.pop(0)

        result = investigate(
            "Is checkout up?",
            {"suspects": []},
            backend=backend,
            completion_fn=fake_completion,
        )
        self.assertEqual(result["status"], "diagnosis")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(result["diagnosis"]["component"], "checkout")
        self.assertEqual(result["diagnosis"]["evidence_ids"], ["ev-001"])
        self.assertEqual(result["review"]["verdict"], "agree")
        self.assertEqual(result["tool_results"][0]["evidence_id"], "ev-001")
        self.assertEqual(result["evidence"][0]["tool"], "prom_query")
        self.assertTrue(result["tool_results"][0]["result"]["ok"])
        self.assertEqual(seen_messages[1][-1]["role"], "tool")
        self.assertIn('"ok":true', seen_messages[1][-1]["content"])
        self.assertEqual(len(_tool_definitions()), 9)

    def test_unknown_tool_never_executes_and_is_reported_to_model(self) -> None:
        queue = [
            response(
                calls=[
                    tool_call(
                        "call-bad",
                        "run_shell",
                        '{"command":"whoami"}',
                    )
                ]
            ),
            response(diagnosis_content()),
        ]
        follow_up: list[dict[str, Any]] = []

        def fake_completion(**kwargs: Any) -> dict:
            if len(follow_up) == 1:
                follow_up.append(kwargs["messages"][-1])
            else:
                follow_up.append({})
            return queue.pop(0)

        result = investigate(
            "Investigate safely",
            {},
            backend=OfflineBackend({}),
            completion_fn=fake_completion,
        )
        self.assertEqual(result["status"], "escalated")
        self.assertFalse(result["tool_results"][0]["result"]["ok"])
        self.assertIn("not available", follow_up[1]["content"])
        self.assertEqual(result["evidence"], [])

    def test_rejects_extra_or_invalid_tool_arguments(self) -> None:
        queue = [
            response(
                calls=[
                    tool_call(
                        "call-invalid",
                        "prom_query",
                        '{"promql":"up","minutes":2,"shell":"whoami"}',
                    )
                ]
            ),
            response(diagnosis_content()),
        ]

        def fake_completion(**_: Any) -> dict:
            return queue.pop(0)

        result = investigate(
            "Check a metric",
            {},
            backend=OfflineBackend({}),
            completion_fn=fake_completion,
        )
        self.assertEqual(result["status"], "escalated")
        self.assertFalse(result["tool_results"][0]["result"]["ok"])
        self.assertIn("Unsupported argument", result["tool_results"][0]["result"]["summary"])
        self.assertEqual(result["evidence"], [])

    def test_invalid_json_is_returned_as_tool_error(self) -> None:
        queue = [
            response(
                calls=[tool_call("call-invalid-json", "get_trace", "{not json")]
            ),
            response("The requested trace identifier was malformed."),
        ]

        def fake_completion(**_: Any) -> dict:
            return queue.pop(0)

        result = investigate(
            "Inspect trace",
            {},
            backend=OfflineBackend({}),
            completion_fn=fake_completion,
        )
        self.assertFalse(result["tool_results"][0]["result"]["ok"])
        self.assertIn("invalid JSON", result["tool_results"][0]["result"]["summary"])

    def test_malformed_tool_call_is_reported_without_execution(self) -> None:
        result = investigate(
            "Inspect a trace",
            {},
            backend=OfflineBackend({}),
            completion_fn=lambda **_: response(
                calls=[tool_call("", "get_trace", '{"trace_id":"trace-1"}')]
            ),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["steps"], 0)
        self.assertIn("without a valid call ID", result["error"])

    def test_stops_at_tool_budget_and_does_not_execute_extra_calls(self) -> None:
        queue = [
            response(calls=[tool_call("1", "list_services", "{}")]),
            response(calls=[tool_call("2", "list_services", "{}")]),
            response(calls=[tool_call("3", "list_services", "{}")]),
        ]

        def fake_completion(**_: Any) -> dict:
            return queue.pop(0)

        result = investigate(
            "List services",
            {},
            backend=OfflineBackend({"jaeger": {"services": ["checkout"]}}),
            max_steps=2,
            completion_fn=fake_completion,
        )
        self.assertEqual(result["status"], "step_limit")
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(result["tool_results"]), 2)

    def test_final_answer_can_follow_the_last_allowed_tool_call(self) -> None:
        queue = [
            response(calls=[tool_call("1", "list_services", "{}")]),
            response(
                diagnosis_content(
                    component="checkout",
                    cause="Jaeger reports checkout",
                    confidence=0.7,
                    evidence_ids=["ev-001"],
                    findings=[
                        {
                            "claim": "Jaeger reports checkout",
                            "evidence_ids": ["ev-001"],
                            "quotes": ['"checkout"'],
                        }
                    ],
                    escalated=False,
                )
            ),
            reviewer_response(),
        ]

        def fake_completion(**_: Any) -> dict:
            return queue.pop(0)

        result = investigate(
            "List services",
            {},
            backend=OfflineBackend({"jaeger": {"services": ["checkout"]}}),
            max_steps=1,
            completion_fn=fake_completion,
        )
        self.assertEqual(result["status"], "diagnosis")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(result["diagnosis"]["evidence_ids"], ["ev-001"])
        self.assertEqual(result["review"]["verdict"], "agree")

    def test_llm_request_error_is_explicit(self) -> None:
        def fail_completion(**_: Any) -> dict:
            raise LLMRequestError("provider unavailable")

        result = investigate("Incident", {}, completion_fn=fail_completion)
        self.assertEqual(result["status"], "error")
        self.assertIn("provider unavailable", result["error"])

    def test_missing_llm_configuration_is_returned_as_loop_error(self) -> None:
        with patch.dict("os.environ", {"LLM_MODEL": "provider/model"}, clear=True):
            result = investigate("Incident", {})
        self.assertEqual(result["status"], "error")
        self.assertIn("LLM_API_KEY or LLM_API_BASE", result["error"])

    def test_output_token_limit_is_not_reported_as_diagnosis(self) -> None:
        def truncated_completion(**_: Any) -> dict:
            item = response("Possible cause, but this is incomplete.")
            item["choices"][0]["finish_reason"] = "length"
            return item

        result = investigate("Incident", {}, completion_fn=truncated_completion)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["diagnosis"])

    def test_invalid_plain_text_diagnosis_is_rejected(self) -> None:
        result = investigate(
            "Incident",
            {},
            completion_fn=lambda **_: response("The payment service is failing."),
        )
        self.assertEqual(result["status"], "invalid_diagnosis")
        self.assertIsNone(result["diagnosis"])
        self.assertIn("valid JSON", result["validation_errors"][0])

    def test_rejects_invalid_inputs_before_calling_model(self) -> None:
        with self.assertRaises(ValueError):
            investigate("", {})
        invalid_triage: Any = []
        with self.assertRaises(ValueError):
            investigate("Incident", invalid_triage, completion_fn=lambda **_: None)
        with self.assertRaises(ValueError):
            investigate("Incident", {}, max_steps=0, completion_fn=lambda **_: None)
        with self.assertRaises(ValueError):
            investigate("Incident", {}, confidence_threshold=1.1, completion_fn=lambda **_: None)

    def test_reviewer_disagreement_escalates_the_validated_diagnosis(self) -> None:
        queue = [
            response(
                calls=[tool_call("call-1", "list_services", "{}")]
            ),
            response(
                diagnosis_content(
                    component="checkout",
                    cause="Checkout is reported by telemetry",
                    confidence=0.9,
                    evidence_ids=["ev-001"],
                    findings=[
                        {
                            "claim": "Checkout is reported by telemetry",
                            "evidence_ids": ["ev-001"],
                            "quotes": ['"checkout"'],
                        }
                    ],
                    escalated=False,
                )
            ),
            reviewer_response("disagree", "The telemetry does not establish a root cause"),
        ]

        result = investigate(
            "Investigate checkout",
            {},
            backend=OfflineBackend({"jaeger": {"services": ["checkout"]}}),
            completion_fn=lambda **_: queue.pop(0),
        )

        self.assertEqual(result["status"], "escalated")
        self.assertTrue(result["diagnosis"]["escalated"])
        self.assertEqual(result["review"]["verdict"], "disagree")
        self.assertTrue(any("Reviewer disagreed" in item for item in result["escalation_reasons"]))

    def test_low_confidence_escalates_even_when_reviewer_agrees(self) -> None:
        queue = [
            response(
                calls=[tool_call("call-1", "list_services", "{}")]
            ),
            response(
                diagnosis_content(
                    component="checkout",
                    cause="Checkout is reported by telemetry",
                    confidence=0.69,
                    evidence_ids=["ev-001"],
                    findings=[
                        {
                            "claim": "Checkout is reported by telemetry",
                            "evidence_ids": ["ev-001"],
                            "quotes": ['"checkout"'],
                        }
                    ],
                    escalated=False,
                )
            ),
            reviewer_response(),
        ]

        result = investigate(
            "Investigate checkout",
            {},
            backend=OfflineBackend({"jaeger": {"services": ["checkout"]}}),
            confidence_threshold=0.7,
            completion_fn=lambda **_: queue.pop(0),
        )

        self.assertEqual(result["status"], "escalated")
        self.assertTrue(result["diagnosis"]["escalated"])
        self.assertEqual(result["review"]["verdict"], "agree")
        self.assertTrue(any("below the configured threshold" in item for item in result["escalation_reasons"]))

    def test_reviewer_failure_escalates_safely(self) -> None:
        queue = [
            response(
                calls=[tool_call("call-1", "list_services", "{}")]
            ),
            response(
                diagnosis_content(
                    component="checkout",
                    cause="Checkout is reported by telemetry",
                    confidence=0.9,
                    evidence_ids=["ev-001"],
                    findings=[
                        {
                            "claim": "Checkout is reported by telemetry",
                            "evidence_ids": ["ev-001"],
                            "quotes": ['"checkout"'],
                        }
                    ],
                    escalated=False,
                )
            ),
            response("not JSON"),
        ]

        result = investigate(
            "Investigate checkout",
            {},
            backend=OfflineBackend({"jaeger": {"services": ["checkout"]}}),
            completion_fn=lambda **_: queue.pop(0),
        )

        self.assertEqual(result["status"], "escalated")
        self.assertTrue(result["diagnosis"]["escalated"])
        self.assertEqual(result["review"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
