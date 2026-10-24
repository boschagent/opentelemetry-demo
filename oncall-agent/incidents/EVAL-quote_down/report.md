# Incident EVAL-quote_down - DIAGNOSED

- **Detected:** 2026-10-24T14:44:15
- **Alert:** eval quote_down
- **Root cause:** `quote` - service_down  (confidence **0.878**)
- **Summary:** quote: service down. container quote status=exited exit_code=0; quote stopped serving: 0.0/s now vs 0.125/s at baseline, and none of its callers went quiet.

## Evidence
- `ev-001` (container_health): !! quote: status=exited health=unhealthy restarts=0 oom=False exit=0 finished_at=2026-10-23T19:35:14.265840354Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-003` (service_red): quote: rps=0.0 (base 0.125), err=0.0% (base 0.0%), p95=n/a (base 20.0ms)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- image-provider [app_errors] score=0.5
- frontend [app_errors] score=0.35
- shipping [latency_degradation] score=0.25

## Action
- Proposed: restart_service {"service": "quote"} - quote diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:15 - alert received: eval quote_down
- 2026-10-24T14:44:16 - triage: 4 suspects, top=quote/service_down
- 2026-10-24T14:44:16 - diagnosis: quote [service_down] conf=0.878

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
