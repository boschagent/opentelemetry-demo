# Incident INC-20261024-000555-cart_down - DIAGNOSED

- **Detected:** 2026-10-24T00:05:55
- **Alert:** practice incident 'cart_down' (alert: on-call page)
- **Root cause:** `cart` - service_down  (confidence **0.89**)
- **Summary:** cart: service down. container cart status=exited exit_code=137 graph RCA: cart ranks #2 (share 13%); own anomaly rps_down 0.3756->0.0 (z=5.1); its callers broke after it: frontend +0s, frontend-proxy +0s (also dependency_unreachable) 14 failed calls from frontend to cart never reached a cart server span

> **Escalated to human:** dry-run: no change was made

## Evidence
- `ev-001` (container_health): container cart status=exited exit_code=137; graph RCA: cart ranks #2 (share 13%); own anomaly rps_down 0.3756->0.0 (z=5.1); its callers broke after it: frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 14 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 6 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-011` (graph_rca): container cart status=exited exit_code=137; graph RCA: cart ranks #2 (share 13%); own anomaly rps_down 0.3756->0.0 (z=5.1); its callers broke after it: frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 14 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 6 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `rca.analyze over ev-009 + ev-010 on the merged call graph`
- `ev-007` (find_error_traces): container cart status=exited exit_code=137; graph RCA: cart ranks #2 (share 13%); own anomaly rps_down 0.3756->0.0 (z=5.1); its callers broke after it: frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 14 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 6 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-23T18%3A26%3A06Z&query.start_time_max=2026-10-23T18%3`
- `ev-006` (find_error_traces): container cart status=exited exit_code=137; graph RCA: cart ranks #2 (share 13%); own anomaly rps_down 0.3756->0.0 (z=5.1); its callers broke after it: frontend +0s, frontend-proxy +0s; (also dependency_unreachable) 14 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 6 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=checkout&query.start_time_min=2026-10-23T18%3A26%3A06Z&query.start_time_max=2026-10-23T18%3`

## Alternatives considered
- load-generator [traffic_surge] score=0.7
- frontend [dependency_unreachable] score=0.681
- product-catalog [crash_restart] score=0.6

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792780567.3685396, "action": "restart_service", "params": {"service": "cart"}, "dry_run": true, "response": {"ok": true, "dry_run": true, "would_run": "docker start cart", "current_state": "exited"}}

## Timeline
- 2026-10-24T00:05:55 - alert received: practice incident 'cart_down' (alert: on-call page)
- 2026-10-24T00:06:06 - triage: 5 suspects, top=cart/service_down
- 2026-10-24T00:06:06 - diagnosis: cart [service_down] conf=0.89
- 2026-10-24T00:06:06 - human approval: APPROVED
- 2026-10-24T00:06:07 - action restart_service -> ok=True dry_run=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
