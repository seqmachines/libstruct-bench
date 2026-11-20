#!/usr/bin/env python3
"""Prevent an audit skill turn from stopping before a required selector."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


_MARKER = "<!-- audit-question-required -->"
_APPLICATION_MARKER = "<!-- audit-application-question-required -->"
_CAPABILITY_REVIEW_MARKER = "<!-- capability-review-question-required -->"
_ISSUE_HEADING = re.compile(r"\bIssue\s+\d+\s+of\s+\d+\b", re.IGNORECASE)


def _pending_gate(payload: dict[str, Any]) -> str | None:
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        return None
    if _APPLICATION_MARKER in message:
        return "application"
    if _CAPABILITY_REVIEW_MARKER in message:
        return "capability_review"
    if _MARKER in message:
        return "issue"
    # Backstop for cards rendered by older sessions before the marker rule was
    # loaded. Keep this narrow so ordinary Claude turns are never affected.
    if (
        _ISSUE_HEADING.search(message) is not None
        and "Current value" in message
        and "Proposed value" in message
        and "Evidence" in message
        and ("Reason and impact" in message or "Reason / impact" in message)
    ):
        return "issue"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    gate = _pending_gate(payload)
    if gate is None:
        return 0
    if gate == "application":
        reason = (
            "A finalized audit is awaiting separate application authorization. "
            "Immediately call AskUserQuestion to offer deterministic application "
            "and promotion, or leaving the finalized review unapplied."
        )
    elif gate == "capability_review":
        reason = (
            "A capability-review section is awaiting human input. Do not "
            "repeat or summarize the section. Immediately call AskUserQuestion "
            "with the section or final-proposal choices before stopping."
        )
    else:
        reason = (
            "An audit decision card is awaiting interactive input. Do not "
            "repeat or summarize the card. Immediately call AskUserQuestion "
            "with the valid dispositions for this issue before stopping."
        )
    json.dump(
        {
            "decision": "block",
            "reason": reason,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
