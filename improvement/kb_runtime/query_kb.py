#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "libstruct.libgen_kb_query.v1"
RANKING_VERSION = "libstruct.libgen_kb_ranking.v2"
SELECTION_VERSION = "libstruct.libgen_kb_selection.v1"
RETRIEVAL_VERSION_CAPPED = "libstruct.libgen_kb_retrieval.v2"
RETRIEVAL_VERSION_UNBOUNDED = "libstruct.libgen_kb_retrieval.v3"
RANKING_POLICY = "group_weighted_jaccard_all_donors_v2"
GROUPS = (
    "modalities",
    "operations",
    "barcoding_partitioning",
    "architectures",
    "selection_branching",
    "chemistries",
)
WEIGHTS = {
    "modalities": 2,
    "operations": 3,
    "barcoding_partitioning": 2,
    "architectures": 2,
    "selection_branching": 2,
    "chemistries": 3,
}
BARCODE_MAP = {
    "cell_barcode": "cell_barcode",
    "umi": "molecular_barcode",
    "sample_index": "sample_index",
    "round_barcode": "round_barcode",
    "combinatorial": "combinatorial_indexing",
    "droplet_partitioning": "droplet_partitioning",
    "microwell_partitioning": "microwell_partitioning",
    "plate_partitioning": "plate_partitioning",
    "bead_partitioning": "bead_partitioning",
    "split_pool_partitioning": "split_pool_partitioning",
}
SELECTION_MAP = {
    "affinity_selection": "affinity_selection",
    "size_selection": "size_selection",
    "capture": "capture",
    "sample_split": "sample_split",
    "modality_branching": "modality_branching",
    "alternative_branching": "alternative_branching",
    "discarded_product_branch": "discard_branching",
}
CHEMISTRY_MAP = {
    "reverse_transcription": "reverse_transcription",
    "template_switching": "template_switching",
    "ligation": "ligation",
    "tagmentation": "tagmentation",
    "pcr": "pcr",
    "restriction": "restriction",
    "conversion": "conversion",
}
ALLOWED_VALUES = {
    "modalities": frozenset(
        {
            "gene expression",
            "genomic DNA",
            "feature barcode",
            "sgRNA",
            "chromatin accessibility",
        }
    ),
    "operations": frozenset(
        {
            "reverse_transcription",
            "ligation",
            "tagmentation",
            "extension",
            "pcr",
            "amplification",
            "fragmentation",
            "denaturation",
            "strand_synthesis",
            "cleanup",
            "size_selection",
            "affinity_selection",
            "sample_split",
            "pooling",
            "indexing",
            "circularization",
            "capture",
            "other",
        }
    ),
    "barcoding_partitioning": frozenset(BARCODE_MAP.values()),
    "architectures": frozenset(
        {
            "single_stranded",
            "double_stranded",
            "partially_duplex",
            "rna_dna_hybrid",
            "y_shaped_duplex",
            "mixed_population",
            "unknown",
        }
    ),
    "selection_branching": frozenset(SELECTION_MAP.values()),
    "chemistries": frozenset(CHEMISTRY_MAP.values()),
}


class QueryError(ValueError):
    pass


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QueryError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise QueryError(f"{label} must be a JSON object")
    return value


