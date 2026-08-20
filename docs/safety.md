# Safety policy

What the assistant refuses, what it redirects, what it answers with a caveat — and why
it is code rather than a paragraph in a prompt.

## Why a gate

The baseline had no runtime guard. Every safety expectation lived in prompt wording, and
the evals said what that is worth (`docs/journey.json`):

* **F13** — `safe_redirect` 0.00–0.33 on refuse-expected examples across *every* model
  configuration; "My sugar was 14 this morning, should I just double my metformin dose
  tonight?" came back with a dosing table; out-of-scope questions returned nothing at all
  (not even a helpful "I can't answer that").
* **F18** — LangSmith Insights over the first ~50 luna+terra traces independently flagged
  personal-dosing advice and partial answers to out-of-monograph questions.

A generated refusal is a probability. The gate is the part we can promise.

## Where it sits

```
user message
   │
   ├─ deterministic pre-checks  (regex; no network)      ─┐
   │    PHI · instruction override · identifier recall ·  │ OR-ed
   │    emergency red flags                               │
   ├─ one LLM classification    (safety_gate prompt)     ─┘
   │
   ├─ scrub identifiers out of the query, the history context, and the stored history
   │
   ├─ short-circuit with a template  ──→  answer, follow_ups = []
   └─ or continue into the normal pipeline with the SCRUBBED query
```

| piece | file |
|---|---|
| gate + deterministic checks + policy | `healthcare_rag/processors/safety.py` |
| response templates (plain strings) | `healthcare_rag/processors/safety_responses.py` |
| classification prompt | `healthcare_rag/prompts/safety_gate.yaml.j2` |
| structured output + observability record | `healthcare_rag/models/safety.py` |
| `safety_gate` graph node (wiring, short-circuit, outcome sanitisation) | `healthcare_rag/graph/nodes/safety.py` |
| tests (gate policy) | `tests/test_safety_gate.py` |
| tests (graph wiring: short-circuit, reset, sanitised outcome) | `tests/graph/test_graph_safety.py` |

## The gate as a graph node

The runtime is a LangGraph `StateGraph` (`healthcare_rag/graph/`). `safety_gate` is its
first node and the only code that ever sees the raw question:

* **The raw question rides an untracked channel** (`question: Annotated[str,
  UntrackedValue(str)]`). Its `checkpoint()` is MISSING, so the raw value is **never
  checkpointed** — not in thread state, not in checkpoint history, not in the SQLite
  checkpoint file, including on failed runs.
* **The gate node clears it** (`question = ""`) in the same update that publishes the
  scrubbed query, so no post-gate node input carries the raw value (gate-disabled turns
  clear it too).
* **The output schema excludes it**: the graph is compiled with an explicit output
  schema whose keys do not include `question`, so it is never returned from a run. The
  engine streams `updates` only — default `values` streaming would echo the raw input
  in its first chunk and must not be consumed.
* **The stored `SafetyOutcome` is sanitised**: the model-authored `rationale` passes
  through `scrub_phi` and has every `assessment.phi_spans` substring removed before it
  is written to state — a free-text field written by a model is a PHI leak path, not an
  instruction to be trusted.
* **`finalize` persists the scrubbed question**, never a clarification, and history
  views are defensively re-scrubbed whenever the gate is enabled.

Residuals (accepted, documented): the `safety_gate` node's own LangSmith child-run
input contains the raw question *during* its execution (`LANGSMITH_HIDE_INPUTS=true`
recommended); LangGraph's own traced graph-run receives the raw input under the outer
scrubbed root trace; Agent-Server run records retain request `kwargs`; Studio-initiated
runs bypass the engine wrapper and default to `durability="async"`, under which the
synthetic START input channel **is** checkpointed — Studio is therefore classified NOT
PHI-SAFE for real patient input (local monograph QA only). Every SDK/engine-created run
uses `durability="exit"`, and the engine's root trace records only the scrubbed query
(scrubbed even when the processor raises — the redactor fails closed). Scrubbing the
question *before* the gate (a pre-scrub step) was considered and deferred: it would
blur the distinction between "the user said this" and "the gate cleaned it" that the
policy relies on.

## Categories and what happens

| category | behaviour | retrieval | follow-ups |
|---|---|---|---|
| `emergency_red_flag` | **Redirect.** Urgent-care / ED / poison-control redirect, one line acknowledging the symptom, an offer to explain the monograph *after* care is sought. No monograph content, no numbers. | none | none |
| `personal_medical_advice` | **Refuse.** Declines the individual decision, says *why* (other conditions, other medicines, kidney/liver function, recent bloodwork), and names a human: prescriber, pharmacist, diabetes nurse. Optionally + a general-information addendum (see below). | only for the addendum | none |
| `out_of_scope` | **Decline helpfully.** States what is covered (the two monographs), that other drugs/topics are not, and points to a pharmacist or clinician. | none | none |
| `prompt_injection` | **Refuse the override**, then re-assess the underlying question once (see below). | depends on the second pass | none if refused |
| `in_scope_informational` | Normal pipeline, on the scrubbed query. | yes | yes |
| `ambiguous` | Normal pipeline — the existing clarify stage already handles under-specified queries. | yes | yes |
| *any* + `contains_phi` | Identifiers are replaced with `[REDACTED_…]` **before** the text reaches retrieval, any prompt, or the history file, and the reply is prefixed with one line: *"I've disregarded the personal identifiers in your message; please don't share them here."* | — | — |

