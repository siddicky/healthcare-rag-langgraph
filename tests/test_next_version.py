"""Version selection for automated tagging.

`scripts/next_version.py` decides what the release workflow tags, so a wrong
answer here is a wrong version in the registry, in the image, and in the
rollback ledger. The git-backed cases build real repositories in `tmp_path`
rather than mocking `git log` — the parsing is the part that breaks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.next_version import Version, classify, latest_release, next_version


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _ = (tmp_path / "pyproject.toml").write_text('name = "healthcare-rag"\nversion = "0.2.0"\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "chore: initial")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def commit(repo: Path, message: str) -> None:
    # One file per commit: the merge case must merge cleanly.
    name = f"{len(list(repo.glob('change-*.txt')))}-{abs(hash(message)) % 10_000}"
    _ = (repo / f"change-{name}.txt").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


# --- version ordering -------------------------------------------------------


def test_a_final_release_outranks_its_own_prereleases() -> None:
    tags = ["v1.2.0-rc1", "v1.2.0", "v1.1.9"]

    assert latest_release(tags) == Version.parse("v1.2.0")


def test_versions_order_numerically_not_lexically() -> None:
    tags = ["v1.9.0", "v1.10.0", "v1.2.0"]

    latest = latest_release(tags)
    assert latest is not None
    assert latest.tag == "v1.10.0"


def test_non_release_tags_are_ignored() -> None:
    assert latest_release(["nightly", "v1.0.0", "release-2"]) == Version.parse("v1.0.0")
    assert latest_release(["nightly", "wip"]) is None


def test_a_prerelease_finalises_to_its_own_number() -> None:
    rc = Version.parse("v1.2.0-rc1")
    assert rc is not None

    # v1.2.0-rc1 promotes to v1.2.0, never to v1.3.0.
    assert rc.bumped("minor").tag == "v1.2.0"


def test_the_tag_and_the_project_version_stay_in_step() -> None:
    version = Version.parse("v1.2.3")
    assert version is not None

    assert version.tag == "v1.2.3"
    assert version.project_version == "1.2.3"


# --- conventional-commit classification -------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("feat: add a thing", "minor"),
        ("feat(graph): add a thing", "minor"),
        ("fix: correct a thing", "patch"),
        ("docs: explain a thing", "patch"),
        ("feat!: drop a thing", "major"),
        ("refactor(api)!: rename a thing", "major"),
        ("fix: something\n\nBREAKING CHANGE: the env var moved", "major"),
        ("not a conventional subject", "patch"),
    ],
)
def test_a_single_commit_classifies_as(message: str, expected: str) -> None:
    assert classify([message]) == expected


def test_the_strongest_bump_in_the_range_wins() -> None:
    assert classify(["docs: a", "feat: b", "fix: c"]) == "minor"
    assert classify(["docs: a", "feat: b", "fix!: c"]) == "major"


# --- end to end against real git history ------------------------------------


def test_the_first_release_seeds_from_the_project_version(repo: Path) -> None:
    commit(repo, "feat: something")

    version, _, current, _ = next_version("auto")

    # Not an invented 0.1.0: the package has been carrying 0.2.0 all along.
    assert current is None
    assert version.tag == "v0.2.0"


def test_a_feat_since_the_last_tag_bumps_the_minor(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")
    commit(repo, "feat: something new")

    version, level, current, commits = next_version("auto")

    assert (version.tag, level) == ("v1.3.0", "minor")
    assert current is not None and current.tag == "v1.2.3"
    assert len(commits) == 1


def test_only_fixes_since_the_last_tag_bump_the_patch(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")
    commit(repo, "fix: a bug")
    commit(repo, "docs: a note")

    version, level, _, _ = next_version("auto")

    assert (version.tag, level) == ("v1.2.4", "patch")


def test_a_breaking_change_below_1_0_bumps_the_minor_not_the_major(repo: Path) -> None:
    _git(repo, "tag", "-a", "v0.4.1", "-m", "release v0.4.1")
    commit(repo, "feat!: change the interface")

    version, level, _, _ = next_version("auto")

    # 0.x means "not stable yet" — promoting to 1.0.0 would declare stability
    # by accident.
    assert (version.tag, level) == ("v0.5.0", "minor")


def test_a_breaking_change_at_or_above_1_0_bumps_the_major(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.4.1", "-m", "release v1.4.1")
    commit(repo, "feat!: change the interface")

    version, level, _, _ = next_version("auto")

    assert (version.tag, level) == ("v2.0.0", "major")


def test_an_explicit_bump_overrides_the_commit_classification(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")
    commit(repo, "docs: a note")

    version, level, _, _ = next_version("minor")

    assert (version.tag, level) == ("v1.3.0", "minor")


def test_releasing_with_nothing_new_is_refused(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")

    with pytest.raises(SystemExit, match="nothing to release"):
        _ = next_version("auto")


def test_merge_commits_do_not_drive_the_bump(repo: Path) -> None:
    _git(repo, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")
    _git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "fix: a bug")
    _git(repo, "checkout", "-q", "main")
    commit(repo, "docs: a note")
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "feat: merge branch 'side'")

    version, level, _, commits = next_version("auto")

    # The merge subject says "feat" but no merge commit carries its own change.
    assert (version.tag, level) == ("v1.2.4", "patch")
    assert all("merge branch" not in message for message in commits)
