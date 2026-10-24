# Incident EVAL-catalog_down - DIAGNOSED

- **Detected:** 2026-10-24T01:47:22
- **Alert:** eval catalog_down
- **Root cause:** `product-catalog` - service_down  (confidence **0.81**)
- **Summary:** product-catalog: service down. container product-catalog status=exited exit_code=137

## Evidence
- `ev-001` (container_health): container product-catalog status=exited exit_code=137  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- recommendation [app_errors] score=0.25
- frontend [app_errors] score=0.15

## Action
- Proposed: restart_service {"service": "product-catalog"} - product-catalog diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:22 - alert received: eval catalog_down
- 2026-10-24T01:47:22 - triage: 3 suspects, top=product-catalog/service_down
- 2026-10-24T01:47:23 - diagnosis: product-catalog [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
