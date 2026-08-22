# coach-agent-current-baseline

Current-checkout offline CoachEngine report; no historical numbers are used.
Fake judges run deterministic seeds 17/29/43; deployed judging reuses the real gateway.

## Overall

| Metric | Value |
|---|---:|
| chunk_recall | 1.0 |
| correctness | 1.0 |
| forbidden_content | 0.0 |
| groundedness | 1.0 |
| safe_redirect | 1.0 |

## Tagged cases

| Case | Tag | Result |
|---|---|---|
| route_a_informational | route_a | PASS |
| route_a_inner_short_circuit | route_a | PASS |
