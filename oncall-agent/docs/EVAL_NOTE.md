# Evaluation note

_Fill this in from `eval/SCOREBOARD.md` after recording the practice incidents._

## Method
- Each practice incident was injected live, and the telemetry was recorded into a bundle (`eval/bundles/<name>`).
- The true answer was written by a human into `eval/labels.json` from what was injected (never from the agent's output).
- `python eval/run_eval.py` replays every bundle offline and scores it as **correct**, **escalated** (honest "not sure") or **WRONG** (confident and wrong).
- The deterministic self-test (`python -m oncall.selftest`, 19 tests) checks safety and logic requirements on scripted scenarios.

## Results
| incident | truth | predicted | confidence | outcome | why |
|---|---|---|---|---|---|
| | | | | | |

## What we got wrong and why
- 

## Calibration
(paste the calibration table: confidence bucket → accuracy)
