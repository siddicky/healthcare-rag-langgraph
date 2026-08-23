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

import pytest

os.environ.setdefault(
    "LANGSMITH_FEEDBACK_PROJECT_ID", "00000000-0000-4000-8000-000000000fee"
)

# ---------------------------------------------------------------------------
# anyio / pytest-asyncio interop
#
# These suites mark their tests `@pytest.mark.anyio` while `pyproject.toml` sets
# `asyncio_mode = "auto"`, so BOTH plugins hook `pytest_fixture_setup` and both
# claim the async-generator fixtures here (`harness`, `client`, ...).  Each one
# only backs off if it finds `fixturedef.func` already replaced by the other's
# sync wrapper, so the winner is decided purely by pluggy's LIFO hookwrapper
# order -- i.e. by plugin *registration* order, which follows entry-point
# discovery and therefore differs between machines.
#
#   anyio first  -> `run_asyncgen_fixture` drives one generator in one task:
#                   setup and teardown share the test's task and event loop.
#   asyncio first -> pytest-asyncio drives setup and teardown as two separate
#                   `Runner.run()` calls, in a loop that is not the anyio test
#                   runner's.  `test_runs.py::harness` holds
#                   `anyio.create_task_group()` (via the app lifespan) open
#                   across its `yield`, so the exit lands in a different task:
#                   "Attempted to exit cancel scope in a different task than it
#                   was entered in" -- 17 teardown ERRORs, tests themselves green.
#
# That is exactly the GitHub Actions failure; it reproduces on any platform with
# `pytest -p no:anyio -p anyio.pytest_plugin tests/server/test_runs.py`, which
# forces the losing registration order.  Pin the order instead of inheriting it.
# ---------------------------------------------------------------------------


def _plugin(config: pytest.Config, module_name: str) -> object | None:
    """The registered plugin object for `module_name`, whatever name it goes by."""
    for plugin in config.pluginmanager.get_plugins():
        if getattr(plugin, "__name__", None) == module_name:
            return plugin
    return None


def _anyio_wraps_first(
    config: pytest.Config, anyio_plugin: object, asyncio_plugin: object
) -> bool:
    """True if anyio's `pytest_fixture_setup` wrapper runs outside pytest-asyncio's.

    pluggy executes `get_hookimpls()` in reverse, so a *later* entry wraps first.
    """
    plugins = [impl.plugin for impl in config.hook.pytest_fixture_setup.get_hookimpls()]
    if anyio_plugin not in plugins or asyncio_plugin not in plugins:
        return True
    return plugins.index(anyio_plugin) > plugins.index(asyncio_plugin)


def pytest_configure(config: pytest.Config) -> None:
    anyio_plugin = _plugin(config, "anyio.pytest_plugin")
    asyncio_plugin = _plugin(config, "pytest_asyncio.plugin")
    if anyio_plugin is None or asyncio_plugin is None:
        return
    if _anyio_wraps_first(config, anyio_plugin, asyncio_plugin):
        return
    # Re-registering moves anyio to the end of the hookimpl list, i.e. first to
    # run. Assert the outcome rather than trusting the LIFO rule silently: a
    # pluggy change must fail loudly here, not resurface as teardown ERRORs.
    name = config.pluginmanager.get_name(anyio_plugin)
    config.pluginmanager.unregister(anyio_plugin)
    config.pluginmanager.register(anyio_plugin, name)
    if not _anyio_wraps_first(config, anyio_plugin, asyncio_plugin):
        raise RuntimeError(
            "could not order the anyio pytest plugin ahead of pytest-asyncio; "
            "async fixtures in tests/server would run on the wrong event loop"
        )
