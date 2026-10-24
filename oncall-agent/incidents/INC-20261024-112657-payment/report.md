# Incident INC-20261024-112657-payment - ESCALATED

- **Detected:** 2026-10-24T11:26:57
- **Alert:** control room: incident 'payment'
- **Root cause:** `product-catalog` - latency_degradation  (confidence **0.36**)
- **Summary:** product-catalog: latency degradation. product-catalog p95 916.9ms vs baseline 330.5ms (x2.77)

> **Escalated to human:** confidence 0.36 below threshold 0.55

## Evidence
- `ev-003` (service_red): product-catalog p95 916.9ms vs baseline 330.5ms (x2.77)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- recommendation [latency_degradation] score=0.5
- checkout [app_errors] score=0.25

## Action
- Proposed: restart_service {"service": "product-catalog"} - product-catalog diagnosed as latency_degradation; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T11:26:57 - alert received: control room: incident 'payment'
- 2026-10-24T11:27:34 - triage: 3 suspects, top=product-catalog/latency_degradation
- 2026-10-24T11:27:34 - LLM investigation finished (1 steps)
- 2026-10-24T11:27:34 - diagnosis: product-catalog [latency_degradation] conf=0.36
- 2026-10-24T11:27:34 - escalated to human: confidence 0.36 below threshold 0.55

## Prevention
- Add a p95 latency SLO alert per service and investigate the slow dependency.
