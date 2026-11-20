#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


class CheckerInputError(ValueError):
    """Raised when a checker input cannot be interpreted."""


def load_object(path: str, label: str) -> dict[str, Any]:
    candidate = Path(path)
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckerInputError(f"cannot read {label} {candidate}: {error}") from error
    if not isinstance(value, dict):
        raise CheckerInputError(f"{label} must be a JSON object")
    return value


def parser(name: str, *, ledger: bool = False) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=name)
    result.add_argument("--t2", required=True)
    result.add_argument("--t3", required=True)
    if ledger:
        result.add_argument("--ledger", required=True)
    result.add_argument("--format", choices=("json",), default="json")
    return result


def finding(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


def run_checker(
    checker: str,
    argv: list[str] | None,
    check: Callable[..., list[dict[str, str]]],
    *,
    ledger: bool = False,
) -> int:
    args = parser(checker, ledger=ledger).parse_args(argv)
    try:
        t2 = load_object(args.t2, "T2 prediction")
        t3 = load_object(args.t3, "T3 prediction")
        inputs: list[dict[str, Any]] = [t2, t3]
        if ledger:
            inputs.append(load_object(args.ledger, "evidence ledger"))
        findings = sorted(
            check(*inputs),
            key=lambda item: (item["location"], item["code"], item["message"]),
        )
        output = {
            "checker": checker,
            "status": "pass" if not findings else "findings",
            "finding_count": len(findings),
            "findings": findings,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if not findings else 1
    except (CheckerInputError, KeyError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "checker": checker,
                    "status": "error",
                    "finding_count": 0,
                    "findings": [],
                    "error": str(error),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


def workflows(t3: Mapping[str, Any]) -> Iterable[tuple[int, Mapping[str, Any]]]:
    values = t3.get("workflows")
    if not isinstance(values, list):
        raise CheckerInputError("T3 workflows must be an array")
    for index, workflow in enumerate(values):
        if not isinstance(workflow, Mapping):
            raise CheckerInputError(f"T3 workflow {index} must be an object")
        yield index, workflow


def object_list(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise CheckerInputError(f"{label} must be an array of objects")
    return value


def string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CheckerInputError(f"{label} must be an array of strings")
    return value


_COMPLEMENT = str.maketrans(
    "ACGTRYSWKMBDHVNacgtryswkmbdhvn",
    "TGCAYRSWMKVHDBNtgcayrswmkvhdbn",
)


def reverse_complement(value: str) -> str:
    return value.translate(_COMPLEMENT)[::-1]


def explicit_side_sequence(
    side: Mapping[str, Any],
    strands: Mapping[str, Mapping[str, Any]],
) -> str | None:
    strand_id = side.get("strand_id")
    strand = strands.get(strand_id) if isinstance(strand_id, str) else None
    if strand is None:
        return None
    by_id = {
        segment.get("segment_id"): segment
        for segment in object_list(strand.get("segments"), f"strand {strand_id} segments")
    }
    pieces: list[str] = []
    for segment_id in string_list(side.get("segment_ids"), "paired side segment_ids"):
        segment = by_id.get(segment_id)
        if segment is None or not isinstance(segment.get("sequence"), str):
            return None
        pieces.append(segment["sequence"])
    return "".join(pieces)


def escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
