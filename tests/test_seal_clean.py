from __future__ import annotations

import subprocess

import pytest

from evals.seal_clean import GitStatusError, check_clean, is_clean_status


@pytest.mark.parametrize(
    ("status", "clean"),
    [
        ("", True),
        ("?? dist/\n", True),
        ("?? evals/results/report.json\n", True),
        (" M evals/results/report.json\n", False),
        ("?? evals/results/helper.py\n", False),
        ("?? src/new.txt\n", False),
    ],
)
def test_is_clean_status_when_paths_vary(status: str, clean: bool) -> None:
    assert is_clean_status(status) is clean


def test_git_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    error = subprocess.CalledProcessError(2, ["git", "status"])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(GitStatusError):
        check_clean()


def test_untracked_executable_is_dirty(tmp_path) -> None:
    executable = tmp_path / "evals" / "results" / "report.log"
    executable.parent.mkdir(parents=True)
    executable.write_text("report")
    executable.chmod(0o755)

    assert is_clean_status("?? evals/results/report.log\n", tmp_path) is False
