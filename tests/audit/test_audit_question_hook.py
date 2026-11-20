from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_WORKSPACE = REPO_ROOT / "paper" / "audit"
HOOK = AUDIT_WORKSPACE / ".claude" / "hooks" / "require_audit_question.py"
SKILLS = AUDIT_WORKSPACE / ".claude" / "skills"


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


def test_audit_question_hook_blocks_after_capability_review_section() -> None:
    result = _run_hook(
        "T3 molecular states — all ground-truth and agent states shown.\n"
        "<!-- capability-review-question-required -->"
    )
    assert result["decision"] == "block"
    assert "capability-review section" in result["reason"]
    assert "AskUserQuestion" in result["reason"]


def test_audit_question_hook_ignores_normal_completion() -> None:
    assert _run_hook("Audit proposal saved; no human gate is pending.") == {}


@pytest.mark.parametrize("skill", ["audit-protocol", "review-capability"])
def test_skill_stop_hook_runs_from_another_directory(tmp_path, skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "Stop:" in frontmatter
    hook_fields = dict(
        line.strip().split(": ", 1)
        for line in frontmatter.splitlines()
        if line.strip().startswith(("command: ", "args: "))
    )
    args = [
        arg.replace("${CLAUDE_PROJECT_DIR}", str(AUDIT_WORKSPACE))
        for arg in json.loads(hook_fields["args"])
    ]
    result = subprocess.run(
        [hook_fields["command"], *args],
        cwd=tmp_path,
        input=json.dumps({"last_assistant_message": "<!-- audit-question-required -->"}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["decision"] == "block"
    if skill == "review-capability":
        assert "AskUserQuestion" in text
        assert "<!-- capability-review-question-required -->" in text
