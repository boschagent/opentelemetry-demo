# Incident Zero Architecture

## 1. Overview

Incident Zero is an AI On-Call Engineer designed to investigate application and infrastructure incidents in the OpenTelemetry Demo.

The system combines deterministic telemetry analysis with LLM-based investigation.

Its primary responsibilities are:

- Identify suspicious services.
- Investigate incidents using telemetry.
- Produce evidence-grounded diagnoses.
- Propose safe corrective actions.
- Request human approval before acting.
- Verify recovery.
- Escalate when uncertain.

The system is designed to support both live telemetry and recorded incident bundles.

## 2. High-Level Architecture

    Incident Alert
         |
         v
    Incident Detector
         |
         v
    Deterministic Triage
         |
         v
    Ranked Suspects
         |
         v
    AI Investigator
         |
         +------> Prometheus Tools
         |
         +------> Jaeger Tools
         |
         +------> Logs Tools
         |
         +------> Docker Health Tools
         |
         v
    Evidence Ledger
         |
         v
    Diagnosis Validator
         |
         v
    Reviewer Agent
         |
         +------> Escalation
         |
         v
    Action Planner
         |
         v
    Human Approval
         |
         v
    Action API
         |
         v
    Recovery Verification
         |
         +------> Rollback and Escalation
         |
         v
    Incident Report

## 3. Components

### 3.1 Incident Detection

The system receives an incident through a manual request or an alert detector.

The initial implementation will support manual incident execution.

Automatic detection will be implemented after the investigation workflow is functional.

### 3.2 Deterministic Triage

The triage engine processes telemetry and calculates suspicious signals.

Examples:

- Increased error rate.
- Increased request latency.
- Container stopped or unhealthy.
- Container restart activity.
- High CPU or memory usage.
- Client-side latency significantly exceeding server-side latency.

The output is a ranked list of suspicious services with supporting signals.

`agent/triage.py` ranks only telemetry-backed suspects; the incident description
is included in the output for context but is not used to select a cause. Call
`triage(incident, telemetry)` with Step 4 tool results under `service_red`,
`container_health`, `resource_metrics`, `traces`, `logs`, and `deploy_history`.
Each value may be a tool response (`ok`, `summary`, `data`, `source`) or its data
payload. The result contains a stable symptom fingerprint, ranked suspects,
signal details, and any unavailable telemetry sources. A failed tool is
reported as unavailable and does not produce a healthy-looking default.

Signals cover increased/error-heavy RED metrics, stopped or unhealthy
containers, restarts and OOM kills, high CPU, monotonically increasing memory,
error spans, connection failures, client/server latency gaps, recent
container events, cache errors, and high traffic accompanied by broad latency
increases and a low error ratio. Scores prioritize direct health/error evidence
over indirect symptoms; equal scores are ordered by service name. Thresholds
are deterministic heuristics, not a diagnosis or authorization to take an
action. Docker containers explicitly labeled as belonging to another Compose
project are excluded from suspects and listed in `ignored_containers`, preventing
unrelated local workloads from contaminating this demo's triage. Triage does not
perform corrective actions.

### 3.3 Telemetry Tools

The investigator can call read-only tools to collect evidence.

| Tool | Responsibility |
|---|---|
| prom_query | Query Prometheus metrics |
| service_red | Calculate service error rates and latency changes |
| list_services | Retrieve available Jaeger services |
| find_error_traces | Find traces containing errors |
| get_trace | Inspect trace spans and relationships |
| get_logs | Retrieve recent service logs |
| container_health | Inspect container status and resource usage |
| deploy_history | Identify recent container changes |

All tool outputs must follow the agreed tool response interface.

    {
        "ok": true,
        "summary": "Human-readable summary",
        "data": {},
        "source": "Exact query or command"
    }

Tool errors must return structured failures instead of terminating the investigation.

### 3.4 Live and Offline Backends

Two backends will implement the same interface.

LIVE backend:

- Reads Prometheus metrics.
- Reads Jaeger traces.
- Reads service logs.
- Reads Docker container health.

OFFLINE backend:

- Reads recorded incident bundles.
- Provides the same data interface as the LIVE backend.
- Enables repeatable evaluation without a running demo.

The investigation logic must not depend on which backend is selected.

### 3.5 AI Investigator

The LLM investigates ranked suspects using the available telemetry tools.

The investigation loop:

1. Receives the incident description and triage results.
2. Selects a read-only telemetry tool.
3. Executes the tool.
4. Returns the result to the model for review.
5. Calls additional tools if required.
6. Produces a diagnosis or escalates.

The implementation exposes only the telemetry tools defined by the Python
allowlist; it cannot execute arbitrary code, shell commands, or Docker commands.
It uses a configurable maximum number of tool calls, defaulting to 12. The loop
returns an explicit status when a provider fails, output is incomplete, or the
tool-call budget is reached. It produces a diagnosis only after validating the
model's JSON response against the evidence ledger. Its diagnosis is
investigative only.

