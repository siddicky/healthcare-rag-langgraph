# Decision: 142 Dependabot alerts — delete the stale `requirements.txt`, upgrade `cryptography`, assess the rest

- **Verdict: DELETE `requirements.txt` (136 alerts), UPGRADE `cryptography` 46.0.7 → 50.0.0 (4 alerts), ASSESS-AND-DEFER the remaining 2.**
  `pyproject.toml` + `uv.lock` remain the only dependency source. `tests/server/oracle/requirements.txt` is untouched.
- Date: 2026-08-23 · Commit: `2494ae8` · Trigger: GitHub reported 142 open alerts on `main` before the first prod deploy.
- Evidence: `gh api repos/siddicky/healthcare-rag-langgraph/dependabot/alerts?state=open --paginate`, `uv lock` resolver output, full offline suite.

## Before

| severity | count |
|---|---|
| critical | 2 |
| high | 61 |
| medium | 56 |
| low | 23 |
| **total** | **142** |

Grouped by the manifest that raised them:

| manifest | alerts |
|---|---|
| `requirements.txt` | **136** |
| `uv.lock` | 6 |

## Why deleting `requirements.txt` is a fix and not suppression

The file was a frozen `pip freeze` inherited from the original template. Three separate documents already
warned against using it (`AGENTS.md`, `README.md`, `openwiki/operations/runbook.md`), each stating that its
pins are mutually unsatisfiable — `grpcio==1.67.1` conflicts with the surrounding `grpcio-*` pins, so
`pip install -r requirements.txt` cannot succeed at all.

Nothing built from it. Verified by search across `Makefile`, `Dockerfile`, `server/Dockerfile`,
`docker-compose.yml`, `docker-compose.server.yml`, `langgraph.json`, `pyproject.toml` and every file in
`.github/workflows/`: the only match is `server-parity.yml:73`, which reads
`tests/server/oracle/requirements.txt` — a different, deliberate file that pins the oracle environment the
contract suite runs against. That one is preserved.

So the 136 alerts described the security posture of an environment nobody could install and nothing
deployed. **Both criticals were in this group:**

- `authlib` — JWS JWK header injection, signature verification bypass
- `langchain-core` — serialization injection enabling secret extraction

Neither package version was ever resolved into the real dependency graph. Deleting the file removes the
alerts because the vulnerable manifest genuinely no longer exists, not because anything was dismissed.

A file that must be warned against in three places is a file that should not exist.

## The 6 real alerts, in `uv.lock`

| severity | package | vulnerable range | patched | action |
|---|---|---|---|---|
| high | cryptography | `>= 0.5.0, < 48.0.1` | 48.0.1 | **upgraded** |
| high | cryptography | `<= 48.0.0` | 49.0.0 | **upgraded** |
| high | cryptography | `>= 44.0.0, < 50.0.0` | 50.0.0 | **upgraded** |
| medium | cryptography | `<= 48.0.0` | 49.0.0 | **upgraded** |
| medium | langchain | `<= 1.3.8` | 1.3.9 | deferred, not reachable |
| low | langchain-openai | `< 1.1.14` | 1.1.14 | deferred, not reachable |

`cryptography` is transitive, so `uv lock --upgrade-package cryptography` was enough; no floor was added to
`pyproject.toml`, because nothing was holding it back and an undeclared pin would be a lie about what this
project directly depends on. 46.0.7 → 50.0.0 clears all four ranges at once.

## Why the last two are deferred

Both upgrades are blocked by pins this repo holds deliberately, and forcing either would mean a cascading
dependency migration during a submission freeze.

**`langchain >= 1.3.9`** is blocked by the **`langgraph-sdk`** pin, not by the `langgraph` range. The
project's `langgraph >= 1.2, < 2` is not itself contradictory; the chain is that `langgraph >= 1.2.4`
requires `langgraph-sdk >= 0.4.2, < 0.5.0`, while this project pins `langgraph-sdk >= 0.3, < 0.4`:

```
Because langgraph>=1.2.4 depends on langgraph-sdk>=0.4.2,<0.5.0 ...
And because your project depends on langgraph-sdk>=0.3,<0.4 and
your project requires healthcare-rag[dev], we can conclude that your
project's requirements are unsatisfiable.
```

Reproduce with `uv lock --dry-run --upgrade-package 'langchain>=1.3.9'`.

