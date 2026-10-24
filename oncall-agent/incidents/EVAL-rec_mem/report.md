# Incident EVAL-rec_mem - ESCALATED

- **Detected:** 2026-10-24T14:44:17
- **Alert:** eval rec_mem
- **Root cause:** `recommendation` - memory_starved  (confidence **0.891**)
- **Summary:** recommendation: memory starved. recommendation memory limit is 41.9 MB (healthy baseline 524.3 MB) - lowered; it was OOM-killed (exit 137, status restarting); also (service_down) container recommendation status=restarting exit_code=137; also (service_down) recommendation stopped serving: 0.0/s now vs 0.617/s at baseline, and none of its callers went quiet.

> **Escalated to human:** no allowlisted remedy for fault class 'memory_starved'

## Evidence
- `ev-001` (container_health): !! recommendation: status=restarting health=unhealthy restarts=12 oom=True exit=137 finished_at=2026-10-23T19:51:46.092702001Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-003` (service_red): recommendation: rps=0.0 (base 0.617), err=0.0% (base 0.0%), p95=n/a (base 744.0ms)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- frontend [app_errors] score=0.175

## Action
- None proposed

## Timeline
- 2026-10-24T14:44:17 - alert received: eval rec_mem
- 2026-10-24T14:44:17 - triage: 2 suspects, top=recommendation/memory_starved
- 2026-10-24T14:44:18 - diagnosis: recommendation [memory_starved] conf=0.891
- 2026-10-24T14:44:18 - escalated to human: no allowlisted remedy for fault class 'memory_starved'

## Prevention
- Restore the memory limit from config; review limit changes in config review; alert on OOM kills and on limit changes.
