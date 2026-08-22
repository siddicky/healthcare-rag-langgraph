from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import pytest

import evals.routing_report_io as report_io
from evals.routing_report_io import (
    ReportError,
    ReportPair,
    publish_report_batch,
    recover_report_files,
)


@dataclass(frozen=True, slots=True)
class TempFailure:
    phase: Literal["write", "flush", "fsync"]
    file_call: int
    kind: Literal["oserror", "interrupt"]


def _pairs(root: Path) -> tuple[ReportPair, ...]:
    return tuple(
        ReportPair(
            json_path=root / f"arm-{index}.json",
            json_content=f"new-json-{index}",
            markdown_path=root / f"arm-{index}.md",
            markdown_content=f"new-md-{index}",
        )
        for index in range(4)
    )


def test_report_batch_when_late_publish_fails_restores_all_four_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    pairs = _pairs(tmp_path)
    targets = tuple(
        path for pair in pairs for path in (pair.json_path, pair.markdown_path)
    )
    for index, target in enumerate(targets):
        _ = target.write_text(f"old-{index}")
    before = tuple(target.read_bytes() for target in targets)
    original = report_io.replace_report_file
    calls = 0

    def fail_last_publish(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 16:
            raise OSError("injected eighth-file publish failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_last_publish)

    # When / Then
    with pytest.raises(ReportError, match="publish"):
        _ = publish_report_batch(pairs)
    assert tuple(target.read_bytes() for target in targets) == before
    assert not tuple(tmp_path.glob(".*.tmp"))
    assert not tuple(tmp_path.glob(".*.backup"))


def test_report_batch_when_rollback_fails_retains_batch_recovery_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    pairs = _pairs(tmp_path)
    targets = tuple(
        path for pair in pairs for path in (pair.json_path, pair.markdown_path)
    )
    for index, target in enumerate(targets):
        _ = target.write_text(f"old-{index}")
    before = tuple(target.read_bytes() for target in targets)
    original = report_io.replace_report_file
    calls = 0

    def fail_publish_and_rollback(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 16:
            raise OSError("injected eighth-file publish failure")
        if calls == 17:
            raise OSError("injected first restore failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_publish_and_rollback)

    # When
    with pytest.raises(ReportError, match="rollback") as caught:
        _ = publish_report_batch(pairs)

    # Then
    assert len(caught.value.recovery_paths) == 8
    assert all(
        path.exists() and path.read_bytes() in before
        for path in caught.value.recovery_paths
    )
    assert not tuple(tmp_path.glob(".*.tmp"))
    monkeypatch.setattr(report_io, "replace_report_file", original)
    _ = recover_report_files(caught.value.recovery_paths)
    assert tuple(target.read_bytes() for target in targets) == before
    assert not tuple(tmp_path.glob(".*.backup"))


@pytest.mark.parametrize("stale_index", [0, 1])
def test_report_pair_when_any_stale_backup_exists_preflight_changes_nothing(
    tmp_path: Path, stale_index: int
) -> None:
    # Given
    pair = _pairs(tmp_path)[0]
    targets = (pair.json_path, pair.markdown_path)
    for index, target in enumerate(targets):
        _ = target.write_text(f"old-target-{index}")
    backups = tuple(target.with_name(f".{target.name}.backup") for target in targets)
    _ = backups[stale_index].write_text(f"stale-backup-{stale_index}")
    target_before = tuple(target.read_bytes() for target in targets)
    backup_before = backups[stale_index].read_bytes()

    # When / Then
    with pytest.raises(ReportError, match="recovery_required") as caught:
        _ = publish_report_batch((pair,))
    assert caught.value.recovery_paths == (backups[stale_index],)
    assert tuple(target.read_bytes() for target in targets) == target_before
    assert backups[stale_index].read_bytes() == backup_before
    assert not backups[1 - stale_index].exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_report_batch_when_first_temp_fails_removes_created_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    output_dir = tmp_path / "nonexistent" / "batch"
    pairs = _pairs(output_dir)

    def fail_temp(_target: Path, _content: str) -> Path:
        raise OSError("injected first temp failure")

    monkeypatch.setattr(report_io, "write_report_temp", fail_temp)

    # When / Then
    with pytest.raises(ReportError, match="temp_write"):
        _ = publish_report_batch(pairs)
    assert not output_dir.exists()
    assert not (tmp_path / "nonexistent").exists()


@pytest.mark.parametrize(
    "scenario",
    [
        TempFailure(phase, file_call, kind)
        for phase in ("write", "flush", "fsync")
        for file_call in (1, 2)
        for kind in ("oserror", "interrupt")
    ],
)
def test_report_temp_phase_failure_leaves_prior_pair_and_no_remnants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: TempFailure
) -> None:
    # Given
    pair = _pairs(tmp_path)[0]
    targets = (pair.json_path, pair.markdown_path)
    for index, target in enumerate(targets):
        _ = target.write_text(f"old-{index}")
    before = tuple(target.read_bytes() for target in targets)
    attribute = {
        "write": "write_temp_content",
        "flush": "flush_temp_file",
        "fsync": "sync_temp_file",
    }[scenario.phase]
    functions: dict[str, Callable[[IO[str], str], None]] = {
        "write_temp_content": report_io.write_temp_content,
        "flush_temp_file": report_io.flush_temp_file,
        "sync_temp_file": report_io.sync_temp_file,
    }
    original = functions[attribute]
    calls = 0

    def fail_selected(handle: IO[str], content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == scenario.file_call:
            if scenario.kind == "interrupt":
                raise KeyboardInterrupt
            raise OSError(f"injected {scenario.phase} failure")
        original(handle, content)

    monkeypatch.setattr(report_io, attribute, fail_selected)
    expected = KeyboardInterrupt if scenario.kind == "interrupt" else ReportError

    # When / Then
    with pytest.raises(expected):
        _ = publish_report_batch((pair,))
    assert tuple(target.read_bytes() for target in targets) == before
    assert not tuple(tmp_path.glob(".*.tmp"))
    assert not tuple(tmp_path.glob(".*.backup"))


def test_report_temp_fsync_failure_removes_new_nested_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    output_dir = tmp_path / "nonexistent" / "fsync"
    pair = _pairs(output_dir)[0]

    def fail_fsync(_handle: IO[str], _content: str) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(report_io, "sync_temp_file", fail_fsync)

    # When / Then
    with pytest.raises(ReportError, match="temp_write"):
        _ = publish_report_batch((pair,))
    assert not output_dir.exists()
    assert not (tmp_path / "nonexistent").exists()
