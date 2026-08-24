from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

import httpx
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "oracle: tests that hit the pinned oracle server (need ORACLE=1 or CI)")


# 2025 is the langgraph dev default; ORACLE_PORT overrides it for a machine
# where something else already holds that port (langgraph dev would silently
# pick a free one and the readiness probe would then never find the server).
ORACLE_PORT = int(os.getenv("ORACLE_PORT", "2025"))
ORACLE_URL = f"http://127.0.0.1:{ORACLE_PORT}"
ORACLE_CONFIG = "tests/server/oracle/langgraph.json"
ORACLE_LANGGRAPH = "tests/server/oracle/.venv/bin/langgraph"


@pytest.fixture(scope="session")
def oracle_server() -> None:
    """Start pinned oracle via subprocess when ORACLE=1, else skip."""
    explicitly_requested = os.getenv("ORACLE") == "1"
    if not explicitly_requested and os.getenv("CI") not in ("true", "1"):
        pytest.skip("oracle tests require ORACLE=1 (or CI)")
    # CI is set on every GitHub Actions job, but only the oracle job builds the
    # pinned venv. Gating on CI alone made this suite try to run — and error with
    # FileNotFoundError — in any other workflow that happens to collect it.
    # ORACLE=1 is an explicit request, so a missing venv there is a real failure.
    if not pathlib.Path(ORACLE_LANGGRAPH).exists():
        if explicitly_requested:
            pytest.fail(
                f"ORACLE=1 was set but the pinned oracle venv is missing at {ORACLE_LANGGRAPH}. "
                "Build it first (see .github/workflows/server-parity.yml, 'Build pinned oracle venv')."
            )
        pytest.skip(f"pinned oracle venv not built in this job ({ORACLE_LANGGRAPH})")
    # Keep the server's own output: a dead oracle is otherwise reported as a
    # bare exit code, and the cause (a dependency downgrade, a missing env var)
    # is only in the log the subprocess wrote.
    log_dir = tempfile.TemporaryDirectory(prefix="oracle-")
    log_path = pathlib.Path(log_dir.name) / "oracle.log"

    def tail(limit: int = 40) -> str:
        # Read through a fresh handle: the child owns the write end's offset.
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-limit:]) or "(no output)"

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [ORACLE_LANGGRAPH, "dev", "--config", ORACLE_CONFIG, "--port", str(ORACLE_PORT), "--no-browser", "--no-reload"],
            stdout=log,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if sys.platform != "win32" else None,
        )
    try:
        deadline = time.time() + 30
        ready = False
        while time.time() < deadline:
            try:
                with httpx.Client(timeout=1) as c:
                    r = c.get(f"{ORACLE_URL}/ok")
                    if r.status_code == 200:
                        ready = True
                        break
            except Exception:
                pass
            time.sleep(1)
            if proc.poll() is not None:
                pytest.fail(
                    f"oracle subprocess exited early with {proc.returncode}\n{tail()}"
                )
        if not ready:
            proc.terminate()
            pytest.fail(
                f"oracle did not become ready on :{ORACLE_PORT} within 30s\n{tail()}"
            )
        yield
    finally:
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
        log_dir.cleanup()
