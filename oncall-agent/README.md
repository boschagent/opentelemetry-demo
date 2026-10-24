# Incident Zero: AI On-Call Engineer (team boschagent)

An agent that receives an alert from the OpenTelemetry Astronomy Shop and works through the first hour of on-call. It investigates with real tools (Prometheus metrics and history, Jaeger traces, container logs and health, change history) and names the root cause with grounded evidence and a confidence score. It then proposes an **allowlisted** fix, waits for a human **"yes"** that is bound to that exact action, applies the fix **only through the Action API**, verifies recovery, rolls back or escalates if needed, and writes an incident report.

> Python 3.11+, **standard library only**: no `pip install` is needed for the agent.

## Results on real incidents (recorded on our laptop, 24 Oct 2026)
**10/10 correct, 0 wrong, with no LLM at all** over 10 real faults: cart / product-catalog / quote / database stopped, payment / Valkey frozen, memory limit squeezed, CPU limit squeezed, one combined fault, and a healthy system with no false alarm. Faults with no safe automatic fix (lowered limits) are diagnosed correctly and escalated, as the brief asks. Details, ablations and robustness: [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) and [eval/SCOREBOARD.md](eval/SCOREBOARD.md).

## What is different about our agent
1. **Code finds suspects, the LLM only checks them.** The deterministic core reaches 10/10 on its own, so a weaker or stronger LLM changes the explanation, not the safety.
2. **Graph root-cause analysis.** Each series is scored against its own past with robust median/MAD statistics, the call graph is learned from live traces, and a random walk with "who broke first" onset ordering pushes blame from symptoms to the origin. Two independent roots are reported as a combined incident.
3. **Name-free few-shot memory.** Incidents become fingerprints without service names, so experience transfers to services never seen before. It helps most when evidence is missing (+3 correct without Docker access).
4. **Approval bound to the exact action.** The human approves a specific proposal (terminal `yes` or the web approval page). The Action API runs only that action with those parameters, once, within 15 minutes.
5. **Adversarial safety.** Telemetry is untrusted. Text in logs that tries to instruct the LLM is flagged, and even a hijacked LLM cannot act outside the allowlist or skip the human. Feature-flag data is stripped everywhere, including flag metrics in Prometheus.
6. **Measured, not claimed.** Every live bug became one of the 46 offline tests, and every real incident is recorded and replayable for scoring, ablation and robustness runs.

## The demo page (one command, everything in the browser)
```powershell
python -m oncall.ui        # or double-click start_ui.bat  ->  http://127.0.0.1:8800
```
Three tabs:
- **Live demo:** five steps: 1 check the system, 2 break something (the agent is not told what), 3 investigate (progress, diagnosis, confidence, evidence, live log), 4 preview as a dry run, then approve or reject the exact fix, 5 fix through the Action API, verify, report. **Backup: recorded incident** runs the real agent on telemetry recorded overnight when no live shop is available.
- **What makes it special:** graph RCA ranking for the incident just investigated, the look-alike past incidents from few-shot memory, both LLM roles (investigator and adversarial reviewer) with how code checked them, four live safety proofs (forbidden actions, kill switch, prompt injection in a log, flag data) and four edge cases where the agent refuses to act (reviewer disagrees, LLM blames the wrong service, no safe fix, fix does not work).
- **Results & tests:** replay the 10 recorded incidents, run all self-tests, read the reports and the audit log.

It also starts the Action API if it is not running.

## Quick start (Windows, from this folder `oncall-agent/`)
```powershell
python -m oncall.selftest              # 46 offline tests, ~15 s
python -m oncall.doctor                # checks the live setup and prints the fix for anything missing
python -m oncall.action_api            # window 1: the only door that can change the demo; approval page at http://127.0.0.1:8765/
python -m oncall.run                   # window 2: investigate now (dry-run; asks for approval)
python -m oncall.run --execute         # really apply the fix after a human approves
python -m oncall.run --execute --web-approve   # approve on the web page instead of the terminal
python -m oncall.run --watch --execute # detector: polls every 20 s and starts on its own
python -m oncall.drill run cart_down   # guided live demo: breaks cart, investigates, asks you, fixes, verifies
python -m oncall.drill auto            # or double-click run_all.bat: unattended practice run (~2 h) + scoring
```
Reports go to `incidents/<id>/report.json` and `report.md`. Every proposal, decision and action is logged to `logs/actions.jsonl`, and every run is logged to `docs/RUNLOG.md`.

## LLM configuration (`oncall.env`; never commit it)
Copy `oncall.env.example` to `oncall.env` and fill in a key. Any OpenAI-compatible endpoint works, and if the configured model has been retired the agent picks an available one.
| provider | ONCALL_LLM_BASE_URL | ONCALL_LLM_MODEL |
|---|---|---|
| Groq (free tier) | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5:7b` |
| organisers' key | whatever they give | whatever they give |

Without a key the agent runs in **triage-only** mode, which is how all the results above were produced.

## How it decides (see docs/ARCHITECTURE.md and the study guide)
1. **Rules triage:** container state, OOM, limits lowered since the healthy baseline, resources against the baseline, error rates, services that went silent, where errors originate in traces, calls that never reached the callee, client-vs-server latency gaps, traffic surges, and log bursts. A failing service whose dependency is down is treated as a symptom.
2. **Graph RCA:** 10 minutes of Prometheus history, robust anomaly scores, onset ordering, and a random walk on the learned call graph.
3. **Few-shot memory:** similar past incidents become worked examples for the LLM and a tie-break between classes the evidence supports. It never overrides a fact Docker observed.
4. **Investigator (LLM, optional):** a JSON tool loop (≤10 steps) verifies the suspects. A **grounding validator** drops any claim whose evidence ID or numbers do not exist (and any "evidence" that is an empty query result), a **reviewer** pass tries to disprove the diagnosis, and the LLM's written summary is checked against Docker: if it says a running container is down, the evidence-based explanation replaces it.
5. **Decision:** confidence fuses the LLM with deterministic support. Below 0.55, or when no allowlisted fix exists, the agent **escalates with its findings**.

## Safety controls
- **Dry-run by default** on the command line (`--execute` is needed for real changes); on the demo page, **Preview (dry run)** shows exactly what the Action API would run before anyone approves.
- **Approval:** the exact word `yes` (terminal), a name plus a typed `yes` (Action API approval page), or the **Approve the fix** button on the demo page (it asks to confirm). The approval is bound to one proposal and is single-use.
- **Allowlist:** four verbs, checked by both the client and the server. Demo services only; never flagd.
- **Kill switch:** a file named `KILL` stops the agent and makes the Action API answer `423`.
- **No flag state is ever read.** Flag span events, log lines, services and Prometheus metrics are stripped or refused. Measured on the 10 real recordings: 54,069 flag mentions in the raw telemetry, none in what the agent or the LLM sees (`t_real_recordings_reach_the_agent_flag_free`).
- **The agent is never told the fault.** The alert it receives says only that an alert fired; the demo page's incident ids carry no fault name.

## Known limitations (honest list)
- Without Docker access, an uninstrumented dependency (Valkey, Postgres) can make its caller look guilty (2/10 wrong in that degraded setting).
- The 5 feature-flag faults need a human in the flag UI, so they were not part of the unattended run.
- The local Action API cannot toggle flags, so flag faults are diagnosed and then escalated when a restart does not cure them. Lowered CPU and memory limits have no allowlisted fix and are always escalated.
- Metrics arrive about every 60 s, so detection and verification take 1–4 minutes. Graph RCA needs about 10 minutes of normal history.
- The LLM path is not bit-for-bit repeatable; the deterministic path is.
