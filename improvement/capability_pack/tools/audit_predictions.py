#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import check_product_conservation
import check_strand_pairing
import check_typed_edges
import check_unsupported_completion
from work_record import (
    ACTIVE_DISPOSITIONS,
    WorkRecordError,
    load_object,
    object_list,
    resolve_pointer,
    validate_work_record,
)


REPORT_SCHEMA_VERSION = "libstruct.libgen_capability_audit_report.v1"
CONTROL_IDS = (
    "evidence_support",
    "physical_oligo_linkage",
    "product_conservation",
    "source_reconciliation",
    "state_event_projection",
    "strand_pairing",
    "terminal_reachability",
    "typed_edges",
    "work_record_compile",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete deterministic LibGen capability audit."
    )
    parser.add_argument("--work-record", required=True)
    parser.add_argument("--t2")
    parser.add_argument("--t3")
    parser.add_argument("--format", choices=("json",), default="json")
    return parser


def _finding(
    control_id: str,
    code: str,
    location: str,
    message: str,
) -> dict[str, str]:
    return {
        "control_id": control_id,
        "code": code,
        "location": location,
        "message": message,
    }


def _entity_pointers(t2: Mapping[str, Any], t3: Mapping[str, Any]) -> list[tuple[str, str]]:
    pointers = [("t2", f"/oligos/{index}") for index, _ in enumerate(t2.get("oligos", []))]
    for wi, workflow in enumerate(t3.get("workflows", [])):
        base = f"/workflows/{wi}"
        pointers.append(("t3", base))
        pointers.extend(
            ("t3", f"{base}/states/{index}")
            for index, _ in enumerate(workflow.get("states", []))
        )
        pointers.extend(
            ("t3", f"{base}/transitions/{index}")
            for index, _ in enumerate(workflow.get("transitions", []))
        )
        pointers.extend(
            ("t3", f"{base}/final_outputs/{index}")
            for index, _ in enumerate(workflow.get("final_outputs", []))
        )
    return pointers


