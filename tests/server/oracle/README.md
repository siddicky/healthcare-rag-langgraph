# Pinned oracle environment

This directory isolates the real `langgraph-api` used to characterize the OSS
Agent Server compatibility contract. Production must not install this package.

## Pinned versions

| package | version | reason |
|---------|---------|--------|
| `langgraph-cli[inmem]` | `==0.4.31` | Compatible with the repo's `>=0.4.31,<0.5` development range |
| `langgraph-api` | `==0.12.6` | Matches `api_version` in the root `langgraph.json` |

The normal development environment resolves `langgraph-api==0.13.0` through
`pyproject.toml` constraints. It is not the characterization source.

## Build the oracle environment

Run these commands from the repository root. Install the oracle pins and this
repository in one resolution pass:

```bash
uv venv tests/server/oracle/.venv --python 3.12
uv pip install --no-config \
  --python tests/server/oracle/.venv/bin/python \
  -r tests/server/oracle/requirements.txt -e . --no-build-isolation
tests/server/oracle/.venv/bin/python -c \
  "import langgraph_api; assert langgraph_api.__version__ == '0.12.6'"
tests/server/oracle/.venv/bin/python -c \
  "import langgraph_grpc_common.proto"
```

`--no-config` bypasses the repository's `constraint-dependencies`, which would
force `langgraph-api>=0.13`. The single resolution pass also checks the shared
gRPC constraint. `langgraph-api==0.12.6` requires `grpcio>=1.80,<1.81`, so the
repository keeps `weaviate-client<4.16.3` to avoid its conflicting `grpcio<1.80`
requirement.

Do not edit `.venv/` by hand. Rebuild it with the commands above.

## Run the parity gate

```bash
OPENAI_API_KEY=dummy make parity
```

`make parity` sets `ORACLE=1` and runs `tests/server/contract`. The session
fixture starts the pinned server on port 2025, waits for `/ok`, preserves its
log tail on startup failure, and stops it after the suite. Override the port if 2025
is occupied:

```bash
OPENAI_API_KEY=dummy ORACLE_PORT=2026 make parity
```

Without `ORACLE=1`, oracle-marked tests skip locally. CI builds this environment
and runs the oracle lane in `.github/workflows/server-parity.yml`. The OSS
contract tests run separately without the proprietary package.

The JSON files in `tests/server/contract/fixtures/` record behavior observed
against `langgraph-api==0.12.6`, including accepted deviations. They are review
evidence, not a replacement for the executable parity tests. The unchanged
`scripts/langgraph_smoke.py` and `scripts/deployed_smoke.py` remain the broader
server compatibility contract.

## Run the oracle server directly

From the repository root:

```bash
OPENAI_API_KEY=dummy tests/server/oracle/.venv/bin/langgraph dev \
  --config tests/server/oracle/langgraph.json \
  --port 2025 --no-browser --no-reload
```

For local Studio access, enable the upstream development auth variant:

```bash
OPENAI_API_KEY=dummy LANGSMITH_LANGGRAPH_API_VARIANT=local_dev \
  tests/server/oracle/.venv/bin/langgraph dev \
  --config tests/server/oracle/langgraph.json \
  --port 2025 --no-browser --no-reload
```

Without that variant, unauthenticated protected requests return 401.

`tests/server/oracle/langgraph.json` must stay byte-for-byte identical to the
root `langgraph.json`. Its graph paths and `dependencies: ["."]` assume the
repository root is the working directory. Do not run this copy from
`tests/server/oracle/` or rewrite only its graph paths.
