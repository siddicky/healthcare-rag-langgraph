from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, override


@dataclass(frozen=True, slots=True)
class ReportError(Exception):
    phase: str
    cause: OSError | OverflowError | ValueError
    recovery_paths: tuple[Path, ...] = ()

    @override
    def __str__(self) -> str:
        recovery = ", ".join(str(path) for path in self.recovery_paths)
        suffix = f"; recover with recover_report_files: {recovery}" if recovery else ""
        return f"routing report {self.phase} failed: {self.cause}{suffix}"


@dataclass(frozen=True, slots=True)
class ReportInterrupted(KeyboardInterrupt):
    phase: str
    recovery_paths: tuple[Path, ...]

    @override
    def __str__(self) -> str:
        recovery = ", ".join(str(path) for path in self.recovery_paths)
        return f"routing report {self.phase} interrupted; recover with recover_report_files: {recovery}"


@dataclass(frozen=True, slots=True)
class ReportPair:
    json_path: Path
    json_content: str
    markdown_path: Path
    markdown_content: str


def replace_report_file(source: Path, target: Path) -> None:
    os.replace(source, target)


def write_temp_content(handle: IO[str], content: str) -> None:
    _ = handle.write(content)


def flush_temp_file(handle: IO[str], _content: str) -> None:
    handle.flush()


def sync_temp_file(handle: IO[str], _content: str) -> None:
    os.fsync(handle.fileno())


def write_report_temp(target: Path, content: str) -> Path:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            write_temp_content(handle.file, content)
            flush_temp_file(handle.file, content)
            sync_temp_file(handle.file, content)
    except (OSError, KeyboardInterrupt):
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return temporary


def _backup_path(target: Path) -> Path:
    return target.parent / f".{target.name}.backup"


def _existing_backups(moved: list[tuple[Path, Path]]) -> tuple[Path, ...]:
    return tuple(sorted(backup for backup, _ in moved if backup.exists()))


def _rollback(published: list[Path], moved: list[tuple[Path, Path]]) -> None:
    for target in reversed(published):
        target.unlink(missing_ok=True)
    for backup, target in reversed(moved):
        replace_report_file(backup, target)


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _cleanup_empty_directories(directories: tuple[Path, ...]) -> None:
    for directory in reversed(directories):
        if directory.exists() and next(directory.iterdir(), None) is None:
            directory.rmdir()


def _prepare_directories(targets: tuple[Path, ...]) -> tuple[Path, ...]:
    missing: set[Path] = set()
    for target in targets:
        directory = target.parent
        while not directory.exists():
            missing.add(directory)
            directory = directory.parent
    created: list[Path] = []
    try:
        for directory in sorted(missing, key=lambda path: len(path.parts)):
            directory.mkdir()
            created.append(directory)
    except OSError as exc:
        _cleanup_empty_directories(tuple(created))
        raise ReportError("prepare", exc) from exc
    return tuple(created)


def recover_report_files(recovery_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    restored: list[Path] = []
    for backup in sorted(recovery_paths):
        name = backup.name
        if not name.startswith(".") or not name.endswith(".backup"):
            error = ValueError(f"invalid report backup path: {backup}")
            raise ReportError("recovery", error, recovery_paths)
        target = backup.with_name(name[1 : -len(".backup")])
        try:
            replace_report_file(backup, target)
        except OSError as exc:
            remaining = tuple(path for path in recovery_paths if path.exists())
            raise ReportError("recovery", exc, remaining) from exc
        restored.append(target)
    return tuple(restored)


def _publish_transaction(
    targets: tuple[Path, ...],
    contents: tuple[str, ...],
    planned_backups: tuple[Path, ...],
) -> tuple[Path, ...]:
    temporary: list[Path] = []
    try:
        for target, content in zip(targets, contents, strict=True):
            temporary.append(write_report_temp(target, content))
    except OSError as exc:
        _cleanup(temporary)
        raise ReportError("temp_write", exc) from exc
    except KeyboardInterrupt:
        _cleanup(temporary)
        raise

    moved: list[tuple[Path, Path]] = []
    published: list[Path] = []
    backups: list[Path] = []
    retain_backups = False
    try:
        for target, backup in zip(targets, planned_backups, strict=True):
            if target.exists():
                replace_report_file(target, backup)
                moved.append((backup, target))
                backups.append(backup)
        for source, target in zip(temporary, targets, strict=True):
            replace_report_file(source, target)
            published.append(target)
    except OSError as exc:
        try:
            _rollback(published, moved)
        except OSError as rollback_error:
            retain_backups = True
            recovery_paths = _existing_backups(moved)
            raise ReportError("rollback", rollback_error, recovery_paths) from exc
        except KeyboardInterrupt as rollback_interrupt:
            retain_backups = True
            recovery_paths = _existing_backups(moved)
            raise ReportInterrupted("rollback", recovery_paths) from rollback_interrupt
        raise ReportError("publish", exc) from exc
    except KeyboardInterrupt as interrupt:
        try:
            _rollback(published, moved)
        except OSError as rollback_error:
            retain_backups = True
            recovery_paths = _existing_backups(moved)
            raise ReportError("rollback", rollback_error, recovery_paths) from interrupt
        except KeyboardInterrupt as rollback_interrupt:
            retain_backups = True
            recovery_paths = _existing_backups(moved)
            raise ReportInterrupted("rollback", recovery_paths) from rollback_interrupt
        raise
    finally:
        _cleanup(temporary)
        if not retain_backups:
            _cleanup(backups)
    return targets


def publish_report_batch(
    pairs: tuple[ReportPair, ...],
) -> tuple[tuple[Path, Path], ...]:
    if not pairs:
        raise ReportError("prepare", ValueError("at least one report pair is required"))
    targets = tuple(
        path for pair in pairs for path in (pair.json_path, pair.markdown_path)
    )
    if len(set(targets)) != len(targets):
        raise ReportError("prepare", ValueError("report targets must be unique"))
    planned_backups = tuple(_backup_path(target) for target in targets)
    stale = tuple(sorted(backup for backup in planned_backups if backup.exists()))
    if stale:
        error = OSError("report backups require recovery")
        raise ReportError("recovery_required", error, stale)
    created_directories = _prepare_directories(targets)
    contents = tuple(
        content
        for pair in pairs
        for content in (pair.json_content, pair.markdown_content)
    )
    try:
        published = _publish_transaction(targets, contents, planned_backups)
    except (ReportError, KeyboardInterrupt):
        _cleanup_empty_directories(created_directories)
        raise
    return tuple(
        (published[index], published[index + 1])
        for index in range(0, len(published), 2)
    )


def publish_report_pair(pair: ReportPair) -> tuple[Path, Path]:
    return publish_report_batch((pair,))[0]
