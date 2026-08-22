# Query-or-respond result

## Outcome

| Field | Value |
|---|---|
| Verdict | `INCONCLUSIVE` |
| Reason | `query judge calibration failed authored threshold` |
| Sealed measurement SHA | `a0f5c070cb925d34d733a783364d5168a191d536` |
| Default query-response arm | `current` |
| Default safety classifier | `llm` |

## Sealed calibration

The Todo 11 seal records the query calibration as `INCONCLUSIVE`: 22 of 24
fixtures passed. `chat-greeting-ok-1` scored `0.78` against `0.80`, and
`chat-greeting-ok-2` scored `0.72` against `0.80`. The sealed safety
calibration is `PASS`, with 12 of 12 fixtures passing and no failures.

## Todo 12 execution state

No adapter, stage 1, stage 2, paid measurement, current-arm measurement,
control-arm measurement, or tool-arm measurement was attempted. The exact paid
command was not executed:

```sh
uv run python -m evals.routing_gate --lane query --stage all --repetitions 2 --concurrency 1 --json --report-name query-or-respond
```

The measurement collections for every arm, metrics, deltas, cost, latency, and
experiment URLs are empty.

## Sealed provenance

Source evidence: [Todo 11 seal](../../.omo/evidence/task-11-evaluate-query-response-and-semantic-safety-routing.json),
[Todo 11 report](../../.omo/evidence/task-11-evaluate-query-response-and-semantic-safety-routing.log),
and [Todo 11 verification](../../.omo/evidence/task-11-final-independent-adversarial-verify.md).

The current provenance hashes match the seal: code
`d9a17baf9fc0908728b84fcd135e544449fd68ea3c32abb60128d7acdca5b285`;
dataset `d43f6ef16ff74dcd7116c5c4e86da446b6c4ccbf060fd9689098f1fc72be9d63`;
multiturn `d77fd9dc840585001496e280f7625d00fec28fe39965420a1eb4352c91ab1278`;
prototypes `402c39e9e2910147a45dde46b04140977cdedb15348ce3addc79cfcecf3d36c1`;
thresholds `214240abc7c2c779cb12b84d4ef8a0b547e8925224a06318b2f893d68f5919c6`;
evaluators `6ca003e9f1102e1d4336fe1c901f09200dfaa66208b9433c2568f76399de4fb6`;
prompts `47f414841b1ad76390341c1de7e2ca071dc74ded935efb5a581cadf457596d7f`;
and lockfile `b7fbfde6ed7f4ee81a0fd7320752196d780bbba79bda7eb380bd2b858118b691`.

The sealed raw inputs match: `evals/routing_dataset.json`
`22bf7c7cd1df47416bcd7c7c559f5059c5763ecb6c857c18b53a43a373435ed7`,
`evals/routing_multiturn_dataset.json`
`2690f1e854c47e9597e6c0c0b9472646ab6a66e7a766a74483f42c515d0cbaa1`,
`evals/routing_prototypes.json`
`079cb9c517a51887ac3eb9326658d92966af896c7d4541dd5ebc949edcb198a4`, and
`evals/routing_evaluator_calibration.json`
`9621a2210b5d64ee1034e11243d545a21ed9c686b08180fd29741339f1aa0490`.
