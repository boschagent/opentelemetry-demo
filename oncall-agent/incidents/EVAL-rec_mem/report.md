# Incident EVAL-rec_mem - DIAGNOSED

- **Detected:** 2026-10-24T01:47:29
- **Alert:** eval rec_mem
- **Root cause:** `recommendation` - service_down  (confidence **0.81**)
- **Summary:** recommendation: service down. container recommendation status=restarting exit_code=137

## Evidence
- `ev-001` (container_health): container recommendation status=restarting exit_code=137  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- product-catalog [service_down] score=0.641
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "recommendation"} - recommendation diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:29 - alert received: eval rec_mem
- 2026-10-24T01:47:29 - triage: 3 suspects, top=recommendation/service_down
- 2026-10-24T01:47:29 - diagnosis: recommendation [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
