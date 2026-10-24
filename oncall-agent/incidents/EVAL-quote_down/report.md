# Incident EVAL-quote_down - DIAGNOSED

- **Detected:** 2026-10-24T01:47:27
- **Alert:** eval quote_down
- **Root cause:** `quote` - service_down  (confidence **0.81**)
- **Summary:** quote: service down. container quote status=exited exit_code=0

## Evidence
- `ev-001` (container_health): container quote status=exited exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- image-provider [app_errors] score=0.5
- frontend [app_errors] score=0.35
- shipping [latency_degradation] score=0.25

## Action
- Proposed: restart_service {"service": "quote"} - quote diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:27 - alert received: eval quote_down
- 2026-10-24T01:47:28 - triage: 4 suspects, top=quote/service_down
- 2026-10-24T01:47:28 - diagnosis: quote [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