### 3.6 Evidence Ledger

Every relevant tool result receives a unique evidence ID.

Example:

    ev-001
    ev-002
    ev-003

Each entry records:

- Evidence ID.
- Tool name.
- Tool arguments.
- Raw successful tool output.
- UTC timestamp.

IDs are sequential within one investigation (`ev-001`, `ev-002`, ...). Failed
tool calls are retained in the loop's tool results but are not treated as
evidence. Successful tool replies include the assigned ID, and the final
diagnosis and findings must cite IDs in the ledger.

### 3.7 Sanitisation

Telemetry is sanitised before it reaches the investigator.

The sanitiser must remove prohibited feature-flag information from span attributes, span events, and logs.

The sanitiser must have deterministic tests using nested span and log fixtures.

Feature-flag configuration and flag-related interfaces must not be used to diagnose incidents.

### 3.8 Diagnosis Validation

The validator checks:

- Required diagnosis fields.
- Strict compliance with the current provisional JSON schema.
- Evidence IDs exist and findings cite the same IDs as the diagnosis.
- Every quoted JSON substring occurs in the cited raw tool output.
- Numeric literals in findings occur exactly in the cited raw tool output.
- The component appears in cited raw evidence (unless it is `unknown`).
- Each non-escalated diagnosis has a finding that supports its cause.

A diagnosis with invalid JSON, schema, citations, quotes, or support returns
`invalid_diagnosis` and must not proceed to action planning. The provisional
schema contains `component`, `cause`, `confidence`, `evidence_ids`, `escalated`,
and `findings` (`claim`, `evidence_ids`, and exact `quotes`). Replace it when
the organisers provide their required JSON schema.

### 3.9 Reviewer Agent

The reviewer attempts to disprove the investigator's diagnosis.

It checks whether:

- The proposed root cause matches the telemetry.
- The evidence supports the selected component.
- Another service could explain the symptoms.
- The investigation has enough information to proceed.

Reviewer disagreement or low confidence results in escalation.

The reviewer is a separate, skeptical LLM pass that receives the validated
diagnosis and cited evidence, has no tools, and must return a structured
`agree`/`disagree` verdict with a reason. An invalid or failed review also
escalates rather than allowing a diagnosis to proceed. The investigation
function defaults to a confidence threshold of `0.7`, configurable through its
`confidence_threshold` argument. A result proceeds as `diagnosis` only if the
investigator did not already request escalation, the reviewer agrees, and
confidence meets the threshold. Escalated results are not action authorization;
the later action workflow must gate on the non-escalated reviewed result and
still apply its own approval requirements.

The confidence threshold must be calibrated using practice incident evaluation.

### 3.10 Action Planner

The action planner selects a corrective action from the approved runbook.

It must not generate arbitrary shell commands or execute unrestricted code.

Every action must have:

- An allowlisted action name.
- Validated parameters.
- Preconditions.
- An approval requirement.
- A verification procedure.
- A rollback strategy where supported.

`agent/act/runbook.yaml` currently allowlists only `restart_service`, requires
the target to match the diagnosed component, and declares no rollback operation.
No feature-flag action is enabled. The runbook is loaded with safe YAML parsing;
unknown actions and extra or malformed parameters are rejected. The action
runner also enforces the investigation's reviewed, non-escalated status and
confidence threshold before displaying a proposal.

### 3.11 Human Approval

The approval interface displays:

- Incident description.
- Diagnosis.
- Supporting evidence.
- Confidence.
- Proposed action.
- Expected impact.

The operator must explicitly enter `yes`; other values are rejected. Dry-run
is the default and returns a proposal without requesting approval or calling an
API.

Live calls require both explicit approval and a configured Action API adapter.
The organisers' API documentation is not yet available, so no endpoint, HTTP
method, or external payload is guessed; the adapter raises a configuration
error until implemented against those docs.

### 3.12 Action Execution

Corrective actions are executed only through the organisers' Action API.

The agent must:

- Check the kill switch before every action.
- Validate the action against the runbook.
- Record the approval.
- Record the API request and response.
- Stop if the action fails.

The exact API interface and supported operations depend on organisers' documentation.
The environment variable `INCIDENT_ZERO_KILL_SWITCH` blocks actions for
`1`/`true`/`yes`/`on`; malformed values fail closed. The check occurs after
approval and immediately before every API call. API submission is not recovery:
the result remains `submitted` until Step 10 verification reports recovery.

### 3.13 Recovery Verification

After a submitted action, call `agent.act.verify.verify_action_recovery` with
pre-action and post-action `service_red` results. Recovery requires both the
error rate and p95 latency for the target service to be available and within
tolerance of the pre-action historical baseline. The default error-rate
tolerance is an absolute `0.01`; latency allows the greater of `50 ms` or `20%`
of its baseline. These are configurable initial heuristics, not calibrated SLOs.
Missing services, failed telemetry, or missing/non-finite metrics are
`unverified` and never count as recovery.

If recovery fails:

