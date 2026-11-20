"""Prepare new, ground-truth-only Hugging Face staging packages; never upload."""
import argparse
import json
from pathlib import Path

from libstruct_bench.audit.hf_groundtruth import prepare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True, help='New private staging directory')
    args = parser.parse_args()
    print(json.dumps(prepare(private=args.private_root.resolve(), repo=args.repo_root.resolve(),
                             output=args.output.resolve()), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
