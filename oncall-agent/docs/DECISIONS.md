# Decisions log: judgement calls that could NOT be proven by a deterministic test

Each entry records the choice, why we made it, how to change it, and its status.

| # | decision | why | how to change | status |
|---|---|---|---|---|
| D1 | Strip all `feature_flag.*` telemetry and flagd services | Mentor said agents must not look at flag details; the demo's OpenFeature hooks put flag keys on spans (checkout, product-catalog) and the ad service logs flag names | `core.redact` | **confirmed by mentor** |
| D2 | Thresholds in `triage.THRESHOLDS` (5% errors, x2 p95, x2 traffic, 85% mem, 80% CPU, 100 ms gap) | Reasonable SRE defaults; tuned on scripted scenarios only | edit the dict, re-run `eval/run_eval.py` | **needs tuning on real practice runs** |
| D3 | Escalation threshold 0.55 | Slides: escalation scores better than a confident wrong fix | `ONCALL_CONFIDENCE_THRESHOLD` | tune with the calibration table |
| D4 | Our own JSON report schema | Organisers' schema not found yet | `report.to_organiser_schema()` | **ask the mentor for the official schema** |
| D5 | Our own local Action API | Organisers' Action API not found yet | point `ONCALL_ACTION_API_URL` at theirs and adapt `act.call_action` paths | **ask the mentor** |
| D6 | Restart as the default remedy for app errors | Only generic allowlisted action available; flag toggling needs the organisers' API | `triage.REMEDY` | open: ask whether flag toggle-off is an allowed *remedy* |
| D7 | Traffic flood root-cause component = `load-generator` | It is the source of the traffic, and the remedy targets it | `triage.py` R7 | **ask how the hidden test names it** |
| D8 | Verification = container running + ≤1 error trace in the last minute, twice in a row | Metrics lag about 60 s; traces are near real time | `act.health_check` | to validate live |
| D9 | LLM uses JSON replies, not native tool-calling | Works with free and local models that have weak tool-calling | `brain.py` | fixed |
