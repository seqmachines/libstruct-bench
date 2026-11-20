"""Shared evidence selection for direct API and Harbor Task 1 runs."""

from pathlib import Path
from typing import Any


PDF_ONLY_INSTRUCTION = """This round provides PDF inputs only. Spreadsheet supplements are excluded.
Use only the provided PDFs; do not retrieve excluded supplements or fill gaps
with remembered sequences. Apply the same source-grounding rules to every protocol."""


def input_paths(protocol: dict[str, Any]) -> list[str]:
    paths = protocol.get("input_paths")
    if paths is None:
        paths = [protocol.get("input_path")]
    if not isinstance(paths, list) or not paths:
        raise ValueError("protocol entry input_paths must be a non-empty list")
    if any(not isinstance(path, str) or not path.strip() for path in paths):
        raise ValueError("every protocol input path must be a non-empty string")
    return [path.strip() for path in paths]


def validate_input_policy(config: dict[str, Any]) -> None:
    policy = config.get("input_policy")
    if policy not in (None, "pdf_only"):
        raise ValueError(f"unsupported input_policy: {policy!r}")
    for protocol in config.get("protocols", []):
        paths = input_paths(protocol)
        if policy == "pdf_only" and any(Path(path).suffix.lower() != ".pdf" for path in paths):
            raise ValueError(f"pdf_only input policy violated for {protocol.get('protocol_id')}")


def input_policy_instruction(policy: str | None) -> str:
    return PDF_ONLY_INSTRUCTION if policy == "pdf_only" else ""
