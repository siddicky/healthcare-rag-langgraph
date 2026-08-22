from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator

import httpx
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "oracle: tests that hit the pinned oracle server (need ORACLE=1 or CI)")


ORACLE_PORT = 2025
ORACLE_URL = f"http://127.0.0.1:{ORACLE_PORT}"
ORACLE_CONFIG = "tests/server/oracle/langgraph.json"
ORACLE_LANGGRAPH = "tests/server/oracle/.venv/bin/langgraph"


@pytest.fixture(scope="session")
def oracle_server() -> None:
    """Start pinned oracle via subprocess when ORACLE=1, else skip."""
    if os.getenv("ORACLE") != "1" and os.getenv("CI") not in ("true", "1"):
        pytest.skip("oracle tests require ORACLE=1 (or CI)")
    proc = subprocess.Popen(
        [ORACLE_LANGGRAPH, "dev", "--config", ORACLE_CONFIG, "--port", str(ORACLE_PORT), "--no-browser", "--no-reload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
                pytest.fail(f"oracle subprocess exited early with {proc.returncode}")
        if not ready:
            proc.terminate()
            pytest.fail("oracle did not become ready on :2025 within 30s")
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
