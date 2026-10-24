# Incident INC-20261024-101245-cart_down - RESOLVED

- **Detected:** 2026-10-24T10:12:45
- **Alert:** practice incident 'cart_down' (alert: on-call page)
- **Root cause:** `cart` - service_down  (confidence **0.898**)
- **Summary:** cart: service down. container cart status=exited exit_code=0 (also dependency_unreachable) 27 failed calls from frontend to cart never reached a cart server span (also dependency_unreachable) 14 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span

## Evidence
- `ev-001` (container_health): container cart status=exited exit_code=0; (also dependency_unreachable) 27 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 14 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `docker ps -a; docker inspect <all>`
- `ev-007` (find_error_traces): container cart status=exited exit_code=0; (also dependency_unreachable) 27 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 14 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=frontend&query.start_time_min=2026-10-24T04%3A33%3A03Z&query.start_time_max=2026-10-24T04%3`
- `ev-006` (find_error_traces): container cart status=exited exit_code=0; (also dependency_unreachable) 27 failed calls from frontend to cart never reached a cart server span; (also dependency_unreachable) 14 failed calls from checkout to oteldemo.CartService/GetCart never reached a cart server span  
  source: `GET http://localhost:8080/jaeger/ui/api/v3/traces?query.service_name=checkout&query.start_time_min=2026-10-24T04%3A33%3A03Z&query.start_time_max=2026-10-24T04%3`

## Alternatives considered
- frontend [app_errors] score=0.175

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792817021.7305224, "action": "restart_service", "params": {"service": "cart"}, "dry_run": false, "response": {"ok": true, "ran": "docker start cart", "output": "cart", "previous_state": "exited"}}
- Verification: recovered=True

## Timeline
- 2026-10-24T10:12:45 - alert received: practice incident 'cart_down' (alert: on-call page)
- 2026-10-24T10:13:04 - triage: 2 suspects, top=cart/service_down
- 2026-10-24T10:13:04 - LLM investigation finished (1 steps)
- 2026-10-24T10:13:04 - diagnosis: cart [service_down] conf=0.898
- 2026-10-24T10:13:40 - human approval: APPROVED
- 2026-10-24T10:13:41 - action restart_service -> ok=True dry_run=False
- 2026-10-24T10:14:28 - verification: recovered=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
