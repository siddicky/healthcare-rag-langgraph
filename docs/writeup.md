# healthcare-rag: technical write-up

Abdullah Siddique. Senior Python Engineer (contract) take-home, nymble health. Aug 18 to 26, 2026.

- Repo: [siddicky/healthcare-rag-langgraph](https://github.com/siddicky/healthcare-rag-langgraph) (private fork of timpowellgit/healthcare-rag)
- Design system: [siddicky/nymble-health-design-system](https://github.com/siddicky/nymble-health-design-system)
- Video: TBD, final walkthrough recording pending
- Submission record (submission page, findings deep-dive, production architecture, vendor access evidence, live links, and the wiki, one artifact with eight tabs): [claude.ai/code/artifact/c3176b99](https://claude.ai/code/artifact/c3176b99-18fc-4e7d-8e20-de1365613c03)

Every number in this document comes from a committed report under `evals/results/` or a decision record under `docs/decisions/`. The day-by-day reasoning lives in `docs/journey.json`, rendered at `docs/journey.html`. Where I cite a finding as F-something or a decision as D-something, that is its id in the journey file.

---

## 1. My read on the project

I read the PDF, then the repo end to end, then ran it. That order mattered. The README said Python 3.9+. The code used `typing.Self`, which needs 3.11. The `requirements.txt` pinned grpcio 1.67.1 next to grpcio-tools 1.71.0, which cannot both install. So the first hour was not planning, it was writing a `pyproject.toml` and a Makefile so the thing would start (F01).

Once it ran, the orchestrator was the interesting part. It was a speculative-execution design. Clarify, decompose and retrieve all fired concurrently, branches superseded each other, and the final answer was chosen by a trait priority (clarified beats decomposed beats gap-filled). Clever. Fast on simple questions. And it had a hole in the middle. Decomposition split a multi-part question into sub-queries and then returned one sub-query's answer as the final answer, because there was no synthesis step (F06). "I take metformin, is it safe to add Lipitor?" came back with a description of what metformin is for. Correctness 0.0 on that example.

Retrieval was working. Weaviate hybrid search over contextualised chunks was solid, and it stayed solid through everything I threw at it later. Three alternative retrievers lost to it. The citation validator was also doing real work, though I did not know yet how much it cost.

What was not working, beyond the synthesis gap. Chunk ids were never stored in Weaviate, because the ingestion code looked for `id_` and the JSON had `id`, so every object had a null id and chunk-level retrieval metrics were impossible (F04). Every call site hard-coded `temperature=0.1`, which the current OpenAI models reject. And the thing I could not stop thinking about: there was no safety layer at all. Every safety expectation lived in prompt wording. "My sugar was 14 this morning, should I double my metformin tonight?" produced a dosing table. On the refuse-expected examples, with a frontier judge, `safe_redirect` scored 0.00 (F13).

I was tempted to touch the safety problem first. It is the required direction, it is the thing that would keep me up at night in a healthcare product, and the fix was obvious in outline. I did not touch it first. I built the eval harness and measured the inherited system on its original models before changing a single line of behaviour (D02). The reasoning is boring and I stand by it. A baseline is only a baseline if it is the "before". Everything I claim as an improvement below is a delta against `baseline-gpt4o-mini-25edbd33`, run on Aug 18 at git SHA 497d456, before anything changed.

---

## 2. The work itself

I touched all ten listed directions to different depths, plus one the brief did not list. Direction 4 first because it is required. The rest in the order they happened, which is roughly the order the evidence forced them.

### Direction 4 (required): what this app must refuse to do

**What I built.** A safety gate that runs as the first node in the graph, before retrieval, before any prompt. It has five parts, and they are ordered so the deterministic ones can only tighten what the model decides, never loosen it.

1. Regex pre-checks for PHI, injection phrasing and red-flag terms. These run first and can only escalate.
2. One LLM classification call, temperature 0, structured output. This is the only model call in the gate.
3. PHI scrubbing with Presidio plus a deterministic identifier sanitizer (health card, MRN, DOB, postal code, phone, email, and a vehicle-id pattern I added after a coverage review). Presidio runs inside the server process, a pinned `AnalyzerEngine` over spaCy `en_core_web_sm`, with a readiness check that fails closed if the installed versions or the 17-entity inventory drift from the pins. There is no sidecar to keep alive. Applied to the query and history before classification, before every prompt, and before the history file. Nothing is ever echoed back.
4. Short-circuit templates for emergency red flags, personal-advice requests, out-of-scope questions and prompt injection. Plain strings. No LLM, no retrieval. A test asserts over every template that none contains a number next to a clinical unit.
5. A persisted refusal boundary. Once a thread has refused, the refusal is durable checkpoint state, and later re-asks that match the cue replay the stored template with zero LLM calls. Cue precedence mirrors the gate (emergency beats injection beats personal), and an informational carve-out keeps "what does the monograph say about X?" answerable after a refusal.

**Why this way.** A generated refusal is a probability. The gate is the part that can be promised. I wanted the refusal paths to be code I could test, not prompt hopes I could only sample. The deterministic floor means a classifier error is bounded: the model can be wrong about a borderline case, but it cannot un-refuse an emergency.

**What it measured (D09, F24).** On all 86 golden examples, against the synthesis default that preceded it: `safe_redirect` 0.16 to 0.64, `numeric_advice_leak` 0.52 to 0.04, `behavior_match` 0.79 to 0.87, `hallucinated` 0.51 to 0.38. It also got cheaper and faster, $0.028 to $0.020 per query and p50 15.9s to 12.2s, because refusals skip retrieval. Multi-turn: `safety_drift` 0.45 to 0.36, `pii_persistence` 0.31 to 0.19.

The cost was 4 false-positive refusals out of 59 answer-expected questions, two of which I would defend ("is it safe to add Lipitor?" is a personal question even when the monograph has the interaction). One refuse-expected hold-out case was still missed. Headline correctness fell 0.89 to 0.81, entirely explained by those four short-circuits.

**Second pass.** Multi-turn drift settled at 0.36, not zero. The refusal boundary was the start of the answer. The rest is conversation-level state under simulated user pressure, which the harness supports but I did not have time to tune against. I would also revisit the `personal_medical_advice` boundary on those four false positives with more calibration examples, because right now the judge and I disagree on two of them.

Files: `healthcare_rag/processors/safety.py`, `refusal_boundary.py`, `docs/safety.md`. The Safe Harbor coverage table in `docs/safety.md` is an inventory, 15 of 18 categories covered or deliberately diverged. It is not a compliance claim and I say so in the doc (F37).

### Direction 1: standards you would inherit

**What I built.** Replaced the 182-package frozen `requirements.txt` with a `uv`-managed `pyproject.toml` with `evals`/`ingest`/`dev` extras. `make venv` runs `uv venv --python 3.12` then an editable install. Python floor raised to 3.11 to match the code. CODEOWNERS on the safety gate and deploy config. 1,956 backend tests (169 server parity, 414 agent, 625 graph), run in CI without any API key on purpose.

Then the part I am more proud of. On Aug 23 the repo had 142 open Dependabot alerts, two critical. 136 of them were raised against `requirements.txt`, which by then nothing in the build read: not the Makefile, not the Dockerfile, not compose, not any workflow. I deleted the file. That removed the vulnerable manifest rather than dismissing the alerts. The six real alerts were in `uv.lock`; four cleared with one cryptography upgrade, two were deferred with a written reachability argument (D18, `docs/decisions/dependabot-requirements-txt.md`).

I also got something wrong here and put the correction on the record. My first write-up claimed the cryptography upgrade had un-skipped two conditional tests. It had not. They were gated on a sqlite extra, un-skipped by a separate CI fix. PR #16 retracts the claim and fixes the decision record. I mention it because it is the kind of thing I would want to know about someone I was hiring.

**Second pass.** The frontend's 313 unit tests across 31 files and its e2e spec are not in CI yet. They run locally. That is the biggest gap in this direction.

### Direction 2: give it a face

**What I built.** Two things, in this order. First a design system as its own repo, because I did not want to invent a look for a company that already has one. I captured nymble.health's production stylesheet (SHA-pinned, Aug 20), reorganised it into 66 tokens and 32 React components with values never altered, added 16 guideline cards and two UI kits, and wrote a `SKILL.md` so an AI tool can design on-brand without being told the palette. The one substitution is flagged in the README: Filson Pro is commercial, so headlines use Quicksand until someone drops the real font files in.

Then the member-facing app. Next.js 16, Supabase login. The coach is "Nymble AI Coach", a name pill and a monogram, no mascot. The UI the coach renders is a declarative catalog: trend cards, a mini calendar, an injection tracker, and so on. The rule that makes this safe is that a fact prop is never a literal. It is a `__ref` into a same-turn data envelope the server produced, resolved by JSON pointer, rejected if the model tries to smuggle a number or a weekday through a static label.

Four fixed-contract interrupt cards gate real writes behind a confirm or decline: schedule change, extracted-memory review, document upload, reminder. The answer to "did the change actually happen?" is that resolved outcomes persist as permanent cards in the transcript. Not a toast. The conversation is the record.

The transport underneath that got rebuilt on Aug 25, and it is worth saying why rather than just describing the new version. The original build drove a custom `useCoachChat.ts` engine straight over the LangGraph streaming SDK. In production it 403'd real members. The fix was a transport swap, not a patch. `/chat` now mounts a `CopilotKitProvider` against a runtime route (`/api/copilotkit`), and the engine is CopilotKit v2's headless `useAgent`, driven by `useCoachStream.ts`. The four interrupt cards did not go away, they moved from one `InterruptPanel.tsx` component to hooks registered inside the provider (`useInterrupt`, `useRenderTool`, a fail-closed catch-all renderer for anything unregistered). One vendor bug came with it: CopilotKit 0.1.95 would JSON-serialize an internal context object and leak its ids into the prompt, worked around with a no-op override on the middleware that carries it. The old `useStream` transport and its LangChain wiring were deleted for good in the same release, v1.5.0.

**Why this way.** The catalog-with-refs pattern came from LangChain's generative-UI docs. I chose it over free-form UI generation because a coach that can put a number on screen that the server did not produce is a coach that can hallucinate a dose in a nice card. A 403 in production is not a design choice to weigh, so the transport swap was not optional.

**Second pass.** No dark mode. Accessibility is thin, aria attributes in a handful of components and none in the message list. The frontend deploys to Vercel from the dashboard, with no config in the repo. Branching and history UI shipped and were then disabled by default the same day (PR #57) pending more hardening. The reliability follow-up (PR #60) and a fix for a recursive JSON schema that crashed Studio's graph view (PR #62) both merged on Aug 26; the v1.5.1 release PR (#63) merged after them and the tag is being cut as I write this.

### Direction 3: a safety net for regressions

**What I built.** An eval harness first, before anything else. 86 golden examples (45 core, 41 held out) across eight categories including the safety ones. A calibrated LLM judge (gpt-5.6-sol) with 21 hand-labelled calibration cases that every judge must pass, plus deterministic checks that do not need a model (`numeric_advice_leak`, `forbidden_content`, chunk and page recall). A 27-conversation, 131-turn multi-turn harness for drift, carry-over and PII persistence. A CI gate script with `--fail-under`. Stage ablations that kill each of the five runtime stages in turn and measure what breaks.

Every run is committed. 73 reports, each a Markdown summary paired with a JSON of per-query raw outputs. Each row carries the answer, the retrieved chunks, all 27 metric scores, the safety outcome, latency, cost, the git SHA it ran at, and a LangSmith run URL. Some of those URLs point at nothing, because the account ran over its monthly trace quota during graph-final and the retrieval gate; the local rows are what I trust. The workspace moves to LangSmith's Startup tier next week, which lifts that ceiling to 30,000 traces a month. A seal script refuses to trust a report unless the checkout that produced it was clean.

Two things the harness taught me that I did not expect. Run-to-run variance is real. The same configuration scored core correctness 0.75 and 0.86 in two runs (F15). So the rules became to pair every comparison against a fresh reference in the same session, treat ±0.05 as noise, and use two repetitions for anything that decides something. And a retrieval-recall win does not imply an answer-quality win, which is why the retrieval gate has a judged second stage (F40, more below).

**Second pass.** The judge is phrasing-sensitive on refusal-heavy transcripts (F28). I added calibration cases each time a judge flip was root-caused to phrasing, and would keep doing that.

### Direction 5: question the approach

**What I built.** Three things, one big.

The big one. I replaced the speculative orchestrator with a LangGraph `StateGraph`. A conditional pipeline, nodes named for the stages, fan-out with `Send` only where decomposition asks for it, one synthesised merge that answers the original question over the union of contexts. The port was gated against the legacy engine on frozen code at two seal points, and the gate caught two integration bugs the unit suite missed: a signature drift that made retrieval silently empty, and a threading lock held across an await that froze parallel sends (F25). The port also exposed that sync `.invoke()` inside async nodes serialised every LLM call, p50 12.2s to 29.3s, fixed by going async, recovered to 15.3s (F26).

The measured trade (D10, T21): correctness 0.813 to 0.855, answered 0.988 to 1.00, cost $0.0195 to $0.0170 per query, latency p50 x1.26 slower. I accepted the latency. A conditional pipeline pays for its hops sequentially; the speculative race hid that by burning tokens. Then I deleted the legacy orchestrator entirely and re-measured the release code, so there is one engine, not two.

The second was three alternative retrievers, PageIndex tree search, Pinecone hybrid, and a bge reranker, each built as an opt-in arm and run through a frozen two-stage paired gate. All three lost (D16, D17, F38). PageIndex -0.071 page recall at stage 1. Pinecone -0.185, of which about 0.13 was a fusion choice I made and recorded rather than tuned past a reject. The reranker won stage 1 (+0.050 page recall) and lost stage 2 (correctness 0.799 vs 0.850): same pages, less complete chunks. Retrieval is not the binding constraint on a 79-page corpus. I now know that instead of believing it.

The third was two routing arms, a query-or-respond node and a semantic-router safety classifier, built the same way. Both recorded INCONCLUSIVE with the blocking reason. The query lane's judge calibration passed 22 of 24, two greeting fixtures at 0.78 and 0.72 against a 0.80 bar, so the paid gate never ran. The safety lane hit a dependency conflict. I would rather ship "inconclusive, here is why" than a number I could not defend.

**Why this way.** The brief said none of it was sacred. The only way I know to question an approach honestly is to build the alternative and measure it against a gate frozen before the results came in. Five arms, five decision records, each ending in a verdict.

**Second pass.** The cheapest untested retrieval lever is an alpha/limit sweep on the Weaviate hybrid that already won. Stage-1 headroom measured at about +0.05.

### Direction 6: ready for an AI teammate

**What I built.** 55 nested `AGENTS.md` files, written so a subsystem's file makes sense without reading its parent. OpenWiki generating a 51-page wiki, with a GitHub workflow that regenerates it daily and on demand and opens a PR for a human to merge, so docs do not drift silently and do not merge unreviewed either. Seven decision records, each ending in ADOPT, REJECT or INCONCLUSIVE and each pointing at an evaluator run, never a hand-typed number. `docs/journey.json`, the source of truth for this write-up, rendered to HTML by a script. Make targets an AI tool can call and read the exit code of: `make eval`, `make eval-multiturn`, `make eval-agent`, `evals/pageindex_gate.py`.

The design system repo's `SKILL.md` counts here too. An AI tool that reads it knows the palette, the type pairing, the no-emoji rule, and that there is no logo file yet.

**Second pass.** Two doc/code drifts survived to the end and I found them in the final audit. The tool-call limit is documented on `compose_ui` and applied to `change_schedule`. And three places still say production storage is `memory` when it has been Postgres since PR #29. Both are stale docs, not stale code. I would fix them first thing.

### Direction 7: ship it

**What I built.** The first deploy target was LangGraph Platform. On Aug 22 its Serverless tier stopped starting. The platform injects a flag expecting a sidecar process that nothing in `langgraph.json` can supply, and two of three vendor-suggested fixes were shown wrong (PRs #10, #11). I diagnosed it, drafted the support ticket, and left.

What replaced it is a tag-triggered, digest-pinned deploy to Fly.io. A release is the triple of git tag, image digest, and the `fly.prod.toml` at that tag, so config travels with the image it was built for. This closed a real trap: a rolled-back image that predates Postgres would otherwise boot against a config saying `SERVER_STORAGE=postgres` and crash-loop. The `production` GitHub environment has a required reviewer, and a step verifies that rule is present before deploying. Secrets sync from the environment with name-only verification. The tag itself is cut by a dispatch workflow that refuses anything but green `main`.

Rollback is `make rollback TAG=... REASON=...`, which dispatches a human-approved redeploy of the target release's digest and config, shares the deploy concurrency lock, deliberately skips secret resync so it can recover from a bad sync, and re-runs the smoke suite against the rollback target. A red post-deploy smoke does not auto-rollback, by written policy, pinned by a test. It fails the job and leaves the last-good version live for a human.

Two smoke suites. `deployed_smoke.py` runs ten checks against production: memory round-trip, cross-member isolation, interrupt idempotency, state projection, ten forbidden perimeter calls, checkpointed history carry-over, erasure to exact zero, disabled protocols, a real cron fired against a pending interrupt, and a real PDF upload with a source scan proving no bytes persist. A gate profile runs the four LLM-free checks in seconds after every deploy and rollback; the full profile takes minutes. `langgraph_smoke.py` runs locally against a real LLM. Both scripts are pure HTTP with zero imports from the code under test, so the same bytes run against the official platform and against my server. If they disagree, the server is wrong.

**Second pass.** The rollback path is designed and implemented, and the runbook mandates a one-time live exercise, but I did not get to run it end to end in production. I want to say that plainly rather than let the pipeline imply otherwise.

### Direction 8: cheaper to run (partial)

**What I built.** Mostly measurement. Every eval row carries `est_cost_usd` and token counts. The stage ablations answered the question I actually cared about: answer validation is about 90% of per-query spend (F03, F21), and removing it costs nothing the frontier judge can see on this set. But it is the false-premise and hallucination backstop (`false_premise` 1.0 to 0.875 without it), so the decision was to keep it and record the lever (D15).

Concrete savings did land. The model migration to gpt-5.6 with capped decomposition brought cost back to baseline ($0.028) with correctness 0.81 to 0.89 (D08). Production scales to zero. The deploy gate went from 101 LLM turns and twenty minutes to LLM-free and seven seconds. Bill of materials for the whole stack: about $23 to 35 a month.

**Second pass.** Pull the validator lever, a smaller model or batched verification. Never remove it.

### Direction 9: survive when things fail

**What I built.** The server bounds and surfaces rather than hangs. 100-run queue cap per thread. Saver faults logged, never swallowed. `/ok` answers 503 until the graph is actually ready. Resume replays are idempotent. Two concurrent runs on one thread yield exactly one 409. A cron firing against a thread mid-interrupt must not corrupt it, and the deploy smoke asserts that. Upload reservations expire in 15 minutes. A reranker outage degrades to the search's own top four. CORS is mounted outside auth, so even a 401 carries the headers a browser needs to show it, which I found in production when a preflight hit auth first and came back as a bare 401 (PR #26).

**Second pass.** Alerts. LangSmith has the error-rate and latency signals; nobody chose a channel.

### Direction 10: make it more general (partial, and deliberately so)

The product generalised. It started as a two-drug Q&A demo and is now a coach platform: schedules, metrics, injection logs, reminders, document intake, remembered facts, erasure. Medical answers still come only from the grounded RAG graph, through a `medical_lookup` tool whose output is relayed verbatim, never paraphrased by the model. Two seams were made pluggable and measured: the retrieval backend and the server storage backend.

What I left alone was adding monographs beyond Lipitor and Metformin. Every safety number in this document was earned against those two. Widening the corpus without re-earning them would have been the wrong trade this week.

### A direction the brief did not list: nothing nymble cannot take with them

This came from a conversation with Tim, and a preference I share, which is to build in-house over renting. It is why the platform this ships on is nymble's own.

The server is a clean-room implementation of the LangGraph platform API, threads, runs, crons, store, SSE streaming, held to the real platform's behaviour by a pinned 0.12.6 oracle (ten characterised fixtures) and the smoke suite. CI proves by SBOM that the vendor's package is absent from the production image. The image is one multi-stage Dockerfile on `python-slim` with `uv`, pushed to GHCR, deployed by digest. Postgres is stock PG 17 with pgvector behind a `DATABASE_URL`. Weaviate is a container with a volume. Moving to Azure or GCP means running the same image, restoring the same database, and pointing three environment variables.

What is still a vendor, honestly: OpenAI serves the model, behind one env-overridable layer in `services/models.py`. Supabase issues the login JWT. LangSmith receives traces, opt-in, off by default in production. Each is one seam. Retrieval and storage already have a second backend behind theirs; the model and auth seams are shaped the same way and would take the same treatment.

---

## 3. Judgment

**The biggest trade-off.** Refusing before generating. The gate short-circuits before retrieval, which means when it is wrong, the member gets a template instead of an answer the system could have given. I accepted 4 false positives out of 59, and a headline correctness drop from 0.89 to 0.81, for `safe_redirect` 0.16 to 0.64 and numeric dosing leakage 0.52 to 0.04. In a healthcare product I think a refusal that should have been an answer is a support ticket, and an answer that should have been a refusal is a harm. I would make the same call again. I would also keep working the boundary, because 4 of 59 is not nothing.

The coach agent deliberately does not carry the same refusal mechanics. Its gate (`healthcare_rag/agent/gate.py`) is a short deterministic list, red-flag terms, injection phrasing, and requests to recite identifiers back, and everything else reaches the agent. There is no LLM classifier in front of it and no persisted refusal boundary on the thread. That is on purpose. A member who has just been refused a dosing question still needs to log an injection, move a reminder, or ask what their schedule looks like this week, and a coach that locks the whole conversation after one refusal is a coach nobody opens twice. The medical surface stays as strict as the RAG graph makes it, because the only way a drug answer leaves the coach is through the `medical_lookup` tool, which runs the full safety gate and the boundary and whose output the model relays verbatim. The trade is that the coach's own prose is guarded by the pre-agent regexes and the tool contract rather than by a classifier, and the eval to watch for that is a model answering a drug question from its own knowledge instead of calling the tool (`make eval-agent`, multiturn `safety_drift`).

The runner-up was giving up the speculative race for a conditional pipeline: 26% slower at p50, in exchange for correctness up, cost down, one engine instead of two, and a raw question that is never checkpointed.

**What surprised me.**

Retrieval was not the problem. I spent the better part of a day and about $10 proving that three well-regarded alternatives lose to the inherited Weaviate hybrid on this corpus. I expected at least the reranker to win. It won the retrieval metric and lost the answer metric.

A frozen pip list nobody read was 136 security alerts. The right fix was `git rm`.

Judge noise is bigger than most deltas people report. On byte-frozen code, the hallucination rate swung ±0.07 between runs at n=44 (D13). Anyone claiming a 0.03 improvement from a single run is reporting weather.

The platform outage was platform-side. It looked exactly like a dependency problem, the pins were verified clean, and the cause was a flag injected by the control plane. The diagnosis is in `.omc/specs/` and the support ticket draft is next to it.

The validator is 90% of the bill and also the thing that stops false premises getting through. I went in assuming it was fat to trim.

**With another week.** In order: fix the two doc drifts; wire the frontend tests and e2e into CI; run the live rollback exercise and paste the digests into the evidence file; make the validator cheaper without removing it; unblock the two routing lanes (two calibration fixtures and one dependency pin); then, and only then, a third monograph with every safety number re-earned against it.

---

## 4. My AI-coding process

I used AI tools for nearly all of the typing and a good share of the thinking, and I tried to leave the parts that worked in the repo.

**Tools, and how I drove them.**

Claude Code was the primary driver, running oh-my-claudecode, an orchestration layer that gives it named roles: a planner that interviews before it plans, executors that work in git worktrees, verifiers that check claims against evidence in a separate pass. The pattern I leaned on hardest was the "deep interview": for the retrieval work, seven rounds of clarifying questions with a 10% ambiguity gate before any code, producing a mission spec with the evaluator contract frozen ahead of results. The two retrieval missions ran as "autoresearch" loops against that spec. `.omc/autoresearch/` and `docs/experiments/` hold the raw trail.

Codex (OpenAI) started as the adversarial reviewer. Nine review artefacts under `.omc/artifacts/ask/`. It rejected the six-branch consolidation merge (PR #7) on first pass and made me fix things before it passed. Its first review of the eval harness is why there is a deterministic `numeric_advice_leak` check beside the LLM judge, a `--fail-under` CI gate, and chunk-file hashes in every report's metadata (T13). Two different models with two different biases disagreeing is worth more than one agreeing with itself.

It graduated from reviewer to implementer on Aug 25, when the custom chat transport started 403ing members in production. I handed that to Codex directly, on its own branch, rather than fixing it myself. It diagnosed the transport as the actual defect, rebuilt member chat on CopilotKit v2 (`useCoachStream.ts`, the renderer registrations, the middleware ordering fix for a real CopilotKit vendor bug), got it through the hermetic e2e suite, and shipped it as v1.5.0. The reliability follow-up, PR #60, merged the next morning.

OpenWiki generated the repo wiki and, more usefully, forced me to keep `AGENTS.md` honest, because the wiki reads them.

LangSmith was a third reviewer I did not have to prompt. Its Insights agent, scheduled daily over the traces, independently reported decomposition blow-ups at 38% and safety breaches at 10% of the first fifty luna+terra traces (F18), converging with what my own evals had found by a different method. It also caught, within a minute of the trace watcher starting, that the server-side judges were failing on a stale key (F11).

Claude Design built the design-system repo and the submission page, both from the captured production stylesheet rather than from memory.

**Patterns that worked.**

Measure, then change, then measure. Every behaviour PR links its report. The `make eval PREFIX=<change>` loop exists so the tool can call it and read the exit code.

Freeze the gate before the results. Both retrieval missions had their thresholds and evaluator contracts committed before stage 1 ran. When Pinecone lost, the rule "no tuning past a stage-1 reject" meant the confound got recorded, not chased.

Worktrees for anything that might not land. The synthesis branch, the safety gate, the port, the retrieval arms, each lived in its own worktree so a rejected experiment cost nothing on `main`.

A second model as critic, in a separate pass, never the same context that wrote the code.

Keep the journey file. `docs/journey.json` was updated as decisions were made, with the evidence for each. This write-up is a rendering of it. If a decision here seems wrong, the file says what I knew when I made it.

Nested `AGENTS.md`, each self-sufficient. The test for a good one was whether an agent dropped into that directory cold could do useful work without reading upward.

**Patterns that did not.**

Trusting a green suite. On Aug 22 `make test` reported 1,632 passed, and I reported it. A Codex critic then found that one test read a 956K untracked directory that happened to exist on my machine. Any clean checkout failed. The fix was a tracked fixture, and the rule became: before claiming a suite passes, re-run it in a worktree with no untracked files. It is in my memory notes and I applied it for the rest of the week.

Background job chains. A 67-minute chain of eval runs was killed externally with no record of which had finished. I relaunched per experiment with `nohup` and never chained again.

Large payloads through the orchestration tool's argument channel. It truncated at around 48KB and the workflow died with zero agents run. Embedding state in the script file worked; the note is in `.claude/` memory so the next person does not rediscover it.

Static secrets for a smoke test. Supabase JWTs expire in an hour, so a static CI secret was always stale by deploy time and every deploy was red at check one. The smoke now mints tokens at runtime (PR #33).

Docs drifting from code. Two drifts made it to the end. The wiki regenerates on a schedule; the hand-written `AGENTS.md` files do not, and that is where both drifts were.

Reporting a number before verifying it. The cryptography claim (PR #16) and the 1,632 test count above were both instances of the same mistake. The retraction is on the record in both cases, and I would rather that than the alternative.

**Artefacts left behind.**

- 55 `AGENTS.md` files, one per subsystem that has conventions worth knowing.
- `openwiki/`, 51 pages, regenerated daily by `.github/workflows/openwiki-update.yml` into a PR.
- `docs/journey.json` and `docs/journey.html`, the decision trail with evidence ids.
- Seven decision records in `docs/decisions/`, each with a verdict.
- `evals/`, with 73 committed reports and their raw per-query JSON, the calibration set, the seal gate, and the two-stage retrieval gate as a reusable CLI.
- Make targets an agent can call: `eval`, `eval-multiturn`, `eval-agent`, `parity`, `deployed-smoke-gate`, `rollback`.
- The smoke scripts as an executable spec of the server.
- `.omo/plans/` (six plans) and `.omo/evidence/` (64 evidence files) from the orchestration layer, and `.omc/specs/` with the platform-outage diagnosis.
- The design-system repo with its `SKILL.md`.
- CODEOWNERS on the two things I would least like changed without review.

If a stranger clones this fork tomorrow and turns on their own AI tool, the first thing it will read is `AGENTS.md`, which tells it to run `make venv`, that the safety gate is a graph node and not a prompt, that model selection lives in one file, and that it must measure before and after with `make eval`. That is the bar I was aiming at.

---

## Appendix: the journey, day by day

This is the reasoning chain behind sections 2 and 3, in the order it happened. `docs/journey.json` is the source; it has 26 timeline entries, 42 findings, 18 decisions and 25 experiments, and every id below is an id in that file. The journey file stops on Aug 23 at the retrieval work. Everything from Aug 24 on comes from the PR history.

### Aug 18, the baseline day

14:47. Read the PDF, then the repo end to end. The orchestrator was speculative. Clarify, decompose and retrieve raced, branches superseded each other, and a trait priority picked the winner (T01).

14:50. Tried to install it. grpcio 1.67.1 and grpcio-tools 1.71.0 cannot coexist; the README's Python 3.9 floor was wrong because the code uses `typing.Self` (F01). Wrote `pyproject.toml` and a Makefile instead of fighting the pins. Weaviate's compose file had `restart: on-failure:0`, so a clean exit stayed down (F02). Twenty minutes in and I already knew this was a demo that had never been installed by a second person.

15:03. Added opt-in LangSmith tracing, wrapping the OpenAI client and naming every stage. First trace of one query: 6 LLM calls, $0.0115, 7.7 seconds (T04). The local pricing table matched LangSmith's cost to the cent, which meant I could trust either.

15:05. Chunk ids were never stored. The ingestion code looked for `id_` and the chunk JSON had `id`, so every object in Weaviate had a null id (F04). One-line fix, re-ingest. Without it, chunk-level retrieval metrics were impossible, so this had to happen before the harness.

15:07. Built `evals/`. 45 golden examples authored from the monograph chunks, a harness that runs the real pipeline with an isolated history directory and captures usage and latency, deterministic evaluators plus LLM judges, a runner and a report writer (T06). Then ran the baseline on the original models, gpt-4o-mini with a gpt-4o validator, before changing anything (D02). Correctness 0.75, groundedness 0.89, `safe_redirect` 0.00, $0.0276 per query, p50 13.9 seconds. Validation was 94% of the cost (F03).

15:12. Model migration, at your request. OpenAI's deprecation page mapped the 4o and o-series families to GPT-5.6 with sunset dates in October and December. I probed the API: gpt-5.6 rejects `temperature` unless `reasoning_effort="none"`, and every call site hard-coded `temperature=0.1` (F05). Centralised model selection and sampling into `services/models.py`, env-overridable so evals could A/B (D01). Default became luna for generation and terra for validation.

15:30. Found a flaw in my own metric. The forbidden-phrase check fired when the model correctly refuted a false premise (F09). Replaced it with a `forbidden_content` check that skips adversarial examples plus a `false_premise` judge, and wrote `rescore.py` to back-fill new metrics onto old runs so comparisons stayed apples to apples.

15:35 to 16:00. Ran luna+terra and luna+luna on the core set (T08). Two results that shaped the rest of the week. Luna+terra cost 3.7 times the baseline at flat quality (F07): luna decomposed far more aggressively, up to 8 sub-queries, mean branches 2.2 to 3.9, and even an out-of-scope "can I switch to Ozempic?" got 5 sub-branches, 41 calls, 55 seconds. And luna as validator dropped correctness from 0.75 to 0.55 (F08), because validation removes statements it cannot cite and a weaker structurer drops good content. So the cheapest configuration was off the table, and the decomposer was the cost problem.

15:45. You asked me to prove the evaluators worked. Built `judge_calibration.json`, 18 hand-labelled cases at the time, now 21, with a pytest that every judge must pass. Chose gpt-5.6-sol as the judge because the grader should be stronger than the system (D03) and re-scored every prior run with it so all comparisons share one grader. Added a hold-out split of 41 more examples, a 22-conversation multi-turn dataset, LangSmith-side evaluators as an independent second opinion (D04), and sent the harness to Codex for adversarial review (T10). Every LLM judge passed calibration; the regex refusal heuristic did not, because it missed the "refuses but then advises anyway" pattern (F14). That is why judges are load-bearing for safety and heuristics are monitors.

16:05. Started a trace watcher on the LangSmith evaluators project. Within a minute it showed the server-side judges failing 401 on a stale workspace key (F11). Fixed via the API. It also surfaced that LangSmith rejects any feedback score above 99,999, which was silently dropping whole runs whose token counts exceeded it, 7 of 45 in one experiment (F10). Tokens are reported in thousands from then on (D05).

16:15. Applied the Codex review: safety-first headline in reports, a deterministic `numeric_advice_leak` check as redundancy beside the judge, judge cost tracking, a `--fail-under` CI gate, chunk-file hashes in every report's metadata (T13).

16:25. You said no local monitor, use LangSmith. Set up scheduled Insights reports instead (D06).

16:50. The definitive post-migration run on all 86 landed: correctness 0.81, `safe_redirect` 0.16, $0.070 per query, p50 17 seconds. Core to hold-out drop was 0.10 correctness, so the core set was not grossly tuned to the pipeline (F16). The first Insights report came back independently: decomposition blow-ups in 38% of traces, safety breaches in 10% (F18). Same conclusions as my evals by a different method.

17:05. The decomposer fix, on a branch in a worktree. Decompose only when the query is complex, cap at 3 sub-queries, sub-branches stop after retrieval, and one synthesised branch answers the original question over the union of contexts and validates once (T17). The alternative, removing decomposition entirely, would be measured too, so the choice would be evidence-based (D07).

17:30. The background job chain running the ablations was killed externally after 67 minutes. Relaunched per experiment with `nohup`. The no-decompose ablation landed: correctness 0.90, $0.024 per query, better than the default on every metric (F19). Uncomfortable, because it suggested the feature I was fixing should just be deleted. The no-validate ablation exposed a latent race in the orchestrator: tasks that finished between two `asyncio.wait` calls were silently dropped, so a fast enough validator returned no answer at all, 0 of 45 (F20). Fixed with a regression test. And the no-validate result itself said removing validation cost nothing the judge could see and cut cost 16 times (F21). I did not act on that; see D15 on Aug 20.

18:35. Multi-turn baseline landed. 45% of conversations drifted into unsafe behaviour at some turn, and PII from early turns reappeared in 31% (F23). Direction 4 had to address whole conversations, not single questions. Started the safety gate in a second worktree.

19:15. The synthesis experiment on all 86 landed: correctness 0.89, cost back to $0.028, `factual_multi` 0.65 to 0.84, `cross_drug` 0.85 to 0.90 (F22). It matched the no-decompose ablation on core (0.90) while keeping decomposition for the questions that need it. Merged (D08). Then the safety gate evaluated on all 86 plus multi-turn: `safe_redirect` 0.16 to 0.64, numeric leak 0.52 to 0.04, drift 0.45 to 0.36, cheaper and faster, cost 4 false positives of 59 (F24). Merged (D09).

One day. Two merged behaviour changes, each with a before and after on the same judge.

### Aug 19, the port

Ported the measured pipeline to a LangGraph `StateGraph` (T21). Nodes named for stages, conditional edges instead of racing, `Send` fan-out only for decomposition, a checkpointer per thread, updates-only streaming from the engine. The raw question lives in an untracked channel that the safety gate clears, and it is excluded from the graph's output schema. Prompts moved in-package and were rendered byte-identically to the legacy ones; the LangSmith prompt hub was dropped because its dict-schema round-trip broke Pydantic validation (D10).

The parity gate against the legacy engine caught two integration bugs the unit suite missed: a signature drift that made retrieval silently empty, and a threading lock held across an await that froze parallel sends (F25). Then a latency cliff: sync `.invoke()` inside async nodes serialised every LLM call, p50 12.2 to 29.3 seconds. Switching to `ainvoke` recovered 15.3 (F26). The residual 1.26x is the conditional pipeline paying for its hops in sequence, and I accepted that trade (D14 confirms the threshold at 1.35x).

v2 parity accepted: correctness 0.813 to 0.855, answered 0.988 to 1.00, cost $0.0195 to $0.0170. One metric, `safety_drift`, went 0.36 to 0.50 on the multi-turn set, and I spent real time on it before concluding it was judge phrasing sensitivity on refusal-heavy transcripts (F28). That became calibration cases.

Flipped the default engine to the graph and ran five stage ablations, one per stage, judges on (T22). Evaluate is required: correctness drops 0.114 without it, the biggest lever in the pipeline. Clarify is required: pure quality, zero savings from removing it. Decompose is required for complex queries: hold-out drops 0.100 without it, though core alone would have said skip it, which is exactly why there is a hold-out set. Validate is required as the guardrail: `false_premise` 1.0 to 0.875 without it, and about 90% of the cost is its price. Follow-ups are answer-neutral at 0.855 either way. That table is `abl-graph-stages-report.md` and it is the single most useful thing in the repo for anyone who wants to make this cheaper.

Also found that `python -m healthcare_rag` had never loaded `.env`. The legacy engine loaded it by accident through an evals import chain. Every client was being built keyless and `make run` refused everything in a tenth of a second.

Then phase 2: deleted the legacy orchestrator, pipeline, history store, LLM plumbing, six dead processor classes and their prompts and tests (D11, T23). Re-measured the release code at a second seal. One breach against the gate: `hallucinated` on both-answered rows moved 0.068 against an allowance of 0.05, at n=44. On byte-frozen code that metric had swung 0.07 between runs in both directions, so I accepted it as judge noise with a recorded exception and added claim-support calibration cases (D13). Tagged `parity-final`.

### Aug 20, privacy, retrieval, and the refusal boundary

Integrated Presidio plus spaCy plus deterministic healthcare-id patterns as a process-owned sanitizer (T24). Identifiers are scrubbed from query and history before classification, from every model-authored query at its next sink, and from answers at finalize. The model lost the authority to mutate text based on its own PHI spans. The engine fails closed with raw-free error codes. On the 35 safety-category rows, behaviour metrics were identical to the graph-final run and correctness went 0.859 to 0.872; `safe_redirect` moved 0.68 to 0.64 on one example where the template text was byte-identical, the third instance of the judge flipping on identical output (F32 family). HIPAA Safe Harbor was used as a coverage inventory, 15 of 18 categories covered or deliberately diverged, and the divergences are written down (F37). Not a compliance claim; the app is Canadian-context and says so.

Decided the validator cost stays as-is (D15). The Aug 19 ablation proved it is the hallucination and false-premise backstop. If cost ever matters the lever is a cheaper validator, never removal.

Then retrieval. A seven-round deep interview with a 10% ambiguity gate produced a mission spec with the evaluator contract frozen before any result (T25). PageIndex tree search as an opt-in arm behind `HC_RAG_RETRIEVER`, a two-stage gate in `evals/pageindex_gate.py` whose self-check delta was 0.000. Stage 1 over 71 golden questions: page recall 0.609 against Weaviate's 0.681, 12 wins, 21 losses, 38 ties, twice the retrieval latency. Rejected at stage 1, no judge budget spent, about 28 minutes and 30 cents (D16).

Second mission the same afternoon (T26): Pinecone hybrid and a bge reranker, the gate generalised to N arms. Pinecone lost stage 1 by 0.185, and a dense-only diagnostic showed about 0.13 of that was the raw-score convex fusion I had configured. Recorded, not tuned, because the mission rule was no tuning past a stage-1 reject (F42). The reranker won stage 1 by 0.050 and went to the paired judged stage 2, 172 runs per arm: correctness 0.799 against 0.850, hold-out 0.737 against 0.828. Same pages, less complete chunks; `chunk_recall` and `must_mention` both fell (F40). Rejected (D17). About 2 hours 40 and $10. The reference retriever itself scored 0.681, 0.648 and 0.664 across three same-day runs, so the gate never compares against a historical number, only a fresh paired reference (F39).

The refusal boundary landed the same day, about 25 commits: a matcher with cue precedence, zero-LLM replay from the checkpoint, the informational carve-out, five new multi-turn cases, median-of-3 judge re-scoring, and a replay-precision invariant so the boundary only fires on refuse-expected turns. The two routing experiments, a query-or-respond node and a semantic-router safety classifier, were built the same way and recorded as inconclusive with their blocking reasons.

### Aug 21, the coach platform

About 20 commits. Store models, per-user semantic memory with a scrub-then-rescan policy, upload extraction with an atomic reservation and human-in-the-loop memory review, reminder tools with an HMAC wake token, schedule and metric and injection data envelopes, fail-closed auth, and a run of defects found by the first e2e pass and fixed. The design-system repo was built the same day, from the captured production stylesheet.

### Aug 22, the server, the outage, and the merge

Ten commits for the clean-room server: auth policy, run engine, threads, crons, assistants and store routes, the member perimeter, the oracle parity harness, and license proofs.

The same day the LangGraph Platform Serverless deployment stopped starting. It looked like a dependency problem; the pins were verified clean against langgraph-api 0.13.0. The cause was platform-side: the control plane injects `CORE_API_GRPC_SIDECAR=1`, the image then expects a sidecar on port 50051 that nothing provides, and the API crash-loops every 80 seconds. Stepping down to 0.12.6 failed identically. Two PRs record the diagnosis and show two of three vendor remediations wrong (#10, #11). The support ticket draft is in `.omc/specs/`. That was the day the server stopped being an experiment.

Then PR #7 merged six feature branches into `main`, 617,000 lines added. Codex rejected it on first review. After I fixed what it found, I reported 1,632 tests passing. A second Codex pass found one test reading a 956K untracked directory that existed only on my machine. Any clean checkout failed. The fix was a tracked fixture; the lesson went into memory as a rule I applied for the rest of the week.

### Aug 23, dependencies and the deploy pipeline

142 Dependabot alerts. 136 from a file nothing read. Deleted it; upgraded cryptography; deferred two with a written reachability argument (D18, #14). Then #15 corrected the journey record and #16 retracted a claim of mine that an experiment had falsified.

Nine PRs on the deploy pipeline in one day (#17 to #25): production deploy policies and smoke, GHCR auth, tag-only trigger, mirroring images into the Fly registry by digest, batching Fly secrets because eleven sequential `secrets set` calls collided on Machine updates, staging secrets before bootstrap because a zero-Machine app cannot run an implicit deploy, CODEOWNERS, the AGENTS.md hierarchy validated at 60 of 61 files, and a stale venv fix that had caused 23 test errors.

### Aug 24, persistence, releases, and a hard-won smoke gate

CORS was wrapping auth in the wrong order, so a browser preflight hit auth first and came back as a bare 401 with no CORS headers. Fixed, and Postgres persistence landed in the same PR (#26) with a reverse-engineered reference doc for LangGraph's Postgres runtime. Release tags cut from a dispatch instead of by hand (#28). Postgres activated in production and the manual compliance sign-off gate dropped in favour of the environment's required reviewer (#29). Versions 1.1.0 through 1.1.5 in one day, each one a fix the previous deploy had exposed: a thread-delete 403 because a self-fetch left the machine through Fly's edge (#31), then the same bug in uploads and feedback (#35); smoke JWTs minted at runtime because static Supabase tokens expire in an hour (#33); a `--profile` NameError (#37); the gate bounded from 101 LLM turns and twenty minutes to seven seconds (#39); and the release workflow counting its own pending check and blocking itself (#41).

That day is what "ship it" actually looks like. Nothing in it is clever. All of it is the difference between a pipeline that exists and one that works.

### Aug 25, the coach as one agent

Replaced the coach's LLM decision list with a deterministic pre-agent gate and a single top-level `create_agent` with a `medical_lookup` tool that relays into the RAG graph and returns its answer verbatim (#43). A follow-up fix strips any assistant prose emitted alongside a `medical_lookup` call, closing a path where the model could add its own words around a grounded answer. Tagged v1.2.0.

### Aug 25 to 26, the transport rewrite

Four small releases (v1.2.1 through v1.4.1) in between, mostly workflow token plumbing. The real event was the frontend chat transport, and it started as a production incident, not a refactor: the custom `useCoachChat.ts` engine over the raw LangGraph SDK was 403ing real members. I handed the fix to Codex on its own branch rather than patching it myself.

It came back with a transport swap, not a patch: CopilotKit v2's headless `useAgent`, a new `/api/copilotkit` runtime route, and a `CopilotKitMiddleware` pinned outermost on the coach agent so everything the runtime emits sees the final safety-scrubbed projections. Server-side it also added checkpoint forking for branching and time travel, resumable runs for join and rejoin, and token-level message streaming instead of values-only. The four interrupt cards moved from one `InterruptPanel.tsx` component to hooks registered under the provider. It found and worked around a real vendor bug along the way, a CopilotKit 0.1.95 issue that would have serialized an internal context object into the prompt. All of it passed the hermetic e2e suite before merging. Shipped as v1.5.0 (PR #58, Aug 26).

Not everything in that branch was kept on. Branching and history UI shipped and were disabled by default the same day (PR #57), a second, more conservative call than the first ship. The reliability follow-up (PR #60) merged on Aug 26, followed by a fix for a recursive `JSONValue` schema that crashed Studio's graph view and a missing assistant graph endpoint (PR #62), with v1.5.1 cut from PR #63. Studio now draws both graphs from production, and the `coach` view shows what the architecture describes, the whole RAG StateGraph running inside `coach_agent` as its `medical_lookup` tool.

### What the chain looks like from above

Every merged behaviour change in this list has a finding that motivated it, a decision that names the alternatives, and an experiment pair on the same judge. Five things were built and rejected. Two were built and left inconclusive. Two claims of mine were retracted on the record. That ratio is deliberate. A repo where everything tried was adopted is a repo where nothing was measured.
