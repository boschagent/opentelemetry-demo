# Incident Zero

## AI On-Call Engineer

Incident Zero is an AI-powered on-call engineer built for the Bosch × PESU hackathon.

The system investigates incidents in the OpenTelemetry Demo (Astronomy Shop), identifies likely root causes using telemetry, proposes safe corrective actions, and verifies whether the application recovers.

The agent uses a combination of deterministic Python logic and LLM-based investigation. It must ground its findings in actual telemetry and request human approval before performing any corrective action.

## Project Goals

- Investigate application and infrastructure incidents.
- Identify likely root causes using Prometheus metrics, Jaeger traces, logs, and Docker health information.
- Support both live telemetry and recorded incident bundles.
- Validate diagnosis evidence before producing reports.
- Require human approval before executing corrective actions.
- Verify recovery after an approved action.
- Escalate when evidence is insufficient or the diagnosis is uncertain.
- Evaluate performance against practice incidents and unseen incident combinations.

## Architecture

The system follows this workflow:

1. Detect or receive an incident.
2. Collect telemetry from the demo application.
3. Rank suspicious services using deterministic triage.
4. Investigate using an LLM and read-only tools.
5. Validate evidence and review the diagnosis.
6. Select an allowed corrective action.
7. Request human approval.
8. Execute the action through the organisers' Action API.
9. Verify recovery and generate an incident report.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Technology Stack

- Python 3.11
- Docker Compose
- Prometheus
- Jaeger
- OpenTelemetry
- Pydantic
- LiteLLM
- pytest
- Streamlit (planned approval interface)

## Prerequisites

Install:

- Git
- Docker Desktop with Docker Compose v2
- Python 3.11
- VS Code

Verify your installation:

    git --version
    docker compose version
    python --version

## Setup

Clone the repository:

    git clone <repository-url>
    cd opentelemetry-demo

Create and activate a Python virtual environment:

    python -m venv .venv

Windows:

    .venv\Scripts\activate

Install project dependencies:

    pip install -r requirements.txt

Start the OpenTelemetry Demo and observability services:

    docker compose -f compose.yaml -f compose.observability.yaml up -d

Check running services:

    docker compose -f compose.yaml -f compose.observability.yaml ps -a

## Local Services

The following addresses are expected for the current demo configuration:

| Service | URL |
|---|---|
| Astronomy Shop | http://localhost:8080 |
| Jaeger | http://localhost:8080/jaeger/ui |
| Grafana | http://localhost:8080/grafana/ |
| Prometheus | http://localhost:9090 |

Verify the actual endpoints against the running Compose configuration.

## Environment Variables

Create a local `.env` file for runtime configuration.

Example:

    LLM_API_KEY=<provided-api-key>
    LLM_MODEL=<configured-model>
    # Optional for compatible gateways or self-hosted providers:
    LLM_API_BASE=<provider-api-base>

Do not commit API keys or other secrets.

Set `LLM_MODEL` to a LiteLLM-supported provider/model identifier. Configure either
`LLM_API_KEY` or `LLM_API_BASE` (the latter for a compatible gateway that does not
require a separate key). The investigation module reads these variables at runtime.

## Running the Agent

The bounded investigation and skeptical review run after deterministic triage.
Call the function with the incident description and triage result:

    from agent.core.loop import investigate
    result = investigate(incident_description, triage_result)

The loop can execute only its allowlisted, read-only telemetry tools and defaults
to a maximum of 12 tool calls. Successful tool results are recorded in an
evidence ledger and returned with IDs such as `ev-001`. The model must return a
JSON diagnosis whose evidence IDs and exact quoted values are validated against
that ledger; numeric literals in findings must also match the raw output. The
current provisional schema uses `component`, `cause`,
`confidence`, `evidence_ids`, `escalated`, and supported `findings`; replace it
with the organisers' schema when provided.

The validated diagnosis is passed to a separate skeptical LLM review. The
reviewer returns a structured `agree` or `disagree` verdict and a reason. If it
disagrees, fails to return a valid verdict, or confidence is below the default
threshold of `0.7`, the result is marked `escalated`; set
`confidence_threshold` on `investigate` to configure the threshold. The loop
returns `diagnosis` only after validation and an agreeing review; otherwise it
may return `escalated`, `invalid_diagnosis`, `step_limit`, `incomplete`, or
`error`. A diagnosis is investigative output only, not approval to perform an
action. A command-line runner is still planned.

## Testing

Run deterministic self-tests:

    python -m agent.selftest

Run the offline incident evaluation harness:

    python -m eval.run_all --fixtures eval\fixtures --labels eval\labels.yaml

The harness writes its JSON scoreboard to `eval\results.json`. Recorded bundles
are loaded from `eval\fixtures\<incident-id>\bundle.json`; human labels stay in
the separate answer key and are loaded only after every agent run completes.
See [docs/EVAL_NOTE.md](docs/EVAL_NOTE.md) for the bundle, label, and metric
formats. No practice bundles or human labels are included yet, so the initial
run reports zero evaluated cases. Evaluation never executes live actions;
action accuracy is not evaluated unless a dry-run proposal is supplied.

