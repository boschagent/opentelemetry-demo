# Incident INC-20261024-004255-catalog_down - DIAGNOSED

- **Detected:** 2026-10-24T00:42:55
- **Alert:** practice incident 'catalog_down' (alert: on-call page)
- **Root cause:** `product-catalog` - service_down  (confidence **0.895**)
- **Summary:** product-catalog: service down. container product-catalog status=exited exit_code=137 (also dependency_unreachable) 33 failed calls from frontend to product-catalog never reached a product-catalog server span (also dependency_unreachable) 20 failed calls from recommendation to product-catalog never reached a product-catalog server span

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container product-catalog status=exited exit_code=137; (also dependency_unreachable) 33 failed calls from frontend to product-catalog never reached a product-catalog server span; (also dependency_unreachable) 20 failed calls from recommendation to product-catalog never reached a product-catalog server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-009` (find_error_traces): container product-catalog status=exited exit_code=137; (also dependency_unreachable) 33 failed calls from frontend to product-catalog never reached a product-catalog server span; (also dependency_unreachable) 20 failed calls from recommendation to product-catalog never reached a product-catalog server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-23T19%3A03%3A43Z&query.start_time_max=2026-10-23T19%3`

## Alternatives considered
- recommendation [app_errors] score=0.8
- checkout [app_errors] score=0.7
- frontend [app_errors] score=0.3

## Action
- Proposed: restart_service {"service": "product-catalog"} - product-catalog diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792782827.373834, "action": "restart_service", "params": {"service": "product-catalog"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker start product-catalog", "current_state": "exited"}}

## Timeline
- 2026-10-24T00:42:55 - alert received: practice incident 'catalog_down' (alert: on-call page)
- 2026-10-24T00:43:44 - triage: 4 suspects, top=product-catalog/service_down
- 2026-10-24T00:43:45 - diagnosis: product-catalog [service_down] conf=0.895
- 2026-10-24T00:43:45 - human approval: APPROVED
- 2026-10-24T00:43:47 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
