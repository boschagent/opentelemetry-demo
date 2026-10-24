# Incident EVAL-combo_cart_ad - DIAGNOSED

- **Detected:** 2026-10-24T01:47:23
- **Alert:** eval combo_cart_ad
- **Root cause:** `cart` - service_down  (confidence **0.81**)
- **Summary:** cart: service down. container cart status=exited exit_code=0

## Evidence
- `ev-001` (container_health): container cart status=exited exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:23 - alert received: eval combo_cart_ad
- 2026-10-24T01:47:24 - triage: 2 suspects, top=cart/service_down
- 2026-10-24T01:47:24 - diagnosis: cart [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
