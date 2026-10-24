# Incident EVAL-payment_paused - DIAGNOSED

- **Detected:** 2026-10-24T14:44:14
- **Alert:** eval payment_paused
- **Root cause:** `payment` - service_down  (confidence **0.81**)
- **Summary:** payment: service down. container payment status=paused exit_code=0.

## Evidence
- `ev-001` (container_health): !! payment: status=paused health=unhealthy restarts=0 oom=False exit=0 finished_at=0001-01-01T00:00:00Z  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- product-catalog [service_down] score=0.6
- ad [latency_degradation] score=0.5
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "payment"} - payment diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-24T14:44:14 - alert received: eval payment_paused
- 2026-10-24T14:44:14 - triage: 4 suspects, top=payment/service_down
- 2026-10-24T14:44:14 - diagnosis: payment [service_down] conf=0.81

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
