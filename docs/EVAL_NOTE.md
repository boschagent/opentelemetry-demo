# Incident Zero Evaluation Note

## 1. Objective

This document records the evaluation of Incident Zero, an AI On-Call Engineer for the OpenTelemetry Demo.

The objective is to measure whether the system can diagnose application and infrastructure incidents using telemetry, produce evidence-grounded findings, propose acceptable corrective actions, and escalate when uncertain.

## 2. Evaluation Scope

The evaluation will cover:

- Application failures.
- Infrastructure failures.
- Service unavailability.
- Latency-related incidents.
- Resource-related incidents.
- Combinations of independent incidents.
- Evidence validity.
- Safe action selection.
- Escalation behaviour.

The evaluation must include both live incidents and recorded incident bundles.

## 3. Dataset

### Practice Incidents

The initial evaluation dataset consists of the organisers' practice incidents.

| Field | Value |
|---|---|
| Total practice incidents | 10 |
| Source | Organisers' incident bundles |
| Answer key | Human-written |
| Evaluation backend | Offline |
| Dataset version | TBD |

The actual incident identifiers and expected outcomes will be recorded after the bundles are obtained.

### Hidden Incidents

The hidden evaluation is expected to contain unseen incidents and combinations of incidents.

The agent must not use hard-coded answers for practice incidents.

## 4. Ground Truth

The evaluation answer key will be maintained in:

    eval/labels.yaml

`eval/labels.yaml` uses this structure:

```yaml
version: 1
incidents:
  - incident_id: practice-01
    expected_component: checkout
    expected_cause_class: connection refused
    acceptable_actions:
      - restart_service
    expected_escalation: false
    expected_recovery: true
```

`incident_id` must match the fixture directory name or the explicit ID in the
bundle. `expected_component` is compared case-insensitively with the diagnosis
component. `expected_cause_class`, when supplied, is a case-insensitive phrase
matched against the diagnosis cause and finding claims; the provisional
diagnosis schema does not yet include a dedicated cause-class field.
`acceptable_actions`, `expected_escalation`, and `expected_recovery` are
optional. Labels with no action or recovery truth are omitted from those metric
denominators.

Recorded bundles live at `eval/fixtures/<incident-id>/bundle.json` and use the
offline backend schema described in `docs/ARCHITECTURE.md`. The bundle may also
contain an `incident` object with `id` and `description`; absent a description,
the runner uses a generic incident prompt. Optional recovery observations use
`recovery.service`, `recovery.before_health`, and `recovery.after_health`, each
health value matching the `service_red` tool result shape.

The harness structurally separates inference from scoring: it discovers and
runs every bundle before opening `labels.yaml`. The answer key is not passed to
triage, the investigator, the reviewer, or any completion callback. Actions
are dry-run only; the project currently has no action planner, so action
accuracy is reported as not evaluated unless a caller provides a dry-run
proposal selector.

The answer key is written by humans from incident descriptions and verified against the organisers' expected outcomes.

The agent must not read the answer key during investigation.

## 5. Metrics

### Diagnosis Accuracy

Measures whether the agent identifies the expected component and cause class.

    Diagnosis Accuracy =
    Correct component-and-cause matches / incidents with diagnosis labels

### Action Accuracy

Measures whether the proposed corrective action is acceptable for the incident.

    Action Accuracy =
    Acceptable Actions / Total Action-Evaluated Incidents

### Evidence Validity

Measures whether the diagnosis passes the same evidence validator used by the
investigation loop, including evidence IDs, exact quotes, numeric grounding,
and component grounding. The denominator includes investigated cases that
produced a diagnosis.

### Escalation

Measures whether the result status is exactly `escalated` when an
`expected_escalation` label is present.

### Recovery Verification

When a label has `expected_recovery` and the bundle contains valid before/after
RED observations, measures agreement with `verify_recovery`. Missing or
insufficient telemetry is not counted as a recovery prediction.

Each metric reports the number correct, the evaluated denominator, and an
accuracy fraction (`null` when its denominator is zero). Per-case rows show
`null` for metrics that were not evaluated.

## 6. Experimental Procedure

1. Add each recorded bundle at `eval/fixtures/<incident-id>/bundle.json`.
2. Add the verified human label for each bundle to `eval/labels.yaml`.
3. Run `python -m eval.run_all` from the project directory.
4. Inspect the terminal scoreboard and `eval/results.json`.
5. Review per-case outcomes and error-analyze failures; do not remove failed
   incidents from the dataset.

Live testing will be performed separately against the organisers' running demo.

## 7. Results

No evaluation results have been recorded yet. The fixture directory and answer
key currently contain no practice cases.

| Metric | Result |
|---|---|
| Practice incidents evaluated | TBD |
| Correct diagnoses | TBD |
| Diagnosis accuracy | TBD |
| Acceptable action rate | TBD |
| Evidence validity | TBD |
| Appropriate escalations | TBD |
| Recovery verification accuracy | TBD |

Results will be populated only after actual evaluation runs.

## 8. Error Analysis

For each incorrect diagnosis, record:

- Incident ID.
- Agent diagnosis.
- Expected diagnosis.
- Evidence collected.
- Missing evidence.
- Incorrect reasoning.
- Whether escalation would have been appropriate.
- Proposed improvement.

Do not remove failed incidents from the evaluation dataset.

## 9. Limitations

- Evaluation results depend on the quality of the recorded incident bundles.
- The answer key must be independently verified.
- Confidence thresholds require calibration.
- Live telemetry may differ from offline data.
- The hidden evaluation dataset is unavailable during development.
- Passing practice incidents does not guarantee hidden-test accuracy.

## 10. Conclusion

This section will be completed after the evaluation results are available.

The final note will report actual performance, common failure cases, and remaining limitations.