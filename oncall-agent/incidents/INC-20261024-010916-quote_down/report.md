# Incident INC-20261024-010916-quote_down - DIAGNOSED

- **Detected:** 2026-10-24T01:09:16
- **Alert:** practice incident 'quote_down' (alert: on-call page)
- **Root cause:** `quote` - service_down  (confidence **0.88**)
- **Summary:** quote: service down. container quote status=exited exit_code=0 (also dependency_unreachable) 20 failed calls from shipping to quote never reached a quote server span

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container quote status=exited exit_code=0; (also dependency_unreachable) 20 failed calls from shipping to quote never reached a quote server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-009` (find_error_traces): container quote status=exited exit_code=0; (also dependency_unreachable) 20 failed calls from shipping to quote never reached a quote server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-23T19%3A29%3A40Z&query.start_time_max=2026-10-23T19%3`

## Alternatives considered
- shipping [app_errors] score=0.85
- image-provider [app_errors] score=0.5
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "quote"} - quote diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792784381.1804523, "action": "restart_service", "params": {"service": "quote"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker start quote", "current_state": "exited"}}

## Timeline
- 2026-10-24T01:09:16 - alert received: practice incident 'quote_down' (alert: on-call page)
- 2026-10-24T01:09:40 - triage: 4 suspects, top=quote/service_down
- 2026-10-24T01:09:40 - diagnosis: quote [service_down] conf=0.88
- 2026-10-24T01:09:40 - human approval: APPROVED
- 2026-10-24T01:09:41 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
