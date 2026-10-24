# Incident EVAL-healthy - ESCALATED

- **Detected:** 2026-10-24T01:47:26
- **Alert:** eval healthy
- **Root cause:** `ad` - latency_degradation  (confidence **0.36**)
- **Summary:** ad: latency degradation. ad p95 540.0ms vs baseline 86.7ms (x6.23)

> **Escalated to human:** confidence 0.36 below threshold 0.55

## Evidence
- `ev-003` (service_red): ad p95 540.0ms vs baseline 86.7ms (x6.23)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- cart [latency_degradation] score=0.5

## Action
- Proposed: restart_service {"service": "ad"} - ad diagnosed as latency_degradation; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:26 - alert received: eval healthy
- 2026-10-24T01:47:26 - triage: 2 suspects, top=ad/latency_degradation
- 2026-10-24T01:47:26 - diagnosis: ad [latency_degradation] conf=0.36
- 2026-10-24T01:47:26 - escalated to human: confidence 0.36 below threshold 0.55

## Prevention
- Add a p95 latency SLO alert per service and investigate the slow dependency.
