# Incident EVAL-valkey - DIAGNOSED

- **Detected:** 2026-10-24T01:47:30
- **Alert:** eval valkey
- **Root cause:** `valkey-cart` - service_down  (confidence **0.81**)
- **Summary:** valkey-cart: service down. container valkey-cart status=paused exit_code=0

## Evidence
- `ev-001` (container_health): container valkey-cart status=paused exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- cart [app_errors] score=0.438
- frontend [app_errors] score=0.3

## Action
- Proposed: restart_service {"service": "valkey-cart"} - valkey-cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:30 - alert received: eval valkey
- 2026-10-24T01:47:30 - triage: 3 suspects, top=valkey-cart/service_down
- 2026-10-24T01:47:30 - diagnosis: valkey-cart [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