def _source_reconciliation(
    record: Mapping[str, Any],
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    documents = {"t2": t2, "t3": t3}
    modeled: set[tuple[str, str]] = set()
    for index, source in enumerate(record["source_coverage"]):
        if source["coverage_status"] != "inspected":
            findings.append(
                _finding(
                    "source_reconciliation",
                    "source_coverage_incomplete",
                    f"/source_coverage/{index}/coverage_status",
                    "every available target source must be fully inspected",
                )
            )
    for index, item in enumerate(record["inventory"]):
        base = f"/inventory/{index}"
        disposition = item["disposition"]
        if disposition == "unresolved":
            findings.append(
                _finding(
                    "source_reconciliation",
                    "inventory_unresolved",
                    f"{base}/disposition",
                    "inventory item remains unresolved",
                )
            )
        if disposition in ACTIVE_DISPOSITIONS and item["support"] not in {
            "explicit",
            "derivable",
        }:
            findings.append(
                _finding(
                    "source_reconciliation",
                    "inventory_active_without_support",
                    f"{base}/support",
                    "a modeled, merged, or folded item requires explicit or derivable support",
                )
            )
        for pointer in item["target_pointers"]:
            key = (pointer["target"], pointer["json_pointer"])
            try:
                resolve_pointer(documents[key[0]], key[1])
            except WorkRecordError:
                findings.append(
                    _finding(
                        "source_reconciliation",
                        "inventory_target_missing",
                        f"{base}/target_pointers",
                        f"inventory target does not resolve: {key[0]}:{key[1]}",
                    )
                )
                continue
            modeled.add(key)
            if (
                pointer["target"] == "t2"
                and pointer["json_pointer"].count("/") == 2
                and item["usage_scope"] != "physical_library_generation"
            ):
                findings.append(
                    _finding(
                        "source_reconciliation",
                        "out_of_scope_oligo_modeled",
                        f"{base}/usage_scope",
                        "only a physical library-generation oligo may map to a T2 family",
                    )
                )
    for target, pointer in _entity_pointers(t2, t3):
        if (target, pointer) not in modeled:
            findings.append(
                _finding(
                    "source_reconciliation",
                    f"unreconciled_{target}_entity",
                    f"{target}:{pointer}",
                    "modeled entity has no active inventory disposition",
                )
            )
    return findings


def _physical_linkage(
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    oligo_ids = {
        item.get("oligo_id")
        for item in object_list(t2.get("oligos"), "T2 oligos")
        if isinstance(item.get("oligo_id"), str)
    }
    used: set[str] = set()
    for wi, workflow in enumerate(object_list(t3.get("workflows"), "T3 workflows")):
        for ti, transition in enumerate(
            object_list(workflow.get("transitions"), f"workflow {wi} transitions")
        ):
            for oligo_id in transition.get("oligo_ids", []):
                if isinstance(oligo_id, str):
                    used.add(oligo_id)
        for si, state in enumerate(object_list(workflow.get("states"), f"workflow {wi} states")):
            for strand_i, strand in enumerate(object_list(state.get("strands"), f"state {si} strands")):
                for segment_i, segment in enumerate(object_list(strand.get("segments"), f"strand {strand_i} segments")):
                    for derivation in segment.get("oligo_derivations", []):
                        oligo_id = derivation.get("oligo_id") if isinstance(derivation, Mapping) else None
                        if not isinstance(oligo_id, str):
                            continue
                        used.add(oligo_id)
                        if oligo_id not in oligo_ids:
                            findings.append(
                                _finding(
                                    "physical_oligo_linkage",
                                    "unknown_derivation_oligo",
                                    f"/workflows/{wi}/states/{si}/strands/{strand_i}/segments/{segment_i}/oligo_derivations",
                                    f"segment derivation references unknown T2 oligo {oligo_id!r}",
                                )
                            )
    for oligo_id in sorted(oligo_ids - used):
        findings.append(
            _finding(
                "physical_oligo_linkage",
                "unused_t2_oligo",
                f"t2:oligo_id:{oligo_id}",
                "T2 family is neither physically used by a transition nor linked to a segment",
            )
        )
    return findings


def _state_event_projection(
    record: Mapping[str, Any],
    t3: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    signatures = {
        (item["workflow_id"], item["state_id"]): item
        for item in record["state_signatures"]
    }
    events = {
        (item["workflow_id"], item["transition_id"]): item
        for item in record["event_records"]
    }
    actual_states: set[tuple[str, str]] = set()
    actual_events: set[tuple[str, str]] = set()
    for wi, workflow in enumerate(object_list(t3.get("workflows"), "T3 workflows")):
        workflow_id = workflow.get("workflow_id")
        if not isinstance(workflow_id, str):
            continue
        for si, state in enumerate(object_list(workflow.get("states"), f"workflow {wi} states")):
            state_id = state.get("state_id")
            if not isinstance(state_id, str):
                continue
            key = (workflow_id, state_id)
            actual_states.add(key)
            signature = signatures.get(key)
            location = f"/workflows/{wi}/states/{si}"
            if signature is None:
                findings.append(
                    _finding(
                        "state_event_projection",
                        "missing_state_signature",
                        location,
                        "modeled state has no deterministic state signature",
                    )
                )
                continue
            expected_strands = [
                {
                    "strand_id": strand.get("strand_id"),
                    "molecule_type": strand.get("molecule_type"),
                    "segment_ids": [segment.get("segment_id") for segment in strand.get("segments", [])],
                }
                for strand in state.get("strands", [])
            ]
            comparisons = {
                "strand_architecture": state.get("strand_architecture"),
                "reference_strand_id": state.get("reference_strand_id"),
                "strands": expected_strands,
                "paired_region_ids": [item.get("paired_region_id") for item in state.get("paired_regions", [])],
                "discontinuity_ids": [item.get("discontinuity_id") for item in state.get("discontinuities", [])],
            }
            for field, actual in comparisons.items():
                if signature.get(field) != actual:
                    findings.append(
                        _finding(
                            "state_event_projection",
                            "state_signature_mismatch",
                            f"{location}/{field}",
                            "state JSON differs from its recorded state signature",
                        )
                    )
        for ti, transition in enumerate(
            object_list(workflow.get("transitions"), f"workflow {wi} transitions")
        ):
            transition_id = transition.get("transition_id")
            if not isinstance(transition_id, str):
                continue
            key = (workflow_id, transition_id)
            actual_events.add(key)
            event = events.get(key)
            location = f"/workflows/{wi}/transitions/{ti}"
            if event is None:
                findings.append(
                    _finding(
                        "state_event_projection",
                        "missing_event_record",
                        location,
                        "modeled transition has no deterministic event record",
                    )
                )
                continue
            for field in (
                "operation",
                "substrate_state_ids",
                "product_state_ids",
                "carried_forward_product_ids",
                "discarded_product_ids",
                "oligo_ids",
            ):
                if event.get(field) != transition.get(field):
                    findings.append(
                        _finding(
                            "state_event_projection",
                            "event_record_mismatch",
                            f"{location}/{field}",
                            "transition JSON differs from its recorded molecular event",
                        )
                    )
    for workflow_id, state_id in sorted(set(signatures) - actual_states):
        findings.append(
            _finding(
                "state_event_projection",
                "orphan_state_signature",
                f"state_signatures:{workflow_id}:{state_id}",
                "state signature does not map to a modeled state",
            )
        )
    for workflow_id, transition_id in sorted(set(events) - actual_events):
        findings.append(
            _finding(
                "state_event_projection",
                "orphan_event_record",
                f"event_records:{workflow_id}:{transition_id}",
                "event record does not map to a modeled transition",
            )
        )
    return findings


def _terminal_checks(t3: Mapping[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for wi, workflow in enumerate(object_list(t3.get("workflows"), "T3 workflows")):
        states = {
            item.get("state_id")
            for item in object_list(workflow.get("states"), f"workflow {wi} states")
            if isinstance(item.get("state_id"), str)
        }
        finals = [
            item.get("state_id")
            for item in object_list(workflow.get("final_outputs"), f"workflow {wi} final_outputs")
        ]
        duplicates = sorted(item for item, count in Counter(finals).items() if count > 1)
        if duplicates:
            findings.append(
                _finding(
                    "terminal_reachability",
                    "duplicate_terminal",
                    f"/workflows/{wi}/final_outputs",
                    "terminal states are repeated: " + ", ".join(duplicates),
                )
            )
        unknown = sorted(item for item in finals if item not in states)
        if unknown:
            findings.append(
                _finding(
                    "terminal_reachability",
                    "unknown_terminal",
                    f"/workflows/{wi}/final_outputs",
                    "terminal states do not resolve: " + ", ".join(unknown),
                )
            )
        discarded = {
            state_id
            for transition in workflow.get("transitions", [])
            for state_id in transition.get("discarded_product_ids", [])
        }
        invalid = sorted(set(finals) & discarded)
        if invalid:
            findings.append(
                _finding(
                    "terminal_reachability",
                    "discarded_terminal",
                    f"/workflows/{wi}/final_outputs",
                    "discarded products cannot be terminal: " + ", ".join(invalid),
                )
            )
    return findings


def _wrapped(control_id: str, values: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        _finding(control_id, item["code"], item["location"], item["message"])
        for item in values
    ]


def audit(
    record: Mapping[str, Any],
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
) -> dict[str, Any]:
    validate_work_record(record)
    findings: list[dict[str, str]] = []
    if record["drafts"]["t2"] != t2:
        findings.append(
            _finding(
                "work_record_compile",
                "compiled_t2_mismatch",
                "/drafts/t2",
                "compiled T2 differs from the work-record draft",
            )
        )
    if record["drafts"]["t3"] != t3:
        findings.append(
            _finding(
                "work_record_compile",
                "compiled_t3_mismatch",
                "/drafts/t3",
                "compiled T3 differs from the work-record draft",
            )
        )
    findings.extend(_source_reconciliation(record, t2, t3))
    findings.extend(_physical_linkage(t2, t3))
    findings.extend(_state_event_projection(record, t3))
    findings.extend(_terminal_checks(t3))
    ledger = {
        "schema_version": "libstruct.evidence_ledger.v1",
        "claims": record["claims"],
    }
    findings.extend(
        _wrapped(
            "product_conservation",
            check_product_conservation.check(t2, t3),
        )
    )
    findings.extend(_wrapped("typed_edges", check_typed_edges.check(t2, t3)))
    findings.extend(_wrapped("strand_pairing", check_strand_pairing.check(t2, t3)))
    findings.extend(
        _wrapped(
            "evidence_support",
            check_unsupported_completion.check(t2, t3, ledger),
        )
    )
    unique = {
        (item["control_id"], item["code"], item["location"], item["message"]): item
        for item in findings
    }
    ordered = [unique[key] for key in sorted(unique)]
    counts = Counter(item["control_id"] for item in ordered)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "findings" if ordered else "pass",
        "finding_count": len(ordered),
        "controls": [
            {
                "control_id": control_id,
                "status": "findings" if counts[control_id] else "pass",
                "finding_count": counts[control_id],
            }
            for control_id in CONTROL_IDS
        ],
        "findings": ordered,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = load_object(args.work_record, "work record")
        drafts = record.get("drafts")
        if not isinstance(drafts, Mapping):
            raise WorkRecordError("work record drafts must be an object")
        t2 = load_object(args.t2, "T2 prediction") if args.t2 else drafts.get("t2")
        t3 = load_object(args.t3, "T3 prediction") if args.t3 else drafts.get("t3")
        if not isinstance(t2, Mapping) or not isinstance(t3, Mapping):
            raise WorkRecordError("T2 and T3 predictions must be JSON objects")
        report = audit(record, t2, t3)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except (OSError, WorkRecordError, KeyError, TypeError, ValueError) as error:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "error",
            "finding_count": 0,
            "controls": [],
            "findings": [],
            "error": str(error),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
