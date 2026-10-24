# Incident EVAL-ad_starved - ESCALATED

- **Detected:** 2026-10-24T14:44:02
- **Alert:** eval ad_starved
- **Root cause:** `ad` - cpu_starved  (confidence **0.873**)
- **Summary:** ad: cpu starved. ad CPU limit is 0.05 cores (healthy baseline: no limit) - lowered; ad pinned at 5.65% CPU against a 0.05-core limit; also (latency_degradation) ad p95 2150.0ms vs baseline 57.5ms (x37.39).

> **Escalated to human:** no allowlisted remedy for fault class 'cpu_starved'

## Evidence
- `ev-001` (container_health): ad: status=running health=healthy cpu_limit_cores=0.05 mem_limit_mb=314.6  
  source: `docker ps -a; docker inspect <all>`
- `ev-002` (container_stats): load-generator: cpu=207.97% mem=49.62% (744.3MiB / 1.465GiB); ad: cpu=5.65% mem=93.42% (280.3MiB / 300MiB)  
  source: `docker stats --no-stream`
- `ev-003` (service_red): ad: rps=0.317 (base 0.283), err=0.0% (base 0.0%), p95=2150.0ms (base 57.5ms)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`
- `ev-010` (graph_rca): ranking: ad 100%; root: ad [latency_degradation] - graph RCA: ad ranks #1 (share 100%); evidence: p95 latency 20.0 -> 2675.0 (robust z=335.8)  
  source: `rca.analyze over ev-008 + ev-009 on the merged call graph`

## Alternatives considered
- frontend [app_errors] score=0.35

## Action
- None proposed

## Timeline
- 2026-10-24T14:44:02 - alert received: eval ad_starved
- 2026-10-24T14:44:03 - triage: 2 suspects, top=ad/cpu_starved
- 2026-10-24T14:44:03 - diagnosis: ad [cpu_starved] conf=0.873
- 2026-10-24T14:44:03 - escalated to human: no allowlisted remedy for fault class 'cpu_starved'

## Prevention
- Review resource limits (CPU quota was too low); alert on CPU throttling; capacity-plan limits in config review.
