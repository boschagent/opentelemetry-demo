# Experiments on 10 real recorded incidents (24 Oct 2026, overnight run)

All numbers: deterministic agent, **no LLM**, replaying the exact telemetry recorded on the laptop (`python eval/run_eval.py`).
Outcome scoring follows the brief: correct > honest escalation > wrong. Escalating a correctly diagnosed fault that has no
allowlisted fix (lowered CPU / memory limit) counts as correct.

## 1. Main result

10/10 correct, 0 wrong (see eval/SCOREBOARD.md). Leave-one-out fault-class accuracy 9/9 with and without few-shot memory.

## 2. Ablation x robustness matrix

Rows: which data source we pretend is gone. Columns: which part we switch off.

| data source removed | full agent | no graph RCA | no learned graph | no few-shot |
|---|---|---|---|---|
| none | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 |
| jaeger | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 |
| prom | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 | 10 / 0 / 0 |
| docker | 5 / 3 / 2 | 4 / 4 / 2 | 5 / 3 / 2 | 2 / 6 / 2 |
| docker,jaeger | 5 / 3 / 2 | 4 / 4 / 2 | 5 / 3 / 2 | 2 / 6 / 2 |

Cells: correct / escalated / wrong out of 10.

## 3. What we conclude (honestly)

- With all data sources, Docker's own container state decides these 10 infrastructure faults; graph RCA and few-shot add nothing measurable here.
- Losing Jaeger or Prometheus alone costs nothing: the agent has redundant evidence.
- Without Docker access (e.g. a remote or Kubernetes setup) the problem gets hard: the full agent still gets 5/10 right and escalates 3. Few-shot memory is worth +3 correct and graph RCA +1 in that setting.
- The 2 remaining wrong answers without Docker are uninstrumented dependencies (Valkey, Postgres) whose callers look guilty. That is our main known limitation.
- Not yet measured on real data: the 5 feature-flag incidents (application faults), where we expect graph RCA to matter most; they need a human in the flag UI.