1. Stop additional actions.
2. If the runbook defines an inverse, validate it and require a separate exact
   `yes` approval and a fresh kill-switch check before submitting it.
3. Re-measure health after a rollback submission.
4. Escalate to the operator and record failed or unavailable rollback details.

The current restart runbook has no inverse action, so failed verification stops
and escalates without attempting an invented rollback. An Action API response
alone never indicates recovery.

### 3.14 Incident Reporting

Each investigation produces:

- Incident JSON.
- Incident Markdown report.

`agent.report.write_report` writes a paired JSON and Markdown report under
`incidents/` by default. Reports include incident description, an event
timeline, suspected root cause, evidence, confidence and reviewer verdict,
proposed action, approval, action result, recovery verification, and prevention
suggestions. Sections not yet reached by the workflow remain `null` and are not
reported as successful. Filenames use a validated report ID and existing
reports are never overwritten.

Reports are stored in the incidents directory.

## 4. Safety Boundaries

The system must follow these rules:

- No diagnosis based on feature-flag settings.
- No arbitrary shell or code execution.
- No action outside the approved runbook.
- No corrective action without human approval.
- No action when the kill switch is active.
- No recovery claim without verification.
- Escalation when evidence is insufficient.

## 5. Evaluation

The architecture supports offline testing against recorded incident bundles.

The evaluation harness compares the agent output against a human-written answer key.

The answer key must remain inaccessible to the agent during investigation.

Run the offline harness from the project directory with
`python -m eval.run_all`. It reads bundles from
`eval/fixtures/<incident-id>/bundle.json`, runs all investigations, then loads
`eval/labels.yaml` for scoring and writes `eval/results.json`. Metrics report
their evaluated denominators; action and recovery accuracy remain not evaluated
when no proposal or usable before/after health data exists. Actions are dry-run
only, and the current system has no built-in action planner. See
`docs/EVAL_NOTE.md` for the label schema and scoring details.

## 6. Open Decisions

The following require confirmation from the organisers:

- Exact diagnosis JSON schema.
- Action API endpoints and parameters.
- Supported corrective actions.
- Feature-flag remediation permissions.
- Allowed Docker read-only operations.
- Incident alert input format.
- Hidden-test scoring details.
- Runtime LLM provider and spend limit.
# Telemetry tools

The read-only tools in `agent/tools/` share a backend interface. Pass no backend
to use `LiveBackend`; pass `OfflineBackend` to use recorded responses. Every
public tool returns:

```json
{"ok": true, "summary": "Short result", "data": {}, "source": "Query or command"}
```

Expected operational failures use the same shape with `ok: false`, an empty
`data` object, and a summary that includes the error. Do not treat a failed tool
result as evidence that a service is healthy.

## Live services

- Prometheus uses `PROMETHEUS_URL` or `http://localhost:9090`.
- Jaeger uses `JAEGER_URL` or `http://localhost:8080/jaeger/ui`.
- Container state, inspect data and logs are collected through the local Docker
  CLI. No action or mutation command is exposed by these tools.

`prom_query` returns min/max/last and points for each range-query series.
`service_red` measures request rate, error rate and p95 latency over a recent
three-minute window and compares it with the preceding baseline window.
`container_resources` discovers available `container_*` metric names before
querying supported CPU and memory metrics. `container_health` includes the
Docker state/restart details and the result of that Prometheus resource query.

Jaeger trace summaries include the root operation, span tree, error spans and
client-to-server duration gaps when span kinds are available. All Jaeger output
is normalized from the v3 OTLP response and sanitized before it leaves a tool.
Log lines are deduplicated with counts;
`flagd` and `flagd-ui` log requests are denied.

## Offline bundle format

An offline bundle is a JSON object with optional `prometheus`, `jaeger`, and
`docker` sections. Prometheus query fixtures are keyed by the exact PromQL
string; Jaeger trace fixtures can be keyed by trace ID. Example:

```json
{
  "prometheus": {
    "range_queries": {
      "up": {
        "resultType": "matrix",
        "result": [{"metric": {"service_name": "checkout"}, "values": [[1, "1"]]}]
      }
    },
    "metric_names": ["container_cpu_usage_seconds_total"]
  },
  "jaeger": {
    "services": ["checkout"],
    "traces": {"trace-id": {"traceID": "trace-id", "processes": {}, "spans": []}},
    "traces_by_service": {"checkout": []}
  },
  "docker": {
    "containers": [],
    "inspect": {},
    "logs": {}
  }
}
```

The supported fields are `prometheus.range_queries`, `prometheus.queries`,
`prometheus.metric_names`, `jaeger.services`, `jaeger.traces`,
`jaeger.traces_by_service`, `docker.containers`, `docker.inspect`, and
`docker.logs`. Missing fixture data is reported as an error by the tool.
Jaeger trace entries may be normalized Jaeger span objects or full v3 responses
under `result.resourceSpans`; when using the latter, key the `jaeger.traces`
object by trace ID so the offline adapter can associate each response with its
ID.