# Graph Stage Ablations — are the runtime evaluator stages required?

**Date:** 2026-08-19 · **Engine:** graph (`HC_RAG_ENGINE=graph`) · **Baseline:** `graph-luna-terra-888a223d` (parity PASS, tag `parity-baseline`)
**Protocol:** `HC_RAG_DISABLE_STAGES=<stage> LANGSMITH_TRACING=false`, judges ON, `--no-sync --concurrency 10`, golden set ×3 reps (n=86 scored rows). Reports: `abl-graph-no-{stage}-*.json` (this dir). LangSmith experiments deleted after local export (cost directive).

## Verdict per stage

| Stage | Keep? | Correctness | Key deltas vs baseline | Cost/latency saved by removing |
|---|---|---|---|---|
| **evaluate** (doc evaluator) | **REQUIRED** — strongest | 0.855 → **0.741** (−0.114) | chunk_recall −0.118, hallucinated +0.146, groundedness −0.051 | $0.003/q, 3.6 s p50 — not worth it |
| **clarify** | **REQUIRED** — pure win | 0.855 → **0.814** (−0.041) | drops on BOTH splits (core −0.035, holdout −0.047) | **none** (p50 flat; llm_calls +0.1 — unclarified queries trigger more refine loops) |
| **decompose** | **REQUIRED for complex** | 0.855 → 0.838 (−0.017) | by-split: core **+0.058** (0.878→0.936) but holdout **−0.100** (0.829→0.729); `decomposed_*`/`synthesized` branches verified absent | $0.000/q, 2.2 s p50; hurts simple queries, saves the multi-part ones it exists for |
| **validate** (answer validator) | **REQUIRED for guardrails** | 0.855 → 0.852 (−0.003) | `false_premise_corrected` 1.000→0.875 (its designed job), hallucinated +0.081, correct_but_ungrounded +0.060 | **the expensive one**: ~90% of $ (0.017→0.002/q), p50 15.3→10.1 s. The only stage with a genuine cost/quality tradeoff — guardrails win for healthcare |
| **followups** | **Answer-neutral** — keep as UX | 0.855 → **0.855** (±0.000) | groundedness/chunk_recall flat | $0.001/q, 2.3 s p50. Golden set does not score follow-up *suggestion* quality — this proves "no harm to answers", not "no value". Sole candidate for templating if cost ever matters |

## Answer to "are the runtime evaluators even required?"

**Yes — both.** The **document evaluator** is the single largest quality lever in the graph (−0.114 correctness, −0.118 chunk_recall when removed; it drives branch ranking and the refine loop). The **answer validator** is quality-neutral on scored correctness but is the graph's hallucination backstop: without it, false premises stop being corrected and ungrounded-but-plausible answers ship. Its cost dominance (≈90% of spend, ~5 s p50) is the price of that backstop — if it ever needs optimising, the lever is making the validator cheaper (smaller model / batched), not removing it.

**Noise caveat:** `false_premise_corrected` 1.000→0.875 appears in 4/5 ablations including no-followups (which cannot affect it) — that is **one judge flip on an n=8 category**, i.e. judge noise scale, not signal. The validate-stage verdict rests on the *convergent* direction of false_premise + hallucinated + correct_but_ungrounded, not the 0.125 alone.

## Stage-disable verification (structural)

| Run | llm_calls/q | n_branches | Proof knob took effect |
|---|---|---|---|
| baseline | 7.26 | 1.74 | branch_types: initial/clarified/decomposed_0-2/synthesized |
| no-validate | 6.34 | 1.69 | −0.92 calls ≈ validator |
| no-evaluate | 5.42 | 1.74 | −1.84 calls ≈ evaluator × branches |
| no-decompose | 5.93 | **0.72** | `decomposed_*`+`synthesized` branches absent |
| no-clarify | 7.35 | 1.73 | `clarified` branches absent |
| no-followups | 6.40 | 1.66 | −0.86 calls ≈ follow-up generator |
