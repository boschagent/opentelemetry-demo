from __future__ import annotations

import json
import unittest
from typing import Any

from agent.core.reviewer import review_diagnosis


def response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


class ReviewerTests(unittest.TestCase):
    def test_sends_diagnosis_and_evidence_without_tools(self) -> None:
        diagnosis = {"component": "payment", "cause": "elevated errors"}
        evidence = [{"evidence_id": "ev-001", "raw_output": {"error_rate": 0.25}}]
        calls: list[dict[str, Any]] = []

        def completion(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return response(json.dumps({"verdict": "agree", "reason": "Evidence supports it"}))

        verdict = review_diagnosis(
            diagnosis,
            evidence,
            model="example/model",
            completion_fn=completion,
        )

        self.assertEqual(verdict["status"], "reviewed")
        self.assertEqual(verdict["verdict"], "agree")
        self.assertEqual(calls[0]["tools"], [])
        self.assertEqual(calls[0]["model"], "example/model")
        payload = json.loads(calls[0]["messages"][1]["content"])
        self.assertEqual(payload["diagnosis"], diagnosis)
        self.assertEqual(payload["evidence"], evidence)

    def test_invalid_reviewer_output_is_a_disagreement(self) -> None:
        verdict = review_diagnosis(
            {},
            [],
            completion_fn=lambda **_: response(json.dumps({"verdict": [], "reason": "bad"})),
        )

        self.assertEqual(verdict["status"], "error")
        self.assertEqual(verdict["verdict"], "disagree")

    def test_incomplete_reviewer_output_is_a_disagreement(self) -> None:
        def completion(**_: Any) -> dict[str, Any]:
            result = response(json.dumps({"verdict": "agree", "reason": "ok"}))
            result["choices"][0]["finish_reason"] = "length"
            return result

        verdict = review_diagnosis({}, [], completion_fn=completion)

        self.assertEqual(verdict["status"], "error")
        self.assertEqual(verdict["verdict"], "disagree")


if __name__ == "__main__":
    unittest.main()
