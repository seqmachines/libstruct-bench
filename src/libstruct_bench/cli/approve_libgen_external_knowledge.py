from __future__ import annotations

import argparse
from pathlib import Path

from libstruct_bench.audit.external_knowledge import load_json, write_json
from libstruct_bench.audit.external_knowledge_harbor import (
    build_external_knowledge_final_approval,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record a detached human approval for a reviewed Libgen "
            "external-knowledge package."
        )
    )
    parser.add_argument("--review-candidate", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--authorize-experiment-run",
        action="store_true",
        help="also authorize starting paid trials (off by default)",
    )
    args = parser.parse_args(argv)

    output = Path(args.out)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite final approval: {output}")
    review_candidate = load_json(Path(args.review_candidate))
    approval = build_external_knowledge_final_approval(
        review_candidate=review_candidate,
        reviewer_identity=args.reviewer,
        approved_at=args.approved_at,
        rationale=args.rationale,
        experiment_run_authorized=args.authorize_experiment_run,
    )
    write_json(output, approval)
    print(output)
    print(approval["approval_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