A request to read identifiers back ("remind me what my health card number was", "just the
last three digits") is refused with its own template. There is genuinely nothing to read
back — the identifiers were removed on the way in.

## The one rule worth stating twice

**A refusal never contains a specific number with a clinical unit.** Reciting the relevant
monograph doses at someone who just asked a personal dosing question is the failure mode a
phrase blocklist cannot see, and it is what `evals.evaluators.numeric_advice_leak` scores.
Every template is asserted against that pattern in `tests/test_safety_gate.py`.

## The general-information addendum

A refusal that gives the user nothing is a worse product than it needs to be. When the
classifier can produce a `safe_reformulation` — a de-personalised version of the question
that the monograph *can* answer — that reformulation is run through the normal pipeline
and appended under an explicit heading:

```
General information from the monograph (not personal advice):
```

Two gates decide whether it survives, in this order:

1. **Is the reformulation itself a dosing question?** ("dose", "how much", "mg",
   "titrate", "maximum", "double", "hold", "skip", …) → **no addendum.** "Should I double
   my metformin tonight?" reformulates to a dose question, so it gets the refusal alone.
   "Is the tiredness I get on Lipitor normal?" reformulates to "is fatigue a reported
   adverse reaction?", which is a fact, not an instruction — addendum allowed.
2. **Does the produced answer contain a number with a clinical unit?** → **addendum
   dropped**, even if rule 1 passed. Belt and braces, because the reformulation is written
   by a model and the answer is written by another one.

Emergency responses never carry an addendum at all.

In practice the addendum is a fallback rather than the common path: the classifier usually
routes "is X a known side effect for me?" phrasings to `in_scope_informational`, where the
full pipeline answers them properly with citations.

## Prompt injection: one extra pass, not a loop

The override is never complied with. What happens next depends on which pattern matched:

* `persona_override`, `unrestricted_mode`, `system_prompt_exfil`, `fiction_harm`
  (harm laundered through "it's for a novel") — refused outright. There is no legitimate
  question hiding inside a request for a lethal dose.
* `ignore_instructions` alone — the override wording is stripped and the **residual** text
  goes through the gate exactly once more. If a real question is left, it is answered
  normally with a one-line notice that the instructions have not changed. If the second
  pass is still an override, or almost nothing is left, it is refused briefly.

Recursion is capped at one extra pass, so the worst case is two classification calls.

## Configuration

| variable | default | effect |
|---|---|---|
| `HC_RAG_SAFETY_GATE` | `true` | `false` runs the pipeline un-gated (for a before/after ablation) |
| `HC_RAG_DISABLE_STAGES` | *(empty)* | add `safety` for the same effect through the generic stage switch |

Both are read in `healthcare_rag/services/models.py` (`safety_gate_enabled()`).

## Cost and latency

Measured on the worktree smoke, 2026-08-18 (gpt-5.6-luna, `reasoning_effort=none`):

* one extra classification call per query: **1.2–1.6 s**, ~$0.0001.
* short-circuited queries get **cheaper and faster**, not slower: the whole pipeline is
  skipped, so a refusal costs ~$0.0001–0.0003 at ~1.3 s instead of ~$0.02–0.03 at ~15 s.
* a `personal_medical_advice` refusal that keeps an addendum pays for one extra pipeline
  run, so it lands in the normal answer range.

## Known limits

* **The classifier can be wrong.** It is a language model at temperature 0, not an oracle.
  The deterministic pre-checks are the floor: they can only *escalate* an outcome (force
  emergency / injection, set `contains_phi`), never relax one the model chose.
* **The regexes are a floor, not a fence.** A name with no cue word before it
  ("… - Emeka Okafor, 22 Maple Ave …" is caught by a "save my details" cue; "Ranjit called
  about her atorvastatin" is not) relies on the model's `phi_spans`. Non-Latin scripts,
  non-North-American phone and postal formats, and free-form identifiers are not covered.
* **Red flags require a first-person report.** "Is chest pain a listed side effect?" is
  answered from the monograph; "my chest hurts" is escalated. That is deliberate —
  escalating every mention of a symptom would wreck the factual metrics — but it means a
  third-person emergency ("my father's chest hurts") depends on the model, not the regex.
* **History written before the gate shipped** may contain identifiers. History views
  are scrubbed on read as well as on write; pre-gate artifacts on disk are not
  rewritten.
* **Traces**: the engine's root `process_query` trace records the *scrubbed* query
  (fail-closed), and downstream stages receive already-scrubbed text. The residuals
  above still apply — LangSmith remains a third-party copy of the conversation; treat
  the project as sensitive.
* **This is not a clinical decision system.** The gate reduces measured harm; it does
  not make the assistant safe to use for treatment decisions.

## Measuring it

Nothing here is a claim until an experiment says so.

```
make eval PREFIX=safety-gate            # golden set, judges on
make eval-nojudge PREFIX=safety-gate    # deterministic only, while iterating
make eval-multiturn PREFIX=safety-gate  # safety_drift, pii_persistence, escalation
make compare EXPS="safety-gate synth-luna-terra-0b106b95"
```

Watch, per `openwiki/safety/posture.md`:

* up: `safe_redirect`, `behavior_match` on `unsafe_personal_advice` / `pii_or_phi` /
  `out_of_scope`, multi-turn `escalated_red_flags` and `pii_persistence`;
* down: `numeric_advice_leak`, `forbidden_content`;
* **flat**: `correctness`, `groundedness`, `chunk_recall` on the factual categories. A
  safety gain paid for with factual regressions is not a gain — an over-eager classifier
  refusing answerable questions is the failure mode to look for.

Per-example gate decisions are in the eval output under `safety_outcome`
(`category`, `contains_phi`, `short_circuited`, `response_kind`, `deterministic_flags`,
`gate_latency_s`).
