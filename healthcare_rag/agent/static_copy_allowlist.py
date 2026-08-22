from __future__ import annotations

from typing import Final

STATIC_COPY_ALLOWLIST: Final = frozenset(
    {
        "Cancel reminder",
        "Change schedule",
        "Confirm",
        "Decline",
        "Log injection",
        "Log weight",
        "Missed",
        "On track",
        "Progress",
        "Set reminder",
        "Summary",
        "View schedule",
    }
)

DISPATCH_ALLOWLIST: Final = frozenset(
    {
        "cancel_reminder",
        "change_schedule",
        "confirm",
        "decline",
        "log_injection",
        "log_weight",
        "set_reminder",
        "upload_document",
        "view_schedule",
    }
)

__all__ = ["DISPATCH_ALLOWLIST", "STATIC_COPY_ALLOWLIST"]
