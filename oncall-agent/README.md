# Incident Zero: AI On-Call Engineer (team boschagent)

An agent that receives an alert from the OpenTelemetry Astronomy Shop and works through the first hour of on-call. It investigates with real tools (Prometheus metrics, Jaeger traces, container logs and health, change history) and names the root cause with grounded evidence and a confidence score. It then proposes an **allowlisted** fix, waits for an explicit human **"yes"**, applies the fix **only through the Action API**, verifies recovery, rolls back or escalates if needed, and writes an incident report.

> Python 3.11+, **standard library only**: no `pip install` is needed for the agent.

## Quick start (Windows PowerShell, from this folder `oncall-agent/`)
```powershell
# 0) the demo must be running (from the repo root, one level up)
#    full:    docker compose up -d
#    minimal: docker compose -f compose.yaml -f compose.observability.yaml up -d   (8 GB laptops)

# 1) prove the code does what it should (offline, ~5 s)
python -m oncall.selftest

# 2) start the Action API (the ONLY thing allowed to change the demo); keep this window open
python -m oncall.action_api

# 3) in a 2nd window: investigate now (dry-run, asks for approval)
python -m oncall.run --no-llm            # deterministic triage only
python -m oncall.run                     # with the LLM (configure oncall.env first)
python -m oncall.run --execute           # really apply the fix after you type 'yes'
python -m oncall.run --watch --execute   # detector: polls every 20 s, starts on its own
```
Reports are written to `incidents/<id>/report.json` and `report.md`. Every action is logged to `logs/actions.jsonl`, and every run is logged to `docs/RUNLOG.md`.

## LLM configuration (`oncall.env`; never commit it)
Copy `oncall.env.example` to `oncall.env` and fill in a key. Any OpenAI-compatible endpoint works:
| provider | ONCALL_LLM_BASE_URL | ONCALL_LLM_MODEL (check the console if renamed) |
|---|---|---|
| Groq (free tier, fast) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Google Gemini (free tier) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5:7b` |
| organisers' key | whatever they give | whatever they give |

Without a key the agent still runs in **triage-only** mode.

## How the approval gate works
- The default is **dry-run**: the Action API receives `dry_run: true` and returns what it *would* do.
- `--execute` is required for real changes, and even then the agent prints the diagnosis, evidence and plan, and acts only on the exact input `yes`. Anything else rejects.
- The Action API independently refuses real calls that lack an approval header, actions outside the allowlist (`restart_service`, `throttle_loadgen`, `toggle_flag`, `scale_service`) and non-allowlisted services (e.g. `flagd`).

## How the kill switch works
Create a file named `KILL` in this folder (`New-Item KILL`). The agent stops before its next step or action, and the Action API answers `423 Locked` to every request. Delete the file to re-enable. Ctrl-C also stops the agent immediately.

## How it decides (see docs/ARCHITECTURE.md)
1. **Triage (deterministic):** container state, restarts, OOM, CPU/memory against limits, per-service RED metrics against a baseline, *where errors originate* in traces (the deepest failing span), *calls that never reached the callee*, and the *client-vs-server latency gap* per edge (network faults). Output: ranked suspects with evidence IDs.
2. **Investigator (LLM, optional):** a JSON tool-calling loop (≤10 steps) that verifies the suspects with targeted tool calls.
3. **Grounding validator:** every cited evidence ID must exist, and every number quoted must appear in that evidence. Invented evidence is dropped.
4. **Reviewer agent:** a second LLM pass tries to disprove the diagnosis.
5. **Decision:** confidence = LLM confidence fused with deterministic support, lowered by reviewer disagreement or rejected evidence. Below `ONCALL_CONFIDENCE_THRESHOLD` (default 0.55) the agent **escalates with its findings** instead of acting.

## Rules we enforce in code
- **No feature-flag state is ever read.** `core.redact()` strips `feature_flag.*` span events and tags, flag log lines, and the flagd/flagd-ui services from every tool result before it reaches the ledger or the LLM. This is tested in `selftest`.
- The agent changes the system only through the Action API, and only after approval.

## Repeatable evaluation
```powershell
python -m oncall.run --mode record --bundle eval/bundles/<name> --no-act --no-llm   # during a live incident
python eval/run_eval.py                                                          # replays all bundles offline
```

## Known limitations (honest list, kept up to date)
- Thresholds were tuned on scripted scenarios and our own practice runs, not on the hidden incidents.
- The local Action API cannot toggle feature flags (we never touch flagd ourselves). App faults caused by flags are fixed by restart only if that helps; otherwise the agent escalates after verification fails.
- `cpu_starved` (a lowered CPU limit) has no allowlisted fix, so it is always escalated.
- Network faults are detected only on edges that appear in sampled traces.
- Metrics arrive about every 60 s, so detection and verification take 1–4 minutes.
- In replay mode the LLM path is not bit-for-bit repeatable (the model may pick different tools); triage-only replay is.
