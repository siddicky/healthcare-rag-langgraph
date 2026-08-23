"""Deterministic deployment env for the server suites.

`create_app()` mounts the production custom app (`langgraph.json` ->
`healthcare_rag/agent/http_app.py:app`), whose lifespan validates the dedicated
feedback project. These suites are about server topology, not about the
operator's `.env`, so pin a syntactically valid test project id instead of
depending on one being configured. The probe itself stays offline: the root
conftest clears `LANGSMITH_API_KEY`, and without a platform credential
`validate_feedback_project` skips the LangSmith call. The real validation
contract is asserted in `tests/agent/test_deploy_config.py`.
"""

import os

os.environ.setdefault(
    "LANGSMITH_FEEDBACK_PROJECT_ID", "00000000-0000-4000-8000-000000000fee"
)

# ---------------------------------------------------------------------------
# anyio / pytest-asyncio interop -- why `asyncio_mode = "strict"`
#
# These suites mark their tests `@pytest.mark.anyio` and own async-generator
# fixtures (`harness`, `client`, ...).  `pyproject.toml` used to set
# `asyncio_mode = "auto"`, under which pytest-asyncio adopts *every* unmarked
# async fixture; anyio's plugin claims the same ones.  Both hook
# `pytest_fixture_setup`, and each only backs off if it finds `fixturedef.func`
# already replaced by the other's sync wrapper -- so the winner was decided by
# pluggy's LIFO hookwrapper order, i.e. by plugin *registration* order, which
# follows entry-point discovery and therefore differs between machines.
#
#   anyio wins   -> `run_asyncgen_fixture` drives one generator in one task:
#                   setup and teardown share the test's task and event loop.
#   asyncio wins -> pytest-asyncio drives setup and teardown as two separate
#                   `Runner.run()` calls, in a loop that is not the anyio test
#                   runner's.  `test_runs.py::harness` holds
#                   `anyio.create_task_group()` (via the app lifespan) open
#                   across its `yield`, so the exit lands in a different task:
#                   "Attempted to exit cancel scope in a different task than it
#                   was entered in" -- 17 teardown ERRORs, tests themselves green.
#
# That was exactly the GitHub Actions failure; it reproduces on any platform
# with `pytest -p no:anyio -p anyio.pytest_plugin tests/server/test_runs.py`,
# which forces the losing registration order.
#
# The fix is `asyncio_mode = "strict"` in `pyproject.toml`, a supported
# pytest-asyncio setting: in strict mode pytest-asyncio only adopts objects that
# opt in (`@pytest.mark.asyncio`, `@pytest_asyncio.fixture`), so it never looks
# at the anyio-marked tests or the plain `@pytest.fixture` async generators
# here.  With only one plugin claiming them, hook ordering stops mattering and
# the reversed-registration command above passes too.
#
# The cost is that async tests must name their runner: the suites that relied on
# auto mode now carry a per-test `@pytest.mark.asyncio`, and the suites here keep
# their `@pytest.mark.anyio`.  Do not re-enable auto mode to avoid adding a marker
# to a new async test -- that resurrects the race.
#
# Rejected alternatives: mutating `config.pluginmanager` from this conftest to
# pin registration order (reaches into pluggy internals and mutates process-wide
# state from a nested conftest, affecting tests outside `tests/server`), and
# running the server suites with `-p no:asyncio` (has to be repeated in every
# invocation site -- both CI workflows and `make test` -- and silently stops
# protecting anything the moment one is added).
# ---------------------------------------------------------------------------
