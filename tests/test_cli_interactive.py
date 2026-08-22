from __future__ import annotations

import builtins
import os
import subprocess
import sys
from dataclasses import dataclass

import pytest

from healthcare_rag.cli import interactive


@dataclass(slots=True)  # noqa: MUTABLE_OK - records cleanup calls.
class RecordingEngine:
    close_count: int = 0

    async def aclose(self) -> None:
        self.close_count += 1


def _closed_input(_: str) -> str:
    raise EOFError


def _quit_input(_: str) -> str:
    return "quit"


def _interrupted_input(_: str) -> str:
    raise KeyboardInterrupt


@pytest.mark.parametrize("module_name", ("healthcare_rag", "healthcare_rag.cli.interactive"))
def test_module_entries_return_nonzero_for_invalid_initialization_config(
    module_name: str,
) -> None:
    environment = os.environ | {
        "HC_RAG_QUERY_RESPONSE_ARM": "bogus",
        "LANGSMITH_TRACING": "false",
    }

    completed = subprocess.run(
        [sys.executable, "-m", module_name],
        input="",
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "PRIVACY_OR_RUNTIME_INITIALIZATION_FAILED" in output
    assert "bogus" not in output
    assert "Traceback" not in output


@pytest.mark.asyncio
async def test_interactive_main_quit_closes_engine_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = RecordingEngine()

    async def build_recording_engine() -> RecordingEngine:
        return engine

    monkeypatch.setattr(interactive, "build_engine", build_recording_engine)
    monkeypatch.setattr(builtins, "input", _quit_input)

    await interactive.interactive_main()

    output = capsys.readouterr().out
    assert "Ending session..." in output
    assert "Closing connection..." in output
    assert "✓ Connection closed successfully." in output
    assert "Thank you for using Medical RAG Assistant!" in output
    assert engine.close_count == 1


@pytest.mark.asyncio
async def test_interactive_main_keyboard_interrupt_closes_engine_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = RecordingEngine()

    async def build_recording_engine() -> RecordingEngine:
        return engine

    monkeypatch.setattr(interactive, "build_engine", build_recording_engine)
    monkeypatch.setattr(builtins, "input", _interrupted_input)

    await interactive.interactive_main()

    output = capsys.readouterr().out
    assert "Session interrupted by user. Shutting down..." in output
    assert "Closing connection..." in output
    assert "✓ Connection closed successfully." in output
    assert "Thank you for using Medical RAG Assistant!" in output
    assert engine.close_count == 1


@pytest.mark.asyncio
async def test_interactive_main_eof_closes_engine_without_fatal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = RecordingEngine()

    async def build_recording_engine() -> RecordingEngine:
        return engine

    monkeypatch.setattr(interactive, "build_engine", build_recording_engine)
    monkeypatch.setattr(builtins, "input", _closed_input)

    await interactive.interactive_main()

    output = capsys.readouterr().out
    assert "Input closed. Ending session..." in output
    assert "Closing connection..." in output
    assert "✓ Connection closed successfully." in output
    assert "Thank you for using Medical RAG Assistant!" in output
    assert "RUNTIME_FATAL_ERROR" not in output
    assert engine.close_count == 1
