"""`copy_to_clipboard` — one safe headless client-side tool.

The model calls this to ask the member's browser to copy a short text to the
clipboard. Execution is client-side: the server interrupts with a headless-tool
payload ``{type:"tool", tool_call:{name,args,id}}`` which ``useStream`` on the
frontend recognises via ``isHeadlessToolInterrupt`` and auto-resumes after
``navigator.clipboard.writeText`` succeeds. Unknown-tool interrupts fail closed
with telemetry on the client — never a crash or raw copy of unregistered tools.

Notes:
- Text may be PHI-sensitive; the server never logs the raw value. Telemetry
  on this module is limited to tool name / arg length, not content.
- The tool intentionally carries no store / auth dependency — it is a pure
  client-side copy hop. The interrupt payload is the only server→client
  signal.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool("copy_to_clipboard")
def copy_to_clipboard(text: str) -> str:
    """Copy text to the member's clipboard (client-side)"""
    resume_value: Any = interrupt(
        {
            "type": "tool",
            "tool_call": {
                "id": str(uuid4()),
                "name": "copy_to_clipboard",
                "args": {"text": text},
            },
        }
    )
    # Client ``execute`` returns ``"copied"`` on success or
    # ``{"error": "..."}`` on unknown-tool / failure (headless helper
    # contract). Normalise to a short ToolMessage string; never echo raw
    # text back beyond the success marker — the client already copied it.
    if isinstance(resume_value, dict) and "error" in resume_value:
        return f"Copy failed: {resume_value['error']}"
    if isinstance(resume_value, str) and resume_value:
        return resume_value
    if isinstance(resume_value, dict):
        # Defensive: some resumes arrive as {toolCallId: result}
        for value in resume_value.values():
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict) and "error" in value:
                return f"Copy failed: {value['error']}"
    return "copied"


__all__ = ["copy_to_clipboard"]
