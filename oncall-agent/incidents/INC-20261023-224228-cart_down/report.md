# Incident INC-20261023-224228-cart_down - RESOLVED

- **Detected:** 2026-10-23T22:42:28
- **Alert:** practice incident 'cart_down' (alert: on-call page)
- **Root cause:** `cart` - service_down  (confidence **0.919**)
- **Summary:** The cart container exited with exit_code 137 (SIGKILL). No OOM flag on the container itself, but the host is under high memory pressure (multiple services >90% memory, astronomy-db OOM flag true). The host likely killed the cart process, causing downstream checkout and frontend errors.

## Evidence
- `ev-011` (container_health): cart: status=exited health=None restarts=0 oom=False exit=137 finished_at=2026-10-23T17:08:26.526474705Z  
  source: `docker ps -a; docker inspect <all>`
- `ev-012` (get_logs): cart logs show normal operation calls, no explicit crash or OOM messages.  
  source: `docker logs --since 15m --timestamps cart`
- `ev-013` (container_stats): Many services report high memory usage: product-catalog mem=81.5% (16.3MiB/20MiB), payment mem=91.38% (127.9MiB/140MiB), frontend mem=95.17% (237.9MiB/250MiB), astronomy-db mem=92.68% (74.14MiB/80MiB).  
  source: `docker stats --no-stream`
- `ev-001` (container_health): deterministic triage ranked cart as service_down with score=0.979.  
  source: `docker ps -a; docker inspect <all>`

## Alternatives considered
- astronomy-db OOM could be a co‑occurring issue.

## Reviewer agent
- verdict: **agree** - The cart container exited with SIGKILL (code 137) while no OOM flag is set on it, and host memory pressure is high (many services >90% memory, astronomy-db OOM flag true). Downstream calls to cart fail, matching the observed errors, and no other component shows a direct cause.

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as service_down; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).
- Approval: approved=True by Amrith
- Taken: {"ts": 1792775762.8164566, "action": "restart_service", "params": {"service": "cart"}, "dry_run": false, "response": {"ok": true, "ran": "docker start cart", "output": "cart", "previous_state": "exited"}}
- Verification: recovered=True

## Timeline
- 2026-10-23T22:42:28 - alert received: practice incident 'cart_down' (alert: on-call page)
- 2026-10-23T22:43:02 - triage: 4 suspects, top=cart/service_down
- 2026-10-23T22:44:42 - LLM investigation finished (6 steps)
- 2026-10-23T22:45:09 - reviewer verdict: agree
- 2026-10-23T22:45:09 - diagnosis: cart [service_down] conf=0.919
- 2026-10-23T22:45:57 - human approval: APPROVED
- 2026-10-23T22:46:02 - action restart_service -> ok=True dry_run=False
- 2026-10-23T22:46:52 - verification: recovered=True

## Prevention
- Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.
