"""The release/rollback contract, asserted against the workflow that implements it.

`docs/decisions/release-tags-and-rollback.md` states the taxonomy: git tags are
the release identity, the immutable `{{version}}` image tag is the ledger, and
nothing but a digest is ever deployed. A decision record cannot enforce itself —
these tests are what make the rules load-bearing, so an edit to `deploy.yml` that
reintroduces a mutable-tag deploy (or a raw smoke log artifact) fails here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

WORKFLOW: Final = Path(".github/workflows/deploy.yml")
RELEASE_WORKFLOW: Final = Path(".github/workflows/release.yml")
FLY_APP: Final = "hc-rag-server-prod"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())
    return parsed


@pytest.fixture(scope="module")
def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    return workflow.get("on") or workflow[True]


@pytest.fixture(scope="module")
def source() -> str:
    return WORKFLOW.read_text()


def test_only_semver_tags_and_a_manual_dispatch_reach_production(
    triggers: dict[str, Any],
) -> None:
    assert triggers["push"]["tags"] == ["v*.*.*"]
    assert set(triggers) == {"push", "workflow_dispatch"}, (
        "a new trigger on the deploy workflow is a new way to reach prod"
    )


def test_rollback_requires_a_version_and_a_recorded_reason(
    triggers: dict[str, Any],
) -> None:
    inputs = triggers["workflow_dispatch"]["inputs"]

    assert inputs["version"]["required"] is True
    # The reason is the record of why production changed; it is not optional.
    assert inputs["reason"]["required"] is True
    assert inputs["image_digest"]["required"] is False


def test_a_dispatch_never_rebuilds_the_image(workflow: dict[str, Any]) -> None:
    # A rollback targets an image that already exists. Rebuilding would produce
    # a different digest for the same release and break the ledger.
    assert workflow["jobs"]["build"]["if"] == "github.event_name == 'push'"
    assert workflow["jobs"]["rollback"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert "needs" not in workflow["jobs"]["rollback"]


def test_both_deploy_paths_are_gated_by_the_production_environment(
    workflow: dict[str, Any],
) -> None:
    for job in ("deploy-prod", "rollback"):
        assert workflow["jobs"][job]["environment"]["name"] == "production", job
        # One lock, so a rollback and a tag deploy serialise instead of racing.
        assert workflow["jobs"][job]["concurrency"]["group"] == "deploy-production", job
        assert workflow["jobs"][job]["concurrency"]["cancel-in-progress"] is False, job


def test_every_deploy_is_by_digest_never_by_tag(source: str) -> None:
    deploys = re.findall(r"flyctl deploy(?:[^\n]*\\\n)+?[^\n]*--image \"([^\"]+)\"", source)
    assert len(deploys) == 2, f"expected one flyctl deploy per path, found {deploys}"
    # Each --image is a shell variable the step proved matches
    # ^sha256:[0-9a-f]{64}$ before use; no literal tag reference is allowed.
    for image in deploys:
        assert image.startswith("$"), f"--image {image} is not a validated variable"
    assert source.count("sha256:[0-9a-f]{64}") >= 3, (
        "each path must validate its digest shape before deploying it"
    )


def test_no_mutable_tag_is_deployable(source: str) -> None:
    assert "latest=false" in source, "the metadata action must not publish `latest`"
    assert f"registry.fly.io/{FLY_APP}:" in source, "the Fly mirror tag is expected"
    # The rolling {{major}}.{{minor}} tag exists for humans reading the registry;
    # it must never appear in a deploy or resolve step.
    assert "{{major}}.{{minor}}" in source
    assert "flyctl deploy" in source
    assert re.search(r"--image .*\{\{major\}\}", source) is None


def test_the_release_ledger_is_the_immutable_semver_image_tag(source: str) -> None:
    # Rollback resolves ghcr.io/<repo>:<X.Y.Z> -> digest rather than consulting a
    # separate release database that could disagree with the registry.
    assert 'type=semver,pattern={{version}}' in source
    assert '${SOURCE_REPO}:${SEMVER}' in source


def test_rollback_deploys_the_release_config_not_the_current_one(
    workflow: dict[str, Any], source: str,
) -> None:
    steps = workflow["jobs"]["rollback"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))

    # Checking out the target tag is what keeps image and fly.prod.toml paired —
    # the SERVER_STORAGE trap in docs/deploy.md §6.3.
    assert checkout["with"]["ref"] == "${{ inputs.version }}"
    assert "--config deploy/fly.prod.toml" in source


def test_rollback_does_not_resync_secrets(workflow: dict[str, Any]) -> None:
    # A rollback must be able to recover from a bad secret sync.
    names = [str(step.get("name", "")).lower() for step in workflow["jobs"]["rollback"]["steps"]]
    assert not [name for name in names if "secret" in name and "sync" in name]


def test_rollback_verifies_tag_ancestry_like_the_forward_path(
    workflow: dict[str, Any],
) -> None:
    steps = workflow["jobs"]["rollback"]["steps"]
    ancestry = next(
        step for step in steps if "reachable from main" in str(step.get("name", ""))
    )
    run: str = ancestry["run"]

    assert "git ls-remote --tags origin" in run
    assert "git merge-base --is-ancestor" in run


def test_smoke_runs_after_both_deploy_paths_and_only_redacted_logs_are_uploaded(
    workflow: dict[str, Any], source: str,
) -> None:
    for job in ("deploy-prod", "rollback"):
        names = [str(step.get("name", "")).lower() for step in workflow["jobs"][job]["steps"]]
        assert any("smoke" in name for name in names), job
        assert any("/ok" in name for name in names), job

    assert source.count("python3 scripts/redact_smoke_log.py") == 2, (
        "both paths must redact through the same script"
    )
    # The raw log stays on the runner: an artifact of it would carry the bearer
    # tokens the redaction exists to remove.
    assert "deploy-artifact/smoke-raw.log" not in source


def test_the_decision_record_backing_these_rules_exists() -> None:
    record = Path("docs/decisions/release-tags-and-rollback.md").read_text()

    assert "**Verdict: ADOPT.**" in record
    assert "no auto-rollback" in record.lower()


# --- tag creation (.github/workflows/release.yml) ----------------------------


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(RELEASE_WORKFLOW.read_text())
    return parsed


@pytest.fixture(scope="module")
def release_source() -> str:
    return RELEASE_WORKFLOW.read_text()


@pytest.fixture(scope="module")
def release_steps(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(step.get("name")): step for step in release["jobs"]["tag"]["steps"]}


def test_releases_are_cut_by_hand_not_on_every_merge(release: dict[str, Any]) -> None:
    triggers = release.get("on") or release[True]

    # Tagging every merge would queue a production deploy request for every
    # merge, against this repo's human-gated posture (production environment
    # required reviewers).
    assert set(triggers) == {"workflow_dispatch"}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"bump", "dry_run"}


def test_a_release_can_only_be_cut_from_main(
    release_steps: dict[str, dict[str, Any]],
) -> None:
    guard: str = release_steps["Refuse to release from anything but main"]["run"]

    assert "refs/heads/main" in guard
    checkout = release_steps["Check out main with full history and tags"]
    assert checkout["with"]["ref"] == "main"
    # Tag history drives the version; a shallow clone would compute the wrong one.
    assert checkout["with"]["fetch-depth"] == 0


def test_the_release_refuses_to_move_an_existing_tag(
    release_steps: dict[str, dict[str, Any]],
) -> None:
    guard: str = release_steps["Refuse to re-point an existing tag"]["run"]

    assert "git ls-remote --tags origin" in guard
    assert "immutable" in guard


def test_the_tag_and_the_packaged_version_must_agree(
    release_steps: dict[str, dict[str, Any]],
) -> None:
    # Otherwise the wheel and image keep reporting an older version than the
    # tag that deployed them, and provenance becomes a guess.
    guard: str = release_steps["Require pyproject to carry the version being tagged"]["run"]

    assert "pyproject.toml" in guard
    assert "make release-prep" in guard


def test_a_red_or_pending_commit_cannot_be_released(
    release_steps: dict[str, dict[str, Any]],
) -> None:
    guard: str = release_steps["Require the commit to be green"]["run"]

    assert "check-runs" in guard
    assert "red checks on this commit" in guard
    assert "checks still running" in guard


def test_the_tag_is_pushed_with_a_token_that_can_trigger_deploy(
    release_steps: dict[str, dict[str, Any]],
) -> None:
    step = release_steps["Create and push the release tag"]
    run: str = step["run"]

    # GITHUB_TOKEN-pushed refs raise no workflow events, so a tag created with
    # it would never deploy. The job refuses rather than making a dead tag.
    assert step["env"]["RELEASE_TAG_TOKEN"] == "${{ secrets.RELEASE_TAG_TOKEN }}"
    assert "RELEASE_TAG_TOKEN is not set" in run
    assert "git tag -a" in run, "release tags are annotated"


def test_a_dry_run_tags_nothing(release_steps: dict[str, dict[str, Any]]) -> None:
    assert release_steps["Plan (dry run stops here)"]["if"] == "${{ inputs.dry_run }}"
    for name in ("Create and push the release tag", "Publish the GitHub release"):
        assert release_steps[name]["if"] == "${{ !inputs.dry_run }}", name


def test_local_preview_and_ci_compute_the_version_the_same_way(
    release_source: str,
) -> None:
    # `make next-version` and the workflow both shell out to the same script;
    # a second implementation would drift.
    makefile = Path("Makefile").read_text()

    assert "scripts/next_version.py" in release_source
    assert "scripts/next_version.py" in makefile
