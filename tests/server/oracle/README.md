# Pinned Oracle Environment

Isolated environment for parity characterization against the real `langgraph-api`.

## Pinned pair

| package | version | reason |
|---------|---------|--------|
| `langgraph-cli[inmem]` | `==0.4.31` | repo dev extra `>=0.4.31,<0.5` — compatible range |
| `langgraph-api` | `==0.12.6` | published on PyPI, matches `langgraph.json:api_version` |

`langgraph-cli[inmem]==0.4.31` declares `langgraph-api>=0.5.35,<1.0.0` for its `inmem` extra,
so `0.12.6` is within range. The repo dev venv resolves `langgraph-api==0.13.0` via
`[tool.uv] constraint-dependencies` and is **NOT** the characterization source.

## Verify resolution

```bash
uv venv tests/server/oracle/.venv --python 3.12
uv pip install --no-config --python tests/server/oracle/.venv/bin/python \
  -r tests/server/oracle/requirements.txt
tests/server/oracle/.venv/bin/python -c "import langgraph_api; print(langgraph_api.__version__)"
# → 0.12.6
```

`--no-config` bypasses `pyproject.toml:constraint-dependencies` which would otherwise force `>=0.13`.

## Run the oracle server

Dependencies are `["."]` so the server needs this repo on `PYTHONPATH`.
Run with `cwd` = repo root so `./healthcare_rag/...` graph paths resolve:

```bash
# from repo root
tests/server/oracle/.venv/bin/langgraph dev \
  --config tests/server/oracle/langgraph.json \
  --port 2025 --no-browser --no-reload
```

`tests/server/oracle/langgraph.json` is a verbatim copy of the repo `langgraph.json`
(graph/auth/http/store/api_version). Running with `cwd`=repo root keeps all
`./healthcare_rag/...` relative paths valid. No path rewriting is needed.

If `langgraph dev` is invoked with a different `cwd`, adjust the `graphs`/`auth`/`http`
paths to be relative to that `cwd` or absolute.

Optional Studio dev-mode auth bypass (local-only, not production):

```bash
LANGSMITH_LANGGRAPH_API_VARIANT=local_dev \
  tests/server/oracle/.venv/bin/langgraph dev --config tests/server/oracle/langgraph.json --port 2025 --no-browser
```

Without the env var, the StudioUser trigger is disabled and unauthenticated requests get 401.

## Config file

`langgraph.json` here is intentionally identical to `../../langgraph.json` — the
contract tests run with `cwd` = repo root so relative paths resolve unchanged.
If you need to run from `tests/server/oracle/` as cwd, rewrite graph paths to
`../../healthcare_rag/...` accordingly.
