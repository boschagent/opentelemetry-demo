# Incident EVAL-db_down - DIAGNOSED

- **Detected:** 2026-10-24T14:44:09
- **Alert:** eval db_down
- **Root cause:** `astronomy-db` - service_down  (confidence **0.683**)
- **Summary:** astronomy-db: service down. container astronomy-db status=exited exit_code=0.

## Evidence
- `ev-001` (container_health): !! astronomy-db: status=exited health=unhealthy restarts=0 oom=True exit=0 finished_at=2026-10-23T19:22:33.883485549Z  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- recommendation [service_down] score=0.8
- currency [service_down] score=0.6
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "astronomy-db"} - astronomy-db diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:09 - alert received: eval db_down
- 2026-10-24T14:44:11 - triage: 4 suspects, top=astronomy-db/service_down
- 2026-10-24T14:44:11 - diagnosis: astronomy-db [service_down] conf=0.683

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
