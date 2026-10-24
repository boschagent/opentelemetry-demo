# Incident EVAL-catalog_down - DIAGNOSED

- **Detected:** 2026-10-24T14:44:06
- **Alert:** eval catalog_down
- **Root cause:** `product-catalog` - service_down  (confidence **0.878**)
- **Summary:** product-catalog: service down. container product-catalog status=exited exit_code=137; product-catalog stopped serving: 0.0/s now vs 0.467/s at baseline, and none of its callers went quiet.

## Evidence
- `ev-001` (container_health): !! product-catalog: status=exited health=unhealthy restarts=2 oom=False exit=137 finished_at=2026-10-23T19:08:52.594975948Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-003` (service_red): product-catalog: rps=0.0 (base 0.467), err=0.0% (base 0.0%), p95=n/a (base 15000.0ms)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- recommendation [app_errors] score=0.25
- frontend [app_errors] score=0.15

## Action
- Proposed: restart_service {"service": "product-catalog"} - product-catalog diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:06 - alert received: eval catalog_down
- 2026-10-24T14:44:07 - triage: 3 suspects, top=product-catalog/service_down
- 2026-10-24T14:44:07 - diagnosis: product-catalog [service_down] conf=0.878

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
