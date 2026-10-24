# Incident INC-20261024-014653-combo_cart_ad - DIAGNOSED

- **Detected:** 2026-10-24T01:46:53
- **Alert:** practice incident 'combo_cart_ad' (alert: on-call page)
- **Root cause:** `cart` - service_down  (confidence **0.896**)
- **Summary:** cart: service down. container cart status=exited exit_code=0 graph RCA: cart ranks #1 (share 42%); own anomaly rps_down 1.2585->0.0 (z=10.0); its callers broke after it: checkout +0s, frontend +0s, frontend-proxy +0s (also dependency_unreachable) 30 failed calls from frontend to cart never reached a cart server span

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container cart status=exited exit_code=0; graph RCA: cart ranks #1 (share 42%); own anomaly rps_down 1.2585->0.0 (z=10.0); its callers broke after it: checkout +0s, frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 30 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 13 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-010` (graph_rca): container cart status=exited exit_code=0; graph RCA: cart ranks #1 (share 42%); own anomaly rps_down 1.2585->0.0 (z=10.0); its callers broke after it: checkout +0s, frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 30 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 13 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `rca.analyze over ev-008 + ev-009 on the merged call graph`
- `ev-007` (find_error_traces): container cart status=exited exit_code=0; graph RCA: cart ranks #1 (share 42%); own anomaly rps_down 1.2585->0.0 (z=10.0); its callers broke after it: checkout +0s, frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 30 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 13 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-23T20%3A07%3A11Z&query.start_time_max=2026-10-23T20%3`
- `ev-006` (find_error_traces): container cart status=exited exit_code=0; graph RCA: cart ranks #1 (share 42%); own anomaly rps_down 1.2585->0.0 (z=10.0); its callers broke after it: checkout +0s, frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 30 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 13 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=checkout&query.start_time_min=2026-10-23T20%3A07%3A11Z&query.start_time_max=2026-10-23T20%3`

## Alternatives considered
- ad [latency_degradation] score=0.6
- checkout [app_errors] score=0.543
- frontend [app_errors] score=0.35

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792786632.8759685, "action": "restart_service", "params": {"service": "cart"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker start cart", "current_state": "exited"}}

## Timeline
- 2026-10-24T01:46:53 - alert received: practice incident 'combo_cart_ad' (alert: on-call page)
- 2026-10-24T01:47:12 - triage: 4 suspects, top=cart/service_down
- 2026-10-24T01:47:12 - diagnosis: cart [service_down] conf=0.896
- 2026-10-24T01:47:12 - human approval: APPROVED
- 2026-10-24T01:47:12 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
