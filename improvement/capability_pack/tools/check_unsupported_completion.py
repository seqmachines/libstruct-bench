#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from _common import escape_pointer, finding, object_list, run_checker


SENSITIVE_KEYS = {
    "sequence",
    "sequence_architecture",
    "length",
    "placeholder",
    "orientation",
    "orientation_to_source",
    "modifications",
    "strand_architecture",
    "relationship",
    "kind",
    "operation",
    "substrate_state_ids",
    "product_state_ids",
    "carried_forward_product_ids",
    "discarded_product_ids",
    "final_outputs",
    "discontinuities",
}
SUPPORTED = {"explicit", "derivable"}


def _sensitive_pointers(value: Any, path: str = "") -> list[str]:
    pointers: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = path + "/" + escape_pointer(str(key))
            if key in SENSITIVE_KEYS and child not in (None, [], "", "unknown"):
                pointers.append(child_path)
            pointers.extend(_sensitive_pointers(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            pointers.extend(_sensitive_pointers(child, path + f"/{index}"))
    return pointers


def check(
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> list[dict[str, str]]:
    if ledger.get("schema_version") != "libstruct.evidence_ledger.v1":
        raise ValueError("unsupported evidence-ledger schema_version")
    claims = object_list(ledger.get("claims"), "evidence ledger claims")
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    findings: list[dict[str, str]] = []
    for index, claim in enumerate(claims):
        target = claim.get("target")
        pointer = claim.get("json_pointer")
        claim_id = claim.get("claim_id")
        if target not in {"t2", "t3"} or not isinstance(pointer, str) or not pointer.startswith("/") or not isinstance(claim_id, str):
            raise ValueError(f"invalid evidence claim at index {index}")
        key = (target, pointer)
        if key in indexed:
            raise ValueError(f"duplicate evidence claim for {target}:{pointer}")
        indexed[key] = claim
    for target, document in (("t2", t2), ("t3", t3)):
        for pointer in sorted(set(_sensitive_pointers(document))):
            claim = indexed.get((target, pointer))
            location = f"{target}:{pointer}"
            if claim is None:
                findings.append(finding("missing_evidence_claim", location, "completion-sensitive value has no exact evidence-ledger claim"))
                continue
            support = claim.get("support")
            locators = claim.get("source_locators")
            if support not in SUPPORTED:
                findings.append(finding("unsupported_completion", location, f"claim support is {support!r}, not explicit or derivable"))
            if not isinstance(locators, list) or not locators or any(not isinstance(item, str) or not item.strip() for item in locators):
                findings.append(finding("missing_source_locator", location, "supported claim requires at least one source locator"))
    return findings


if __name__ == "__main__":
    raise SystemExit(run_checker("check_unsupported_completion", None, check, ledger=True))
