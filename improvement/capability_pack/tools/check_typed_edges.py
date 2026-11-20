#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Mapping

from _common import finding, object_list, run_checker, string_list, workflows


def check(t2: Mapping[str, Any], t3: Mapping[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    oligos = object_list(t2.get("oligos"), "T2 oligos")
    oligo_ids = {item.get("oligo_id") for item in oligos if isinstance(item.get("oligo_id"), str)}
    for wi, workflow in workflows(t3):
        base = f"/workflows/{wi}"
        states = object_list(workflow.get("states"), f"workflow {wi} states")
        state_ids = {item.get("state_id") for item in states if isinstance(item.get("state_id"), str)}
        transition_ids: set[str] = set()
        graph: dict[str, set[str]] = defaultdict(set)
        for ti, transition in enumerate(object_list(workflow.get("transitions"), f"workflow {wi} transitions")):
            path = f"{base}/transitions/{ti}"
            transition_id = transition.get("transition_id")
            if not isinstance(transition_id, str) or transition_id in transition_ids:
                findings.append(finding("transition_id_invalid", f"{path}/transition_id", "transition IDs must be unique strings"))
            else:
                transition_ids.add(transition_id)
            operation = transition.get("operation")
            if not isinstance(operation, str) or not operation:
                findings.append(finding("operation_missing", f"{path}/operation", "typed transitions require an operation"))
            inputs = string_list(transition.get("substrate_state_ids"), f"{path} substrate_state_ids")
            products = string_list(transition.get("product_state_ids"), f"{path} product_state_ids")
            unknown = (set(inputs) | set(products)) - state_ids
            if unknown:
                findings.append(finding("unknown_edge_state", path, "typed edge references unknown states: " + ", ".join(sorted(unknown))))
            unknown_oligos = set(string_list(transition.get("oligo_ids"), f"{path} oligo_ids")) - oligo_ids
            if unknown_oligos:
                findings.append(finding("unknown_oligo", f"{path}/oligo_ids", "unknown T2 oligos: " + ", ".join(sorted(unknown_oligos))))
            for substrate in inputs:
                graph[substrate].update(products)
        initial = set(string_list(workflow.get("initial_state_ids"), f"workflow {wi} initial_state_ids"))
        finals = {
            item.get("state_id")
            for item in object_list(workflow.get("final_outputs"), f"workflow {wi} final_outputs")
            if isinstance(item.get("state_id"), str)
        }
        reachable = set(initial)
        queue = deque(initial)
        while queue:
            node = queue.popleft()
            for neighbor in graph.get(node, set()):
                if neighbor not in reachable:
                    reachable.add(neighbor)
                    queue.append(neighbor)
        if finals - reachable:
            findings.append(finding("unreachable_final", f"{base}/final_outputs", "terminal states are unreachable: " + ", ".join(sorted(finals - reachable))))
    return findings


if __name__ == "__main__":
    raise SystemExit(run_checker("check_typed_edges", None, check))
