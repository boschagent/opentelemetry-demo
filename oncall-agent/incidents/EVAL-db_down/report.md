# Incident EVAL-db_down - DIAGNOSED

- **Detected:** 2026-10-24T01:47:25
- **Alert:** eval db_down
- **Root cause:** `astronomy-db` - service_down  (confidence **0.81**)
- **Summary:** astronomy-db: service down. container astronomy-db status=exited exit_code=0

## Evidence
- `ev-001` (container_health): container astronomy-db status=exited exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- currency [service_down] score=0.593
- recommendation [latency_degradation] score=0.5
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "astronomy-db"} - astronomy-db diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:25 - alert received: eval db_down
- 2026-10-24T01:47:26 - triage: 4 suspects, top=astronomy-db/service_down
- 2026-10-24T01:47:26 - diagnosis: astronomy-db [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
