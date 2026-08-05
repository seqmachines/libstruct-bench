#!/usr/bin/env python3
"""Prevent an audit skill turn from stopping after a card but before its selector."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


_MARKER = "<!-- audit-question-required -->"
_ISSUE_HEADING = re.compile(r"\bIssue\s+\d+\s+of\s+\d+\b", re.IGNORECASE)


def _awaits_question(payload: dict[str, Any]) -> bool:
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        return False
    if _MARKER in message:
        return True
    # Backstop for cards rendered by older sessions before the marker rule was
    # loaded. Keep this narrow so ordinary Claude turns are never affected.
    return (
        _ISSUE_HEADING.search(message) is not None
        and "Current value" in message
        and "Proposed value" in message
        and "Evidence" in message
        and ("Reason and impact" in message or "Reason / impact" in message)
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict) or not _awaits_question(payload):
        return 0
    json.dump(
        {
            "decision": "block",
            "reason": (
                "An audit decision card is awaiting interactive input. Do not "
                "repeat or summarize the card. Immediately call AskUserQuestion "
                "with the valid dispositions for this issue before stopping."
            ),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
