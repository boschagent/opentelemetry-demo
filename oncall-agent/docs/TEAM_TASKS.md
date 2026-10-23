# Who does what (read your own section only)

Repo: https://github.com/boschagent/opentelemetry-demo. Our agent lives in the folder **`oncall-agent/`**.

## Everyone: one-time setup (10 min)
1. `git clone https://github.com/boschagent/opentelemetry-demo.git` (skip this if you already have it), then `cd opentelemetry-demo\oncall-agent`
2. `python -m oncall.selftest`. It must end with **ALL TESTS PASSED**. If it doesn't, screenshot it and send it to P1.
3. Read `README.md` and `docs/ARCHITECTURE.md` (5 min). Then read **your own file(s)** below line by line. In the 1-to-1 interview you must be able to explain them.

## Everyone: how to commit (copy exactly)
```powershell
git pull --rebase
# ...edit ONLY your own files...
python -m oncall.selftest          # must say ALL TESTS PASSED
git add <your files>
git commit -m "<what you changed>"
git push
```
If `git push` says "rejected", run `git pull --rebase`, then `git push` again. Never edit someone else's file without telling them.

## Honesty rule (this protects our score)
The first version of the code was generated with Claude (AI coding assistant), and **TEAM_FORM.md says so**. Rule 2 allows AI assistants *if declared*. Rule 7 says hiding things loses points. Your job is to **own** your module: run it live, verify it, tune it, fix it, add tests, and commit those changes yourself. Don't say you wrote what you didn't. Say "Claude drafted it, and I verified it live, fixed X and tuned Y". Judges respect that.

---

## P1: Lead / Integrator. Files: `oncall/run.py`, `oncall/brain.py`, `oncall/core.py`
- Commit the initial code (done by P1, today). Run the live demo flow for every checkpoint.
- Configure the LLM (`oncall.env`) and compare `--no-llm` against the LLM mode on 3 incidents.
- **Explain in the interview:** why triage comes first and then the LLM, how the evidence ledger and validator stop hallucinated evidence, and how confidence is fused and when we escalate.

## P2: Metrics & Traces. Files: `oncall/tools/metrics.py`, `oncall/tools/traces.py`
- On the demo laptop, open http://localhost:9090 and run `traces_span_metrics_calls_total`. Check that the labels `service_name`, `span_kind` and `status_code` exist. **If the names differ, fix `metrics.py`.**
- Run `python -c "from oncall.core import *; from oncall.tools import metrics; print(metrics.service_red(Backend(), Config())['summary'])"`. The numbers must look sane.
- During a payment-failure incident, check that `find_error_traces` says the errors originate in payment.
- **Explain:** the RED metrics, the baseline offset, "deepest failing span" = origin, and client-minus-server gap = network.

## P3: Infra, Logs & Sanitiser. Files: `oncall/tools/infra.py`, the `redact` part of `oncall/core.py`
- Run `python -c "from oncall.core import *; from oncall.tools import infra; print(infra.container_health(Backend(), Config())['summary']); print(infra.container_stats(Backend(), Config())['summary'])"`
- Check `get_logs` on cart, payment and ad during incidents. Check that **no flag names** ever appear (the ad service logs them, and they must be redacted).
- **Explain:** why we redact flags (disqualification rule), and how stopped, paused, OOM-killed and CPU-starved containers are detected.

## P4: Hands & Safety. Files: `oncall/act.py`, `oncall/action_api.py`
- Run `python -m oncall.action_api` in one window. Then stop the cart (`python practice/inject.py stop cart`) and run `python -m oncall.run --no-llm --execute`. Type `yes` and watch it restart cart and verify recovery.
- Test the kill switch live: `New-Item KILL`, then run the agent. It must abort. Delete KILL afterwards.
- Stretch: a simple approval web UI (Streamlit/Flask) that calls the same functions.
- **Explain:** the allowlist, the approval header, dry-run, the kill switch (checked both client- and server-side), verify, and rollback or escalate.

## P5: Eval, Data & Docs. Files: `eval/`, `practice/inject.py`, `docs/EVAL_NOTE.md`, `TEAM_FORM.md`
- For each practice incident: inject it (flag UI or `practice/inject.py`), wait 2–3 min, then run
  `python -m oncall.run --mode record --bundle eval/bundles/<name> --no-act --no-llm`.
  Write the TRUE answer into `eval/labels.json`, then run `python practice/inject.py reset` and reset the flags.
- Run `python eval/run_eval.py` and copy the scoreboard into `docs/EVAL_NOTE.md` with a short "why it failed" note per miss.
- Fill in TEAM_FORM.md. Zip `incidents/` and `eval/` to Google Drive every hour (backup).
- **Explain:** how the evaluation is repeatable (record/replay), what our accuracy is, and where we fail.

---

## Copy-paste prompt for your free ChatGPT / Claude (replace the <...>)
```
I'm on a hackathon team building an AI on-call agent for the OpenTelemetry Demo (Astronomy Shop).
Our repo has a Python package oncall/ (stdlib only, Python 3.11+). I own the file(s): <your files>.
Hard rules: the agent must never read feature-flag settings or flag telemetry; it changes the system only
through our Action API after a human types "yes"; evidence must come from real telemetry.
Here is my file:
<paste the whole file>
Here is real output from our running demo:
<paste the command output / error>
Task: <e.g. "the label is called span.kind not span_kind - fix the query"> 
Give me the COMPLETE updated file, keep the same function names and return format
({"ok","summary","data","source"}), and tell me which line(s) changed and why.
After I apply it I will run `python -m oncall.selftest` - tell me if any test needs updating and how.
```