The self-test command is available independently of the evaluation harness.

## Safe actions

`agent.act.runner.run_action` validates a proposal against
[`agent/act/runbook.yaml`](agent/act/runbook.yaml). The current runbook allows
only `restart_service`, requires the target to match the reviewed diagnosis,
and defines no rollback operation. Dry-run is the default:

```python
from agent.act.runner import run_action

proposal = run_action(
    investigation_result,
    "restart_service",
    {"service": "checkout"},
)
```

Dry-run returns the proposal and makes no API request. Live execution requires
`dry_run=False`, an exact human approval response of `"yes"`, the kill switch
to be off, and an `ActionAPIClient` adapter configured against the organisers'
documentation. The live adapter is intentionally not implemented until that
documentation is available; no API endpoint or payload is assumed.
`INCIDENT_ZERO_KILL_SWITCH=true` blocks live actions. The runner returns
`submitted`, not “resolved”; recovery must be independently verified before
claiming resolution.

## Recovery verification

After a submitted action, compare pre-action and post-action `service_red`
results with `agent.act.verify.verify_action_recovery`. Verification requires
both error rate and p95 latency for the target service to be available and
within the pre-action historical baseline tolerance. Defaults are +0.01
absolute error rate and the greater of +50 ms or +20% latency; callers can
override these initial, uncalibrated thresholds. Missing or failed telemetry
returns an unverified/failure result, never success. If recovery fails, further
actions stop; an inverse is attempted only when configured in the runbook and
after separate explicit approval and a fresh kill-switch check. The current
restart action has no rollback inverse, so failed recovery escalates.

## Incident reports

Create paired JSON and Markdown reports from completed workflow results:

```python
from agent.report import write_report

paths = write_report(
    incident_description,
    investigation_result,
    action_result=action_result,
    recovery_verification=recovery_result,
    prevention_suggestions=["Add an alert for sustained checkout errors"],
)
```

Reports are written under `incidents/` with a unique, safe report ID. Pass
`output_dir` for another destination or `report_id` for a chosen filename;
existing reports are never overwritten. The report includes the timeline,
diagnosis, evidence, confidence and reviewer verdict, action proposal, approval,
action result, recovery verification, and prevention suggestions. Unavailable
workflow sections are recorded as `null` rather than represented as successful.

## Safety Rules

- Diagnosis must be based on telemetry.
- The agent must not read feature-flag configuration or use the feature-flag UI for diagnosis.
- Feature-flag information must be removed from tool output as required by the hackathon rules.
- The agent may execute corrective actions only through the organisers' Action API.
- Corrective actions must be restricted to an approved runbook.
- Human approval is required before execution.
- Dry-run is the default during development.
- The kill switch must be checked before every action.
- Uncertain diagnoses must be escalated.
- Recovery must be verified after an action.

## Team

See [TEAM.md](TEAM.md) for team members and responsibilities.

## Evaluation

See [docs/EVAL_NOTE.md](docs/EVAL_NOTE.md) for evaluation methodology and results.

## Run Log

See [docs/RUNLOG.md](docs/RUNLOG.md) for implementation progress and experiment records.

## Known Limitations

- The exact organisers' diagnosis schema must be integrated.
- The Action API implementation depends on the organisers' API documentation.
- Diagnosis accuracy depends on telemetry quality and incident coverage.
- Confidence thresholds require calibration using practice incidents.
- Recovery verification thresholds require testing against the actual demo.
- The live and offline backends must be validated against the organisers' demo version.

## Acknowledgements

Built for the Bosch × PESU hackathon using the OpenTelemetry Demo.
# Incident Zero

Incident Zero investigates OpenTelemetry Demo incidents using read-only telemetry tools.

## Telemetry tools

Each tool returns the same shape: `ok`, `summary`, `data`, and `source`. A failed
query returns `ok: false` with an explanation rather than raising through the
agent. Tools accept an optional backend so the same calls can use live telemetry
or a saved JSON bundle.

The implemented read-only tools are:

- `prom_query(promql, minutes=15, step="15s")`
- `service_red(service=None, minutes=10)`
- `container_resources(minutes=10)`
- `list_services()`
- `find_error_traces(service, lookback="15m", limit=20)`
- `get_trace(trace_id)`
- `get_logs(service, minutes=10, grep=None, level=None, max_lines=60)`
- `container_health()`
- `deploy_history(minutes=120)`

Jaeger and log output is passed through `sanitize.redact`; feature-flag and
flagd-related attributes and log lines are excluded.

## Configure and run

The live backend reads `PROMETHEUS_URL` (default `http://localhost:9090`) and
`JAEGER_URL` (default `http://localhost:8080/jaeger/ui`). It uses Jaeger's v3
query API and normalizes returned OTLP traces before summarizing them. It uses
the local Docker CLI for container state and logs. These tools are read-only.

From this directory, run the independent telemetry tests with:

```powershell
python -m unittest discover -s agent\tests -v
```

For an offline run, pass an `OfflineBackend` a bundle directory containing
`bundle.json`, or a JSON file path. The expected fixture sections and fields are
documented in [ARCHITECTURE.md](docs/ARCHITECTURE.md).