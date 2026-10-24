# Incident INC-20261024-001918-valkey - DIAGNOSED

- **Detected:** 2026-10-24T00:19:18
- **Alert:** practice incident 'valkey' (alert: on-call page)
- **Root cause:** `cart` - app_errors  (confidence **0.666**)
- **Summary:** cart: app errors. cart logged 71 error/warn lines in the last 3 min (baseline 1) graph RCA: cart ranks #1 (share 39%); own anomaly err 0.0->1.0 (z=100.0), p95 49.0667->9750.0 (z=902.7); its callers broke after it: checkout +60s (also latency_degradation) cart p95 9750.0ms vs baseline 88.5ms (x110.16)

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-005` (get_logs): cart logged 71 error/warn lines in the last 3 min (baseline 1); graph RCA: cart ranks #1 (share 39%); own anomaly err 0.0->1.0 (z=100.0), p95 49.0667->9750.0 (z=902.7); its callers broke after it: checkout +60s; (also latency_degradation) cart p95 9750.0ms vs baseline 88.5ms (x110.16)  
  source: `docker logs --since 3m --timestamps cart`
- `ev-013` (graph_rca): cart logged 71 error/warn lines in the last 3 min (baseline 1); graph RCA: cart ranks #1 (share 39%); own anomaly err 0.0->1.0 (z=100.0), p95 49.0667->9750.0 (z=902.7); its callers broke after it: checkout +60s; (also latency_degradation) cart p95 9750.0ms vs baseline 88.5ms (x110.16)  
  source: `rca.analyze over ev-011 + ev-012 on the merged call graph`
- `ev-003` (service_red): cart logged 71 error/warn lines in the last 3 min (baseline 1); graph RCA: cart ranks #1 (share 39%); own anomaly err 0.0->1.0 (z=100.0), p95 49.0667->9750.0 (z=902.7); its callers broke after it: checkout +60s; (also latency_degradation) cart p95 9750.0ms vs baseline 88.5ms (x110.16)  
  source: `PromQL:
sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[3m]))
sum by (service_name) (rate(traces_span_metrics_calls_to`

## Alternatives considered
- valkey-cart [service_down] score=0.9
- ad [latency_degradation] score=0.6
- email [dependency_unreachable] score=0.505

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as app_errors; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792781379.9739223, "action": "restart_service", "params": {"service": "cart"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker restart cart", "current_state": "running"}}

## Timeline
- 2026-10-24T00:19:18 - alert received: practice incident 'valkey' (alert: on-call page)
- 2026-10-24T00:19:39 - triage: 5 suspects, top=cart/app_errors
- 2026-10-24T00:19:39 - diagnosis: cart [app_errors] conf=0.666
- 2026-10-24T00:19:39 - human approval: APPROVED
- 2026-10-24T00:19:39 - action restart_service -> ok=True dry_run=True

## Prevention
- Add an error-rate SLO alert per service, canary/feature-flag guardrails and a regression test for the failing path.
