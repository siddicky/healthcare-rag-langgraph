"""The smoke-log redaction, which is what makes the deploy artifact publishable.

It runs against production with real bearer tokens, so every credential shape the
smoke can emit must come out masked. This was an untestable heredoc inside
`deploy.yml` until the release-tag work moved it into a script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.redact_smoke_log import LINE_LIMIT, redact

SECRET = "abcdefghijklmnopqrstuvwxyz0123456789"


def test_authorization_header_value_is_dropped() -> None:
    out = redact(f"> Authorization: Bearer {SECRET}\n< HTTP/1.1 200 OK")

    assert SECRET not in out
    # The header name survives: the log still shows what was sent.
    assert "Authorization:" in out
    assert "200 OK" in out


def test_credential_shapes_outside_headers_are_masked() -> None:
    out = redact(
        "\n".join(
            [
                f"key=lsv2_pt_{SECRET}",
                f"supabase sbp_{SECRET}",
                "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
                f"LANGGRAPH_U1_TOKEN={SECRET}",
                f"COACH_INTERNAL_TOKEN={SECRET}",
                f"OPENAI_API_KEY=sk-{SECRET}",
            ]
        )
    )

    assert SECRET not in out
    assert out.count("[REDACTED") == 6


def test_long_bodies_are_truncated_to_status_and_length() -> None:
    out = redact("x" * (LINE_LIMIT + 1))

    assert "[TRUNCATED len=501]" in out
    assert len(out.splitlines()[0]) < LINE_LIMIT


def test_ordinary_smoke_output_passes_through_unchanged() -> None:
    clean = "check 3/10: thread isolation ... ok\ncheck 4/10: cron denial ... ok"

    assert redact(clean) == clean


def test_cli_writes_a_redacted_copy(tmp_path: Path) -> None:
    src = tmp_path / "raw.log"
    dst = tmp_path / "redacted.log"
    _ = src.write_text(f"Authorization: Bearer {SECRET}\n")

    result = subprocess.run(
        [sys.executable, "scripts/redact_smoke_log.py", "--in", str(src), "--out", str(dst)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert SECRET not in dst.read_text()
    assert SECRET not in result.stdout


def test_a_missing_log_is_not_an_error(tmp_path: Path) -> None:
    # `if: always()` runs this step even when the smoke died before writing.
    dst = tmp_path / "redacted.log"
    _ = subprocess.run(
        [
            sys.executable,
            "scripts/redact_smoke_log.py",
            "--in",
            str(tmp_path / "missing.log"),
            "--out",
            str(dst),
        ],
        check=True,
    )

    assert "no smoke log produced" in dst.read_text()
