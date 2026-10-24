# Incident EVAL-payment_paused - DIAGNOSED

- **Detected:** 2026-10-24T01:47:27
- **Alert:** eval payment_paused
- **Root cause:** `payment` - service_down  (confidence **0.81**)
- **Summary:** payment: service down. container payment status=paused exit_code=0

## Evidence
- `ev-001` (container_health): container payment status=paused exit_code=0  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- ad [latency_degradation] score=0.5
- recommendation [memory_pressure] score=0.473
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "payment"} - payment diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T01:47:27 - alert received: eval payment_paused
- 2026-10-24T01:47:27 - triage: 4 suspects, top=payment/service_down
- 2026-10-24T01:47:27 - diagnosis: payment [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
