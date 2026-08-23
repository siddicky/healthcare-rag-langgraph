"""The resources-installing fixtures must restore the previous singleton.

`install_resources` (tests/graph/conftest.py) and `node_resources`
(tests/test_pinecone_retrieval.py) both replace the process-wide `Resources`
via `resources.override(...)`. They used to tear down by installing a *fresh*
sealed `Resources`, which left `_OfflineClient` fakes in the global singleton
for whatever ran next -- order-dependent leakage that lets a test pass without
declaring the fixture it depends on, and hides accidental client acquisition.
"""

from __future__ import annotations

import pytest

from healthcare_rag.graph import resources as resources_module

from .conftest import FakeGateway, ResourceInstaller


def test_install_resources_restores_the_previous_singleton(
    assert_resources_restored,
    install_resources: ResourceInstaller,
) -> None:
    """`assert_resources_restored` does the real assert, in teardown.

    It is torn down *after* `install_resources` (LIFO), so it observes the
    singleton the installer left behind. The body here only proves the
    installer really did take over in the first place -- otherwise the teardown
    check would pass vacuously.
    """
    previous = assert_resources_restored
    assert resources_module.get() is previous

    installed = install_resources(FakeGateway())

    assert resources_module.get() is installed
    assert installed is not previous
    # The installed instance is the sealed one; the restored one must not be.
    assert installed._weaviate is not None


def test_previous_singleton_carries_no_offline_fakes() -> None:
    """Runs after the test above; the leaked fakes would still be installed.

    Test order within a module is source order, so this is the "later test"
    from the bug report. With the leak, `_weaviate` here is an `_OfflineClient`
    left over from `install_resources`.
    """
    current = resources_module.get()

    assert current._weaviate is None
    assert current._pinecone_client is None
    assert current._pinecone_index is None


@pytest.mark.parametrize("installs", [1, 2, 3])
def test_repeated_installs_still_restore_the_original(
    installs: int,
    assert_resources_restored,
    install_resources: ResourceInstaller,
) -> None:
    """N `install()` calls inside one test still restore the one prior instance.

    The teardown check lives in `assert_resources_restored`; varying the install
    count is the point, so the parameter has to actually drive the loop.
    """
    for _ in range(installs):
        install_resources(FakeGateway())

    assert resources_module.get() is not assert_resources_restored