**`langchain-openai >= 1.1.14`** requires `openai >= 2.26.0`, against this project's direct
`openai >= 1.76, < 2` pin (`pyproject.toml:8`). To be precise about what that pin is and is not:
`openevals` does not itself require OpenAI 1.x. The `< 2` bound is a deliberate project-wide compatibility
boundary — the evals extra notes that `openevals` pulls in `langchain-openai`, which must resolve against
it — and **whether relaxing it would actually break anything has not been tested**. Crossing a major
version of the OpenAI SDK during a submission freeze, to clear a low-severity advisory that is not
reachable here, is not a trade worth making blind. An OpenAI 2.x migration is the honest prerequisite, not
a pin bump.

### Neither vulnerable code path exists in this repo

This was checked by search, not assumed:

- The **langchain** advisory affects file-search agent middleware and filesystem loaders — path traversal
  and sandbox escape where a resolved path is not confined to its intended root. A search across
  `healthcare_rag/`, `server/` and `evals/` for `file_search`, `FileSearch`, `DirectoryLoader`,
  `GlobLoader`, `TextLoader`, `UnstructuredFileLoader` and `filesystem` returns **zero matches**. This
  application loads no documents from disk through langchain; retrieval reads pre-built chunks from
  Weaviate.
- The **langchain-openai** advisory affects `_url_to_size()` via `get_num_tokens_from_messages`, a
  TOCTOU/DNS-rebinding SSRF window in **image** token counting. A search for `get_num_tokens_from_messages`,
  `_url_to_size`, `image_url` and image message parts returns **zero matches**. The pipeline is text-only
  and never sends an image to a model.

### What would change this verdict

Revisit immediately if either becomes true:

1. Any langchain filesystem loader or file-search middleware is introduced anywhere in the pipeline.
2. Any image input reaches a model — uploads currently go through
   `healthcare_rag/agent/uploads.py` as text extraction, not image messages.

Also revisit when the `langgraph` / `langgraph-sdk` / `langgraph-api` stack is next upgraded as a set, since
that unblocks `langchain >= 1.3.9` without touching the `openai` pin.

## Verification

- Full offline suite (macOS, local): **1736 passed, 1 skipped, 0 failed**. On Linux CI the same suite
  reports one further skip. Quote the platform with the number; neither is "the" suite count.

  An earlier draft of this record claimed the `cryptography` upgrade "un-skips two conditional tests".
  That was wrong and is corrected here: `cryptography` appears **zero** times anywhere under `tests/`.
  The two tests in question — `tests/graph/test_boundary_durability.py` and
  `tests/graph/test_engine_record.py` — are gated on
  `find_spec("langgraph.checkpoint.sqlite")`, i.e. the **graph-sqlite extra**. They began running because
  of the CI extras fix (`02d0a01`), not because of `2494ae8`. Falsified by experiment: removing only
  `langgraph-checkpoint-sqlite` while holding `cryptography` at 50.0.0 reproduces the skips exactly.
- `tests/server` the way CI runs it: **89 passed, 1 skipped**.
- Credential-free: **1729 passed, 1 skipped, 0 failed**, verified by removing `.env` from disk entirely,
  in a fresh venv, with the Weaviate container stopped. Note that `env -u VAR` does **not** produce a
  credential-free run — `tests/conftest.py` calls `load_dotenv()` and reads the file straight back. The
  authoritative evidence is GitHub Actions, which checks out without a `.env` at all.
- PHI-path subsets (`test_safety_gate.py`, `test_privacy_sanitizer.py`, `graph/test_graph_privacy.py`):
  **116 passed**. To be accurate about why: `uv tree --locked --invert --package cryptography` resolves it
  through `authlib -> weaviate-client`, `joserfc -> authlib`, and the dev-only `langgraph-api` — **not**
  through the Presidio/spaCy privacy sanitizer. These subsets are useful regression coverage around the
  auth and PHI paths, but they do not exercise the cryptography advisories themselves.
- `uv lock --check` passes; the lockfile is consistent with `pyproject.toml`.

## Expected alert count after this lands

Dependabot closes alerts on its next scan **after the manifest is removed from the default branch**, so the
count does not drop at commit time — it drops after this reaches `main`. Expected steady state: **6 → 2**,
both deferred and recorded above, neither critical or high, neither reachable.

Anyone reading the alert page before that scan completes will still see 142. That lag is expected and is
not evidence the work did not happen.
