"""Compute the next release version from the tag history and the commits since it.

The release workflow and `make next-version` share this so a human previewing a
release locally gets the number CI will pick, not a similar one. Conventional
Commit subjects drive the bump:

    feat!: / BREAKING CHANGE:  -> major   (minor while the major is still 0)
    feat:                      -> minor
    anything else              -> patch

Pre-1.0 breaking changes bump the minor, the usual semver reading of "the major
is 0, the API is not stable yet" — and the alternative (0.x -> 1.0 on the first
`feat!`) would declare stability by accident.

Standard library only: it runs on the runner's `python3` and in `make`.

    python3 scripts/next_version.py --bump auto --explain
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from typing import Final, Literal, Self

Bump = Literal["major", "minor", "patch"]

TAG_PATTERN: Final = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(?P<pre>[0-9A-Za-z._-]+))?$")
# `type(scope)!: subject` or `type!: subject` — the `!` is the breaking marker.
BREAKING_SUBJECT: Final = re.compile(r"^[a-z]+(\([^)]*\))?!:")
FEAT_SUBJECT: Final = re.compile(r"^feat(\([^)]*\))?:")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    # A final release sorts above its prereleases: (1,2,3,True) > (1,2,3,False).
    is_final: bool = True
    prerelease: str = ""

    @classmethod
    def parse(cls, tag: str) -> Self | None:
        match = TAG_PATTERN.match(tag.strip())
        if match is None:
            return None
        pre = match.group("pre") or ""
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            is_final=not pre,
            prerelease=pre,
        )

    def bumped(self, bump: Bump) -> Version:
        # A prerelease finalises to its own number: v1.2.0-rc1 -> v1.2.0.
        if not self.is_final:
            return Version(self.major, self.minor, self.patch)
        if bump == "major":
            return Version(self.major + 1, 0, 0)
        if bump == "minor":
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)

    @property
    def tag(self) -> str:
        suffix = f"-{self.prerelease}" if self.prerelease else ""
        return f"v{self.major}.{self.minor}.{self.patch}{suffix}"

    @property
    def project_version(self) -> str:
        """The value `pyproject.toml` must carry for this tag."""
        suffix = f"-{self.prerelease}" if self.prerelease else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def latest_release(tags: list[str]) -> Version | None:
    parsed = [version for tag in tags if (version := Version.parse(tag)) is not None]
    return max(parsed) if parsed else None


def classify(commits: list[str]) -> Bump:
    """Pick the strongest bump the commit subjects justify."""
    strongest: Bump = "patch"
    for message in commits:
        subject = message.splitlines()[0] if message.splitlines() else ""
        if BREAKING_SUBJECT.match(subject) or "BREAKING CHANGE:" in message:
            return "major"
        if FEAT_SUBJECT.match(subject):
            strongest = "minor"
    return strongest


def commits_since(tag: str | None) -> list[str]:
    span = f"{tag}..HEAD" if tag else "HEAD"
    raw = _git("log", span, "--no-merges", "--format=%s%n%b%x00")
    return [chunk.strip() for chunk in raw.split("\0") if chunk.strip()]


def next_version(bump: str) -> tuple[Version, Bump, Version | None, list[str]]:
    tags = [line for line in _git("tag", "--list").splitlines() if line]
    current = latest_release(tags)
    commits = commits_since(current.tag if current else None)
    if not commits:
        raise SystemExit(
            f"nothing to release: no commits since {current.tag if current else 'the initial commit'}"
        )
    level: Bump = classify(commits) if bump == "auto" else bump  # pyright: ignore[reportAssignmentType]
    if current is None:
        # First release. `pyproject.toml`'s version is the starting point rather
        # than an invented 0.1.0 — the package has been carrying one all along.
        seed = Version.parse(f"v{_project_version()}")
        if seed is None:
            raise SystemExit(f"pyproject version '{_project_version()}' is not vX.Y.Z-shaped")
        return seed, level, None, commits
    if current.major == 0 and level == "major":
        level = "minor"
    return current.bumped(level), level, current, commits


def _project_version() -> str:
    from pathlib import Path

    for line in Path("pyproject.toml").read_text().splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("pyproject.toml has no version")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bump", default="auto", choices=["auto", "major", "minor", "patch"])
    _ = parser.add_argument("--explain", action="store_true", help="show what drove the bump")
    args = parser.parse_args()

    version, level, current, commits = next_version(str(args.bump))
    if args.explain:
        described = (
            f"{level}{' (auto)' if args.bump == 'auto' else ''}"
            if current is not None
            else "n/a — first release, seeded from pyproject"
        )
        print(f"current release: {current.tag if current else '(none — first release)'}")
        print(f"bump:            {described}")
        print(f"next tag:        {version.tag}")
        print(f"pyproject:       version = \"{version.project_version}\"")
        print(f"commits since:   {len(commits)}")
        for message in commits[:20]:
            print(f"  - {message.splitlines()[0]}")
        if len(commits) > 20:
            print(f"  ... and {len(commits) - 20} more")
    else:
        print(version.tag)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # `next-version --explain | head` closes the pipe; that is not an error.
        raise SystemExit(0) from None
