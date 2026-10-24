# Incident EVAL-combo_cart_ad - DIAGNOSED

- **Detected:** 2026-10-24T14:44:07
- **Alert:** eval combo_cart_ad
- **Root cause:** `cart` - service_down  (confidence **0.864**)
- **Summary:** cart: service down. container cart status=exited exit_code=0; cart stopped serving: 0.0/s now vs 1.259/s at baseline, and none of its callers went quiet.

## Evidence
- `ev-001` (container_health): !! cart: status=exited health=None restarts=0 oom=False exit=0 finished_at=2026-10-23T20:12:50.017072604Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-003` (service_red): cart: rps=0.0 (base 1.259), err=0.0% (base 0.0%), p95=n/a (base 46.6ms)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- ad [cpu_starved] score=0.6
- shipping [service_down] score=0.6
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:07 - alert received: eval combo_cart_ad
- 2026-10-24T14:44:08 - triage: 4 suspects, top=cart/service_down
- 2026-10-24T14:44:08 - diagnosis: cart [service_down] conf=0.864

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
