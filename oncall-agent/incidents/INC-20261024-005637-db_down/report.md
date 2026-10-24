# Incident INC-20261024-005637-db_down - DIAGNOSED

- **Detected:** 2026-10-24T00:56:37
- **Alert:** practice incident 'db_down' (alert: on-call page)
- **Root cause:** `astronomy-db` - service_down  (confidence **0.648**)
- **Summary:** astronomy-db: service down. container astronomy-db status=exited exit_code=0

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container astronomy-db status=exited exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- product-catalog [service_down] score=0.857
- cart [latency_degradation] score=0.6
- recommendation [latency_degradation] score=0.5

## Action
- Proposed: restart_service {"service": "astronomy-db"} - astronomy-db diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792783612.3908553, "action": "restart_service", "params": {"service": "astronomy-db"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker start astronomy-db", "current_state": "exited"}}

## Timeline
- 2026-10-24T00:56:37 - alert received: practice incident 'db_down' (alert: on-call page)
- 2026-10-24T00:56:51 - triage: 5 suspects, top=astronomy-db/service_down
- 2026-10-24T00:56:51 - diagnosis: astronomy-db [service_down] conf=0.648
- 2026-10-24T00:56:51 - human approval: APPROVED
- 2026-10-24T00:56:52 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
