# Incident INC-20261023-235527-healthy - ESCALATED

- **Detected:** 2026-10-23T23:55:27
- **Alert:** practice incident 'healthy' (alert: on-call page)
- **Root cause:** `cart` - latency_degradation  (confidence **0.504**)
- **Summary:** cart: latency degradation. graph RCA: cart ranks #1 (share 36%); own anomaly p95 45.6364->200.0 (z=30.9)

> **Escalated to human:** confidence 0.50 below threshold 0.55

## Evidence
- `ev-010` (graph_rca): graph RCA: cart ranks #1 (share 36%); own anomaly p95 45.6364->200.0 (z=30.9)  
  source: `rca.analyze over ev-008 + ev-009 on the merged call graph`

## Alternatives considered
- shipping [latency_degradation] score=0.6
- email [dependency_unreachable] score=0.521
- ad [latency_degradation] score=0.5

## Action
- Proposed: restart_service {"service": "cart"} - cart diagnosed as latency_degradation; restart is the allowlisted remedy (clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers).

## Timeline
- 2026-10-23T23:55:27 - alert received: practice incident 'healthy' (alert: on-call page)
- 2026-10-23T23:55:40 - triage: 5 suspects, top=cart/latency_degradation
- 2026-10-23T23:55:40 - diagnosis: cart [latency_degradation] conf=0.504
- 2026-10-23T23:55:40 - escalated to human: confidence 0.50 below threshold 0.55

## Prevention
- Add a p95 latency SLO alert per service and investigate the slow dependency.
