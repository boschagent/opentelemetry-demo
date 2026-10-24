# Incident INC-20261024-003209-payment_paused - DIAGNOSED

- **Detected:** 2026-10-24T00:32:09
- **Alert:** practice incident 'payment_paused' (alert: on-call page)
- **Root cause:** `payment` - service_down  (confidence **0.872**)
- **Summary:** payment: service down. container payment status=paused exit_code=0 (also memory_pressure) graph RCA: payment ranks #1 (share 53%); own anomaly mem 73.1445->81.9392 (z=8.8)

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container payment status=paused exit_code=0; (also memory_pressure) graph RCA: payment ranks #1 (share 53%); own anomaly mem 73.1445->81.9392 (z=8.8)  
  source: `docker ps -a; docker inspect <all>`
- `ev-011` (graph_rca): container payment status=paused exit_code=0; (also memory_pressure) graph RCA: payment ranks #1 (share 53%); own anomaly mem 73.1445->81.9392 (z=8.8)  
  source: `rca.analyze over ev-009 + ev-010 on the merged call graph`

## Alternatives considered
- ad [latency_degradation] score=0.737
- email [dependency_unreachable] score=0.528
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "payment"} - payment diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792782154.9354873, "action": "restart_service", "params": {"service": "payment"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker unpause payment", "current_state": "paused"}}

## Timeline
- 2026-10-24T00:32:09 - alert received: practice incident 'payment_paused' (alert: on-call page)
- 2026-10-24T00:32:34 - triage: 5 suspects, top=payment/service_down
- 2026-10-24T00:32:34 - diagnosis: payment [service_down] conf=0.872
- 2026-10-24T00:32:34 - human approval: APPROVED
- 2026-10-24T00:32:34 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