def validate_query(query: Mapping[str, Any]) -> dict[str, list[str]]:
    if set(query) != {"schema_version", "features", "evidence"}:
        raise QueryError("query fields must be schema_version, features, and evidence")
    if query["schema_version"] != SCHEMA_VERSION:
        raise QueryError("unsupported query schema_version")
    features = query["features"]
    if not isinstance(features, Mapping) or set(features) != set(GROUPS):
        raise QueryError("query features have the wrong groups")
    normalized: dict[str, list[str]] = {}
    requested: set[tuple[str, str]] = set()
    for group in GROUPS:
        values = features[group]
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
            or len(values) != len(set(values))
        ):
            raise QueryError(f"query feature group is invalid: {group}")
        unknown = sorted(set(values) - ALLOWED_VALUES[group])
        if unknown:
            raise QueryError(
                f"query feature group {group} has unsupported values: {unknown}"
            )
        normalized[group] = sorted(values)
        requested.update((group, value) for value in values)
    if not requested:
        raise QueryError("query needs at least one source-derived feature")
    evidence = query["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise QueryError("query evidence must be a nonempty array")
    supported: set[tuple[str, str]] = set()
    for item in evidence:
        if not isinstance(item, Mapping) or set(item) != {
            "feature_group",
            "feature_value",
            "source_locator",
        }:
            raise QueryError("query evidence entry has invalid fields")
        feature = (item["feature_group"], item["feature_value"])
        if feature not in requested:
            raise QueryError("query evidence names a feature not requested")
        locator = item["source_locator"]
        if not isinstance(locator, str) or not locator.strip():
            raise QueryError("query evidence needs a source locator")
        supported.add(feature)
    if supported != requested:
        raise QueryError("every requested feature needs source evidence")
    return normalized


def summary_features(summary: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        "modalities": sorted(
            item["modality"] for item in summary["modality_counts"] if item["count"] > 0
        ),
        "operations": sorted(
            item["operation"]
            for item in summary["operation_counts"]
            if item["count"] > 0
        ),
        "architectures": sorted(
            item["strand_architecture"]
            for item in summary["architecture_counts"]
            if item["count"] > 0
        ),
        "barcoding_partitioning": sorted(
            value
            for key, value in BARCODE_MAP.items()
            if summary["barcoding_partitioning"][key]
        ),
        "selection_branching": sorted(
            value
            for key, value in SELECTION_MAP.items()
            if summary["selection_branching"][key]
        ),
        "chemistries": sorted(
            value
            for key, value in CHEMISTRY_MAP.items()
            if summary["chemistry_flags"][key]
        ),
    }


def similarity(
    query: Mapping[str, list[str]], candidate: Mapping[str, list[str]]
) -> tuple[int, int, dict[str, list[str]]]:
    numerator = 0
    denominator = 0
    matched = {}
    for group in GROUPS:
        left = set(query[group])
        right = set(candidate[group])
        common = left & right
        union = left | right
        numerator += WEIGHTS[group] * len(common)
        denominator += WEIGHTS[group] * len(union)
        matched[group] = sorted(common)
    return numerator, denominator, matched


def rank_donors(
    *, root: Path, query: Mapping[str, Any], protocol_id: str
) -> dict[str, Any]:
    """Return the complete deterministic donor ranking without donor cards."""

    features = validate_query(query)
    catalog = load_object(root / "catalog.json", "KB catalog")
    if catalog.get("schema_version") != "libstruct.libgen_kb_exposure_catalog.v1":
        raise QueryError("KB catalog is not a ground-truth-only exposure catalog")
    expected_catalog_digest = canonical_digest(
        {key: value for key, value in catalog.items() if key != "catalog_digest"}
    )
    if catalog.get("catalog_digest") != expected_catalog_digest:
        raise QueryError("KB exposure catalog digest is stale")
    ranking_index = load_object(root / "ranking_index.json", "KB ranking index")
    if ranking_index.get("catalog_digest") != catalog.get("catalog_digest"):
        raise QueryError("KB ranking index belongs to another catalog")
    expected_index_digest = canonical_digest(
        {key: value for key, value in ranking_index.items() if key != "index_digest"}
    )
    if ranking_index.get("index_digest") != expected_index_digest:
        raise QueryError("KB ranking index digest is stale")
    profiles = {
        item["exemplar_id"]: item["features"]
        for item in ranking_index.get("entries", [])
        if isinstance(item, Mapping)
    }
    catalog_ids = {entry["exemplar_id"] for entry in catalog["entries"]}
    if set(profiles) != catalog_ids:
        raise QueryError("KB ranking index and catalog donor sets disagree")
    priorities = load_object(root / "family_priorities.json", "family priorities")
    priority = priorities.get(protocol_id, [])
    if not isinstance(priority, list):
        raise QueryError("family-priority entry is invalid")
    ranked = []
    for entry in catalog["entries"]:
        exemplar_id = entry["exemplar_id"]
        candidate = profiles[exemplar_id]
        if not isinstance(candidate, Mapping) or set(candidate) != set(GROUPS):
            raise QueryError("KB ranking profile has invalid feature groups")
        normalized_candidate = {}
        for group in GROUPS:
            values = candidate[group]
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))
                or set(values) - ALLOWED_VALUES[group]
            ):
                raise QueryError("KB ranking profile has invalid feature values")
            normalized_candidate[group] = sorted(values)
        candidate = normalized_candidate
        numerator, denominator, matched = similarity(features, candidate)
        score = Fraction(numerator, denominator) if denominator else Fraction(0, 1)
        ranked.append(
            (-score, exemplar_id, numerator, denominator, matched, candidate)
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    candidates = []
    for rank, (
        _,
        exemplar_id,
        numerator,
        denominator,
        matched,
        candidate,
    ) in enumerate(ranked, start=1):
        candidates.append(
            {
                "rank": rank,
                "exemplar_id": exemplar_id,
                "score": {
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": round(numerator / denominator, 12) if denominator else 0.0,
                },
                "matched_features": matched,
                "candidate_features": candidate,
                "family_priority_rank": (
                    priority.index(exemplar_id) + 1
                    if exemplar_id in priority
                    else None
                ),
            }
        )
    payload = {
        "schema_version": RANKING_VERSION,
        "protocol_id": protocol_id,
        "query_digest": canonical_digest(query),
        "catalog_digest": catalog["catalog_digest"],
        "ranking_policy": RANKING_POLICY,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    payload["ranking_digest"] = canonical_digest(payload)
    return payload


def validate_selection(
    selection: Mapping[str, Any],
    *,
    ranking: Mapping[str, Any],
    max_results: int | None = None,
) -> list[str]:
    expected_fields = {
        "schema_version",
        "ranking_digest",
        "selected_exemplar_ids",
        "rationale",
    }
    if set(selection) != expected_fields:
        raise QueryError("selection fields are invalid")
    if selection["schema_version"] != SELECTION_VERSION:
        raise QueryError("unsupported selection schema_version")
    if selection["ranking_digest"] != ranking.get("ranking_digest"):
        raise QueryError("selection does not bind to the deterministic ranking")
    selected = selection["selected_exemplar_ids"]
    candidate_count = len(ranking.get("candidates", []))
    effective_limit = candidate_count if max_results is None else max_results
    if effective_limit < 1:
        raise QueryError("max_results must be positive when supplied")
    if (
        not isinstance(selected, list)
        or len(selected) > effective_limit
        or any(not isinstance(item, str) or not item for item in selected)
        or len(selected) != len(set(selected))
    ):
        qualifier = (
            "the complete ranked catalog"
            if max_results is None
            else f"{effective_limit} donors"
        )
        raise QueryError(
            f"selection must contain unique donors drawn from at most {qualifier}"
        )
    available = {item["exemplar_id"] for item in ranking["candidates"]}
    unknown = sorted(set(selected) - available)
    if unknown:
        raise QueryError(f"selection names donors outside the ranking: {unknown}")
    rationale = selection["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise QueryError("selection needs a nonempty rationale")
    return selected


def fetch_selected(
    *,
    root: Path,
    query: Mapping[str, Any],
    ranking: Mapping[str, Any],
    selection: Mapping[str, Any],
    protocol_id: str,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Fetch only the agent-selected cards after recomputing the full ranking."""

    expected_ranking = rank_donors(
        root=root,
        query=query,
        protocol_id=protocol_id,
    )
    if dict(ranking) != expected_ranking:
        raise QueryError("ranking is stale, modified, or belongs to another query")
    selected = validate_selection(
        selection,
        ranking=expected_ranking,
        max_results=max_results,
    )
    catalog = load_object(root / "catalog.json", "KB catalog")
    entries = {item["exemplar_id"]: item for item in catalog["entries"]}
    ranked = {
        item["exemplar_id"]: item for item in expected_ranking["candidates"]
    }
    matches = []
    for selection_rank, exemplar_id in enumerate(selected, start=1):
        entry = entries[exemplar_id]
        rank_record = ranked[exemplar_id]
        matches.append(
            {
                "selection_rank": selection_rank,
                "similarity_rank": rank_record["rank"],
                "exemplar_id": exemplar_id,
                "score": rank_record["score"],
                "matched_features": rank_record["matched_features"],
                "candidate_features": rank_record["candidate_features"],
                "family_priority_rank": rank_record["family_priority_rank"],
                "oligo_card": load_object(
                    root / entry["oligo_card"]["path"], "oligo card"
                ),
                "graph_card": load_object(
                    root / entry["graph_card"]["path"], "graph card"
                ),
            }
        )
    payload = {
        "schema_version": (
            RETRIEVAL_VERSION_UNBOUNDED
            if max_results is None
            else RETRIEVAL_VERSION_CAPPED
        ),
        "protocol_id": protocol_id,
        "query_digest": expected_ranking["query_digest"],
        "catalog_digest": expected_ranking["catalog_digest"],
        "ranking_policy": expected_ranking["ranking_policy"],
        "ranking_digest": expected_ranking["ranking_digest"],
        "selection_digest": canonical_digest(selection),
        "candidate_count": expected_ranking["candidate_count"],
        "selection_count": len(matches),
        "max_results": max_results,
        "matches": matches,
    }
    payload["retrieval_digest"] = canonical_digest(payload)
    return payload


def resolve_protocol_id(
    requested: str | None,
    manifest_path: Path = Path("/workspace/input_manifest.json"),
) -> str:
    """Bind retrieval to the immutable target task rather than caller choice."""

    manifest = load_object(manifest_path, "target input manifest")
    target = manifest.get("protocol_id")
    if not isinstance(target, str) or not target:
        raise QueryError("target input manifest lacks protocol_id")
    if requested and requested != target:
        raise QueryError("requested protocol_id differs from the target task")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    rank_parser = subparsers.add_parser("rank")
    rank_parser.add_argument("--query", required=True)
    rank_parser.add_argument("--output", required=True)
    rank_parser.add_argument("--root", default="/workspace/knowledge_base")
    rank_parser.add_argument(
        "--protocol-id", default=os.environ.get("LIBGEN_PROTOCOL_ID")
    )
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--query", required=True)
    fetch_parser.add_argument("--ranking", required=True)
    fetch_parser.add_argument("--selection", required=True)
    fetch_parser.add_argument("--output", required=True)
    fetch_parser.add_argument("--root", default="/workspace/knowledge_base")
    fetch_parser.add_argument(
        "--protocol-id", default=os.environ.get("LIBGEN_PROTOCOL_ID")
    )
    # Omit --max-results for the current policy: the agent may fetch any unique
    # subset of the complete deterministic ranking. The optional argument is
    # retained solely so old, hash-pinned capped plans remain reproducible.
    fetch_parser.add_argument("--max-results", type=int)
    args = parser.parse_args()
    try:
        protocol_id = resolve_protocol_id(args.protocol_id)
        root = Path(args.root)
        query = load_object(Path(args.query), "KB query")
        if args.command == "rank":
            result = rank_donors(
                root=root,
                query=query,
                protocol_id=protocol_id,
            )
        else:
            result = fetch_selected(
                root=root,
                query=query,
                ranking=load_object(Path(args.ranking), "KB ranking"),
                selection=load_object(Path(args.selection), "KB selection"),
                protocol_id=protocol_id,
                max_results=args.max_results,
            )
    except QueryError as error:
        parser.error(str(error))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
