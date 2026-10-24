# Incident INC-20261024-012209-rec_mem - DIAGNOSED

- **Detected:** 2026-10-24T01:22:09
- **Alert:** practice incident 'rec_mem' (alert: on-call page)
- **Root cause:** `recommendation` - service_down  (confidence **0.877**)
- **Summary:** recommendation: service down. container recommendation status=restarting exit_code=137 (also dependency_unreachable) 12 failed calls from frontend to recommendation never reached a recommendation server span

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container recommendation status=restarting exit_code=137; (also dependency_unreachable) 12 failed calls from frontend to recommendation never reached a recommendation server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-006` (find_error_traces): container recommendation status=restarting exit_code=137; (also dependency_unreachable) 12 failed calls from frontend to recommendation never reached a recommendation server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-23T19%3A42%3A28Z&query.start_time_max=2026-10-23T19%3`

## Alternatives considered
- product-catalog [service_down] score=0.641
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "recommendation"} - recommendation diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792785151.8020613, "action": "restart_service", "params": {"service": "recommendation"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker restart recommendation", "current_state": "restarting"}}

## Timeline
- 2026-10-24T01:22:09 - alert received: practice incident 'rec_mem' (alert: on-call page)
- 2026-10-24T01:22:30 - triage: 3 suspects, top=recommendation/service_down
- 2026-10-24T01:22:30 - diagnosis: recommendation [service_down] conf=0.877
- 2026-10-24T01:22:30 - human approval: APPROVED
- 2026-10-24T01:22:31 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
