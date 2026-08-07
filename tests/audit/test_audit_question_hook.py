from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "require_audit_question.py"
SKILL = REPO_ROOT / ".claude" / "skills" / "audit-protocol" / "SKILL.md"


def _run_hook(message: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"last_assistant_message": message}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout else {}


def test_audit_question_hook_blocks_stop_after_marked_card() -> None:
    result = _run_hook(
        "Issue 5 of 5\nCurrent value: A\nProposed value: B\n"
        "Evidence: source\nReason and impact: change\n"
        "<!-- audit-question-required -->"
    )
    assert result["decision"] == "block"
    assert "Immediately call AskUserQuestion" in result["reason"]


def test_audit_question_hook_blocks_stop_after_unmarked_full_card() -> None:
    result = _run_hook(
        "Issue 5 of 5 requiring an individual decision\n"
        "Current value (legacy-derived candidate): A\n"
        "Proposed value (primary source): B\n"
        "Evidence — supports current: legacy source\n"
        "Evidence — supports proposed: primary source\n"
        "Reason and impact: changes the terminal state\n"
    )
    assert result["decision"] == "block"
    assert "Immediately call AskUserQuestion" in result["reason"]


def test_audit_question_hook_blocks_before_application_authorization() -> None:
    result = _run_hook(
        "Review finalized and validated.\n"
        "<!-- audit-application-question-required -->"
    )
    assert result["decision"] == "block"
    assert "application authorization" in result["reason"]
    assert "AskUserQuestion" in result["reason"]


def test_audit_question_hook_ignores_normal_completion() -> None:
    assert _run_hook("Audit proposal saved; no human gate is pending.") == {}


def test_audit_skill_registers_scoped_stop_hook() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "Stop:" in text
    assert "require_audit_question.py" in text
