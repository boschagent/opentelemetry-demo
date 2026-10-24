# Incident EVAL-ad_starved - ESCALATED

- **Detected:** 2026-10-24T01:47:20
- **Alert:** eval ad_starved
- **Root cause:** `ad` - cpu_starved  (confidence **0.833**)
- **Summary:** ad: cpu starved. ad pinned at 5.65% CPU against a 0.05-core limit (also latency_degradation) ad p95 2150.0ms vs baseline 57.5ms (x37.39) (also latency_degradation) graph RCA: ad ranks #1 (share 100%); own anomaly p95 20.0->2675.0 (z=335.8)

> **Escalated to human:** no allowlisted remedy for fault class 'cpu_starved'

## Evidence
- `ev-002` (container_stats): ad pinned at 5.65% CPU against a 0.05-core limit; (also latency_degradation) ad p95 2150.0ms vs baseline 57.5ms (x37.39); (also latency_degradation) graph RCA: ad ranks #1 (share 100%); own anomaly p95 20.0->2675.0 (z=335.8)  
  source: `docker stats --no-stream`
- `ev-003` (service_red): ad pinned at 5.65% CPU against a 0.05-core limit; (also latency_degradation) ad p95 2150.0ms vs baseline 57.5ms (x37.39); (also latency_degradation) graph RCA: ad ranks #1 (share 100%); own anomaly p95 20.0->2675.0 (z=335.8)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`
- `ev-010` (graph_rca): ad pinned at 5.65% CPU against a 0.05-core limit; (also latency_degradation) ad p95 2150.0ms vs baseline 57.5ms (x37.39); (also latency_degradation) graph RCA: ad ranks #1 (share 100%); own anomaly p95 20.0->2675.0 (z=335.8)  
  source: `rca.analyze over ev-008 + ev-009 on the merged call graph`

## Alternatives considered
- frontend [app_errors] score=0.35

## Action
- None proposed

## Timeline
- 2026-10-24T01:47:20 - alert received: eval ad_starved
- 2026-10-24T01:47:20 - triage: 2 suspects, top=ad/cpu_starved
- 2026-10-24T01:47:21 - diagnosis: ad [cpu_starved] conf=0.833
- 2026-10-24T01:47:21 - escalated to human: no allowlisted remedy for fault class 'cpu_starved'

## Prevention
- Review resource limits (CPU quota was too low); alert on CPU throttling; capacity-plan limits in config review.
