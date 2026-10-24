# Incident EVAL-valkey - DIAGNOSED

- **Detected:** 2026-10-24T14:44:20
- **Alert:** eval valkey
- **Root cause:** `valkey-cart` - service_down  (confidence **0.829**)
- **Summary:** valkey-cart: service down. container valkey-cart status=paused exit_code=0.

## Evidence
- `ev-001` (container_health): !! valkey-cart: status=paused health=unhealthy restarts=0 oom=False exit=0 finished_at=0001-01-01T00:00:00Z  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- cart [app_errors] score=0.438
- frontend [app_errors] score=0.3

## Action
- Proposed: restart_service {"service": "valkey-cart"} - valkey-cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:20 - alert received: eval valkey
- 2026-10-24T14:44:20 - triage: 3 suspects, top=valkey-cart/service_down
- 2026-10-24T14:44:20 - diagnosis: valkey-cart [service_down] conf=0.829

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
