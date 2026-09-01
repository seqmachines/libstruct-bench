#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from _common import finding, object_list, run_checker, string_list, workflows


def check(t2: Mapping[str, Any], t3: Mapping[str, Any]) -> list[dict[str, str]]:
    del t2
    findings: list[dict[str, str]] = []
    for wi, workflow in workflows(t3):
        base = f"/workflows/{wi}"
        states = object_list(workflow.get("states"), f"workflow {wi} states")
        state_ids = [state.get("state_id") for state in states]
        known = {item for item in state_ids if isinstance(item, str)}
        if len(known) != len(state_ids):
            findings.append(finding("state_id_invalid", f"{base}/states", "states require unique string IDs"))
        initial = set(string_list(workflow.get("initial_state_ids"), f"workflow {wi} initial_state_ids"))
        final = {
            item.get("state_id")
            for item in object_list(workflow.get("final_outputs"), f"workflow {wi} final_outputs")
            if isinstance(item.get("state_id"), str)
        }
        carried: set[str] = set()
        discarded: set[str] = set()
        substrates: set[str] = set()
        for ti, transition in enumerate(object_list(workflow.get("transitions"), f"workflow {wi} transitions")):
            path = f"{base}/transitions/{ti}"
            inputs = set(string_list(transition.get("substrate_state_ids"), f"{path} substrate_state_ids"))
            products = set(string_list(transition.get("product_state_ids"), f"{path} product_state_ids"))
            keep = set(string_list(transition.get("carried_forward_product_ids"), f"{path} carried_forward_product_ids"))
            drop = set(string_list(transition.get("discarded_product_ids"), f"{path} discarded_product_ids"))
            missing = (inputs | products) - known
            if missing:
                findings.append(finding("unknown_state", path, "unknown state references: " + ", ".join(sorted(missing))))
            if keep & drop:
                findings.append(finding("dual_product_classification", path, "products cannot be both carried and discarded: " + ", ".join(sorted(keep & drop))))
            if keep | drop != products:
                findings.append(finding("incomplete_product_classification", path, "carried and discarded products must partition product_state_ids"))
            carried.update(keep)
            discarded.update(drop)
            substrates.update(inputs)
        reused = discarded & substrates
        if reused:
            findings.append(finding("discarded_product_reused", f"{base}/transitions", "discarded products are later substrates: " + ", ".join(sorted(reused))))
        unproduced = substrates - initial - carried
        if unproduced:
            findings.append(finding("unproduced_substrate", f"{base}/transitions", "noninitial substrates were not carried forward: " + ", ".join(sorted(unproduced))))
        unused = carried - substrates - final
        if unused:
            findings.append(finding("unused_carried_product", f"{base}/transitions", "carried products are neither consumed nor terminal: " + ", ".join(sorted(unused))))
    return findings


if __name__ == "__main__":
    raise SystemExit(run_checker("check_product_conservation", None, check))
