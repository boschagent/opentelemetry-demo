# Incident INC-20261024-122049-ad_starved - ESCALATED

- **Detected:** 2026-10-24T12:20:49
- **Alert:** on-call page (ad_starved)
- **Root cause:** `ad` - latency_degradation  (confidence **0.468**)
- **Summary:** The ad service latency has risen sharply (p95 362 ms vs 22 ms baseline) while its CPU and memory usage are well within limits and no error traces are reported, indicating a latency degradation internal to the ad service rather than resource starvation or downstream failures.

> **Escalated to human:** confidence 0.47 below threshold 0.55

## Evidence
- `ev-014` (service_red): ad: p95=362.0ms (base 22.0ms) err=0.0% (base 0.0%)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`
- `ev-011` (container_stats): ad: cpu=0.75% mem=83.29% (249.9MiB / 300MiB) – not CPU‑starved  
  source: `docker stats --no-stream`
- `ev-015` (find_error_traces): 0 error traces for 'ad' in last 10m.  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=ad&query.start_time_min=2026-10-24T06%3A42%3A45Z&query.start_time_max=2026-10-24T06%3A52%3A`

## Alternatives considered
- frontend-proxy latency_degradation (p95 up to 221 ms)
- product-catalog latency_degradation (p95 up to 552 ms)

## Reviewer agent
- verdict: **disagree** - While ad latency spikes, its CPU and memory are healthy and no errors appear; however, product-catalog shows a larger latency increase (p95 552 ms vs 150 ms baseline) and could be a downstream dependency causing ad slowdown.

## Action
- Proposed: restart_service {"service": "ad"} - ad diagnosed as latency_degradation; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T12:20:49 - alert received: on-call page (ad_starved)
- 2026-10-24T12:21:05 - triage: 3 suspects, top=ad/cpu_starved
- 2026-10-24T12:23:38 - LLM investigation finished (8 steps)
- 2026-10-24T12:24:04 - reviewer verdict: disagree
- 2026-10-24T12:24:04 - diagnosis: ad [latency_degradation] conf=0.468
- 2026-10-24T12:24:04 - escalated to human: confidence 0.47 below threshold 0.55

## Prevention
- Add a p95 latency SLO alert per service and investigate the slow dependency.
