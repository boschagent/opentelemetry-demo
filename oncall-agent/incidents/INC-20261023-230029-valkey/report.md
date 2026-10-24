# Incident INC-20261023-230029-valkey - ESCALATED

- **Detected:** 2026-10-23T23:00:29
- **Alert:** practice incident 'valkey' (alert: on-call page)
- **Root cause:** `valkey-cart` - service_down  (confidence **0.93**)
- **Summary:** The valkey-cart container is paused and marked unhealthy, so the Redis instance is unreachable. Cart service calls time out (RedisTimeoutException), causing 100% error rates and high latency that cascade to checkout and frontend.

> **Escalated to human:** system did not recover after the approved action (restart has no inverse)

## Evidence
- `ev-014` (container_health): valkey-cart: status=paused health=unhealthy restarts=0 oom=False exit=0  
  source: `docker ps -a; docker inspect <all>`
- `ev-015` (find_error_traces): error ORIGINATES in cart ... "Can't access cart storage. StackExchange.Redis.RedisTimeoutException: The message timed out in the backlog attempting to send because no connection"  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=cart&query.start_time_min=2026-10-23T17%3A21%3A30Z&query.start_time_max=2026-10-23T17%3A31%`
- `ev-018` (service_red): cart: err=100.0% (base 0.0%), p95=9750.0ms (base 47.8ms) – errors propagate from cart to checkout and frontend  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- cart app_errors due to internal bug
- frontend high_cpu causing downstream timeouts

## Reviewer agent
- verdict: **agree** - ev-014 shows valkey-cart paused/unhealthy, directly causing Redis connection failures observed in ev-015 and the 100% error rate in cart (ev-018). No other component shows a comparable root‑cause failure.

## Action
- Proposed: restart_service {"service": "valkey-cart"} - valkey-cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792776788.9384992, "action": "restart_service", "params": {"service": "valkey-cart"}, "dry_run": false, "response": {"ok": true, "ran": "docker unpause valkey-cart", "output": "valkey-cart", "previous_state": "paused"}}
- Verification: recovered=False

## Timeline
- 2026-10-23T23:00:29 - alert received: practice incident 'valkey' (alert: on-call page)
- 2026-10-23T23:00:58 - triage: 6 suspects, top=cart/app_errors
- 2026-10-23T23:03:01 - LLM investigation finished (6 steps)
- 2026-10-23T23:03:03 - reviewer verdict: agree
- 2026-10-23T23:03:03 - diagnosis: valkey-cart [service_down] conf=0.93
- 2026-10-23T23:03:05 - human approval: APPROVED
- 2026-10-23T23:03:08 - action restart_service -> ok=True dry_run=False
- 2026-10-23T23:07:14 - verification: recovered=False

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
