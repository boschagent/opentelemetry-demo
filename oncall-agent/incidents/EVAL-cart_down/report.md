# Incident EVAL-cart_down - DIAGNOSED

- **Detected:** 2026-10-24T14:44:03
- **Alert:** eval cart_down
- **Root cause:** `cart` - service_down  (confidence **0.829**)
- **Summary:** cart: service down. container cart status=exited exit_code=0.

## Evidence
- `ev-001` (container_health): !! cart: status=exited health=None restarts=0 oom=False exit=0 finished_at=2026-10-24T04:38:44.064447074Z  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:03 - alert received: eval cart_down
- 2026-10-24T14:44:04 - triage: 2 suspects, top=cart/service_down
- 2026-10-24T14:44:04 - diagnosis: cart [service_down] conf=0.829

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
