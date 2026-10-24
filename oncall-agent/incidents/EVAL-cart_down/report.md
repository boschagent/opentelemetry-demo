# Incident EVAL-cart_down - DIAGNOSED

- **Detected:** 2026-10-24T01:47:21
- **Alert:** eval cart_down
- **Root cause:** `cart` - service_down  (confidence **0.81**)
- **Summary:** cart: service down. container cart status=exited exit_code=137

## Evidence
- `ev-001` (container_health): container cart status=exited exit_code=137  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- ad [latency_degradation] score=0.5
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:21 - alert received: eval cart_down
- 2026-10-24T01:47:21 - triage: 3 suspects, top=cart/service_down
- 2026-10-24T01:47:21 - diagnosis: cart [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
