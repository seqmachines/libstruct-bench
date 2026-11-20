#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from _common import (
    explicit_side_sequence,
    finding,
    object_list,
    reverse_complement,
    run_checker,
    workflows,
)


def check(t2: Mapping[str, Any], t3: Mapping[str, Any]) -> list[dict[str, str]]:
    del t2
    findings: list[dict[str, str]] = []
    for wi, workflow in workflows(t3):
        for si, state in enumerate(object_list(workflow.get("states"), f"workflow {wi} states")):
            path = f"/workflows/{wi}/states/{si}"
            strands_list = object_list(state.get("strands"), f"{path} strands")
            strands = {
                item.get("strand_id"): item
                for item in strands_list
                if isinstance(item.get("strand_id"), str)
            }
            if state.get("reference_strand_id") not in strands:
                findings.append(finding("unknown_reference_strand", f"{path}/reference_strand_id", "reference_strand_id must resolve"))
            segments: dict[str, tuple[str, int]] = {}
            for strand_id, strand in strands.items():
                for position, segment in enumerate(object_list(strand.get("segments"), f"strand {strand_id} segments")):
                    segment_id = segment.get("segment_id")
                    if not isinstance(segment_id, str) or segment_id in segments:
                        findings.append(finding("segment_id_invalid", f"{path}/strands", "segment IDs must be unique strings within a state"))
                    else:
                        segments[segment_id] = (strand_id, position)
            paired = object_list(state.get("paired_regions"), f"{path} paired_regions")
            paired_segments: set[str] = set()
            for pi, region in enumerate(paired):
                region_path = f"{path}/paired_regions/{pi}"
                for side_name in ("side_1", "side_2"):
                    side = region.get(side_name)
                    if not isinstance(side, Mapping):
                        findings.append(finding("paired_side_invalid", f"{region_path}/{side_name}", "paired-region side must be an object"))
                        continue
                    strand_id = side.get("strand_id")
                    ids = side.get("segment_ids")
                    if strand_id not in strands or not isinstance(ids, list):
                        findings.append(finding("paired_side_reference", f"{region_path}/{side_name}", "paired side must reference a known strand and segments"))
                        continue
                    positions: list[int] = []
                    for segment_id in ids:
                        location = segments.get(segment_id)
                        if location is None or location[0] != strand_id:
                            findings.append(finding("paired_segment_reference", f"{region_path}/{side_name}", f"unknown segment {segment_id!r}"))
                            continue
                        positions.append(location[1])
                        if segment_id in paired_segments and state.get("strand_architecture") != "mixed_population":
                            findings.append(finding("segment_paired_twice", region_path, f"segment {segment_id!r} is paired more than once"))
                        paired_segments.add(segment_id)
                    if positions and positions != list(range(positions[0], positions[0] + len(positions))):
                        findings.append(finding("noncontiguous_pairing", f"{region_path}/{side_name}", "paired segments must be contiguous and ordered"))
                if region.get("relationship") == "reverse_complementary":
                    left = explicit_side_sequence(region.get("side_1", {}), strands)
                    right = explicit_side_sequence(region.get("side_2", {}), strands)
                    if left is not None and right is not None and reverse_complement(left) != right:
                        findings.append(finding("pairing_not_reverse_complementary", region_path, "explicit paired sequences are not reverse complements"))
            architecture = state.get("strand_architecture")
            if architecture == "single_stranded" and (len(strands) != 1 or paired):
                findings.append(finding("single_strand_architecture", path, "single_stranded requires one unpaired strand"))
            if architecture in {"double_stranded", "rna_dna_hybrid", "y_shaped_duplex"} and (len(strands) != 2 or not paired):
                findings.append(finding("duplex_architecture", path, f"{architecture} requires two strands and a paired region"))
            if architecture == "double_stranded" and paired_segments != set(segments):
                findings.append(finding("incomplete_duplex_pairing", path, "double_stranded cannot contain unpaired segments"))
            for di, discontinuity in enumerate(object_list(state.get("discontinuities"), f"{path} discontinuities")):
                after = segments.get(discontinuity.get("after_segment_id"))
                before = segments.get(discontinuity.get("before_segment_id"))
                strand_id = discontinuity.get("strand_id")
                if after is None or before is None or after[0] != strand_id or before[0] != strand_id or before[1] != after[1] + 1:
                    findings.append(finding("invalid_discontinuity", f"{path}/discontinuities/{di}", "discontinuity must lie between adjacent segments on its strand"))
    return findings


if __name__ == "__main__":
    raise SystemExit(run_checker("check_strand_pairing", None, check))
