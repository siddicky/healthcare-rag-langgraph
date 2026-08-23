<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# evals/langsmith

## Purpose
Code and configuration that runs *inside LangSmith itself*, independent of this
repo's inline evaluators — an external second opinion. `code_evaluators.py` is
uploaded as dataset-bound code evaluators via the LangSmith CLI so every future
experiment on the golden dataset is scored automatically; `insights.py` configures
scheduled "insight agent" reports over tracing projects.

## Key Files
| File | Description |
|------|-------------|
| `code_evaluators.py` | `ls_must_mention_recall`, `ls_forbidden_content`, `ls_numeric_advice_leak`, `ls_retrieval`, `ls_routing`, `ls_reliability`, `ls_cost_latency` — `ls_`-prefixed mirrors of `evals/evaluators.py`, each **fully self-contained** (the CLI uploads only the named function's source; module-level helpers are not available at runtime — a past upload hit `NameError: _ref` from violating this) |
| `insights.py` | `build_config()`/`save_config()`/`run_job()` — sets up and runs LangSmith Insights (LLM-summarized trace sampling → clustering → executive summary); CLI: `setup` (save + schedule standing configs) and `run --project <name> --kind <kind>` (one-off report) |
| `model_config.json` | The judge model binding for LangSmith-side evaluators (currently `gpt-5.6-sol` via `ChatOpenAI`) |
| `variable_mapping.json` | Maps LangSmith evaluator input names (`question`, `answer`, `contexts`, `reference`, `expected_behavior`) to `inputs.*`/`outputs.*`/`reference.*` paths |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `prompts/` | `behavior.json`, `correctness.json`, `groundedness.json` — the LangSmith-hosted judge prompts corresponding to the inline judges in `evals/evaluators.py` |
| `schemas/` | `behavior.json`, `correctness.json`, `groundedness.json` — the structured-output schemas paired with each prompt |

## For AI Agents

### Working In This Directory
- Any new or edited function in `code_evaluators.py` must import everything it needs at module scope *inside the function* (or as a top-of-file import that the CLI still bundles) — do not factor out a shared helper at module level, it silently disappears at upload time and the evaluator fails with a `NameError` on its first real run.
- Keep `ls_*` evaluators scoring the same underlying facts as their `evals/evaluators.py` counterparts so the two can be compared directly; if the deterministic definition changes in one place, mirror it here.
- `insights.py` needs the LangSmith API surface reachable via `_client()`/`_post()`/`_get()` — these are thin REST wrappers, not the `langsmith` SDK's high-level client, because Insights config endpoints are not yet in the SDK.
- Uploading is manual (`register.sh` mentioned in `code_evaluators.py`'s docstring, run separately) — editing this file does not update the live LangSmith evaluators until that upload step runs.

### Testing Requirements
There are no unit tests scoped to this directory specifically; `code_evaluators.py`
logic overlaps `evals/evaluators.py`, which is covered by `tests/test_evaluators.py`
and `tests/test_evaluator_calibration.py`. Verify an uploaded evaluator by checking
its scores on the next `make eval` experiment in the LangSmith UI, not locally.

### Common Patterns
- Every `ls_*` function takes `(run, example)` (the LangSmith code-evaluator
  signature) rather than the `(inputs, outputs, reference_outputs)` signature used
  by `evals/evaluators.py` — don't conflate the two call shapes when porting a
  metric from one file to the other.

## Dependencies

### Internal
- Conceptually mirrors `evals/evaluators.py` (kept in sync by hand, not by import, per the self-containment constraint above)

### External
- LangSmith Insights REST API (via `insights.py`'s `_post`/`_get`)
- `langchain_openai.ChatOpenAI` (referenced by `model_config.json`'s serialized constructor)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
