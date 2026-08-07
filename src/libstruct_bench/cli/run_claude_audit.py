from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libstruct_bench.audit.claude_runner import ClaudeAuditError, run_claude_audit


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded, read-only Claude conversion and comparison."
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phase", choices=("comparison",), default="comparison")
    parser.add_argument("--model", required=True, help="full immutable Claude model ID")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="high")
    parser.add_argument("--max-budget-usd", type=float, default=20.0)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--claude-executable", default="claude")
    parser.add_argument("--prompt", type=Path)
    parser.add_argument(
        "--skill",
        type=Path,
        default=REPO_ROOT / ".claude" / "skills" / "audit-protocol" / "SKILL.md",
    )
    parser.add_argument("--policy", type=Path, action="append")
    parser.add_argument("--schema", type=Path)
    parser.add_argument(
        "--packet-schema",
        type=Path,
        default=AUDIT_SCHEMAS / "audit_packet.schema.json",
    )
    args = parser.parse_args(argv)
    schema = args.schema or AUDIT_SCHEMAS / "protocol_audit.schema.json"
    prompt = args.prompt or REPO_ROOT / ".claude" / "prompts" / "audit-comparison.md"
    policies = args.policy or [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
    ]
    try:
        result = run_claude_audit(
            packet_dir=args.packet,
            output_dir=args.out,
            output_schema_path=schema,
            packet_schema_path=args.packet_schema,
            prompt_path=prompt,
            skill_path=args.skill,
            policy_paths=policies,
            model=args.model,
            run_id=args.run_id,
            effort=args.effort,
            review_mode="primary",
            max_budget_usd=args.max_budget_usd,
            timeout_seconds=args.timeout_seconds,
            claude_executable=args.claude_executable,
        )
    except ClaudeAuditError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{result.phase.capitalize()} artifact: {result.artifact_path}")
    print(f"Transcript: {result.transcript_path}")
    print(f"Run metadata: {result.metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
