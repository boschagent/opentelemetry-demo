# Incident INC-20261024-121442-payment_paused - RESOLVED

- **Detected:** 2026-10-24T12:14:42
- **Alert:** on-call page (payment_paused)
- **Root cause:** `payment` - service_down  (confidence **0.928**)
- **Summary:** The payment container is paused (status=paused, health=unhealthy, exit_code=0) and not processing requests, causing downstream callers to see errors and high latency. No internal errors are logged; the failure originates from the container being stopped.

## Evidence
- `ev-010` (container_health): payment: status=paused health=unhealthy restarts=0 oom=False exit=0 finished_at=2026-10-24T06:05:57.321224702Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-011` (container_stats): payment: cpu=0.0% mem=73.27% (102.6MiB / 140MiB)  
  source: `docker stats --no-stream`
- `ev-014` (service_red): payment: rps=0.075 (base 0.0), err=0.0% (base 0.0%), p95=32.0ms (base Nonems)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`
- `ev-015` (find_error_traces): error ORIGINATES in frontend-proxy / router frontend egress: 9 traces; error ORIGINATES in checkout / POST: 2 traces  msg="context deadline exceeded"  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=payment&query.start_time_min=2026-10-24T06%3A36%3A21Z&query.start_time_max=2026-10-24T06%3A`

## Alternatives considered
- checkout latency degradation due to downstream payment unavailability

## Reviewer agent
- verdict: **agree** - ev-010 directly shows the payment container paused and unhealthy, and ev-015 traces timeouts in frontend-proxy and checkout consistent with an unavailable downstream service. No other component shows a comparable failure state.

## Action
- Proposed: restart_service {"service": "payment"} - payment diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792824416.2057316, "action": "restart_service", "params": {"service": "payment"}, "dry_run": false, "response": {"ok": true, "ran": "docker unpause payment", "output": "payment", "previous_state": "paused"}}
- Verification: recovered=True

## Timeline
- 2026-10-24T12:14:42 - alert received: on-call page (payment_paused)
- 2026-10-24T12:15:02 - triage: 3 suspects, top=payment/service_down
- 2026-10-24T12:16:36 - LLM investigation finished (7 steps)
- 2026-10-24T12:16:38 - reviewer verdict: agree
- 2026-10-24T12:16:38 - diagnosis: payment [service_down] conf=0.928
- 2026-10-24T12:16:55 - human approval: APPROVED
- 2026-10-24T12:16:56 - action restart_service -> ok=True dry_run=False
- 2026-10-24T12:18:30 - verification: recovered=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
