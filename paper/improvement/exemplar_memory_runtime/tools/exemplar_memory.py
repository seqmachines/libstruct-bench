#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter, defaultdict
from functools import cmp_to_key
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

QUERY_SCHEMA_VERSION = "libstruct.libgen_exemplar_query.v1"
RETRIEVAL_SCHEMA_VERSION = "libstruct.libgen_exemplar_retrieval.v1"
USAGE_SCHEMA_VERSION = "libstruct.libgen_exemplar_usage.v1"
CATALOG_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory.v1"
MEMORY_MANIFEST_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory_manifest.v1"
SUMMARY_SCHEMA_VERSION = "libstruct.libgen_exemplar_mechanism_summary.v1"
T2_EXAMPLE_SCHEMA_VERSION = "libstruct.libgen_t2_example.v1"
T3_EXAMPLE_SCHEMA_VERSION = "libstruct.libgen_t3_example.v1"
RANKING_POLICY = "group_weighted_jaccard_then_exemplar_id_v1"

FEATURE_GROUPS = (
    "modalities",
    "operations",
    "barcoding_partitioning",
    "architectures",
    "selection_branching",
    "chemistries",
)
FEATURE_GROUP_WEIGHTS = {
    "modalities": 2,
    "operations": 3,
    "barcoding_partitioning": 2,
    "architectures": 2,
    "selection_branching": 2,
    "chemistries": 3,
}
MODALITIES = {
    "gene expression",
    "genomic DNA",
    "feature barcode",
    "sgRNA",
    "chromatin accessibility",
}
OPERATIONS = {
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
BARCODING_PARTITIONING = {
    "cell_barcode",
    "molecular_barcode",
    "sample_index",
    "round_barcode",
    "droplet_partitioning",
    "microwell_partitioning",
    "plate_partitioning",
    "bead_partitioning",
    "split_pool_partitioning",
    "combinatorial_indexing",
}
ARCHITECTURES = {
    "single_stranded",
    "double_stranded",
    "partially_duplex",
    "rna_dna_hybrid",
    "y_shaped_duplex",
    "mixed_population",
    "unknown",
}
SELECTION_BRANCHING = {
    "affinity_selection",
    "size_selection",
    "capture",
    "sample_split",
    "modality_branching",
    "alternative_branching",
    "discard_branching",
}
CHEMISTRIES = {
    "reverse_transcription",
    "template_switching",
    "ligation",
    "tagmentation",
    "pcr",
    "restriction",
    "conversion",
}
ALLOWED_FEATURES = {
    "modalities": MODALITIES,
    "operations": OPERATIONS,
    "barcoding_partitioning": BARCODING_PARTITIONING,
    "architectures": ARCHITECTURES,
    "selection_branching": SELECTION_BRANCHING,
    "chemistries": CHEMISTRIES,
}
OPERATION_CHEMISTRIES = {
    "reverse_transcription": "reverse_transcription",
    "ligation": "ligation",
    "tagmentation": "tagmentation",
    "pcr": "pcr",
}
OPERATION_SELECTION = {
    "affinity_selection": "affinity_selection",
    "size_selection": "size_selection",
    "capture": "capture",
    "sample_split": "sample_split",
}
SUMMARY_BARCODING_MAP = {
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
SUMMARY_SELECTION_MAP = {
    "affinity_selection": "affinity_selection",
    "size_selection": "size_selection",
    "capture": "capture",
    "discarded_product_branch": "discard_branching",
    "sample_split": "sample_split",
    "modality_branching": "modality_branching",
    "alternative_branching": "alternative_branching",
}
SUMMARY_CHEMISTRY_MAP = {
    "reverse_transcription": "reverse_transcription",
    "template_switching": "template_switching",
    "ligation": "ligation",
    "tagmentation": "tagmentation",
    "pcr": "pcr",
    "restriction": "restriction",
    "conversion": "conversion",
}
EXEMPLAR_ID_RE = re.compile(r"^exm-[a-f0-9]{32}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FORBIDDEN_MEMORY_KEY_FRAGMENTS = {
    "groundtruth",
    "ground_truth",
    "verifier",
    "reward",
    "score",
    "error",
    "prediction",
    "protocol_id",
    "protocol_name",
    "source_locator",
    "evidence",
    "provenance",
    "review",
    "decision",
}
FORBIDDEN_TARGET_INPUT_KEY_FRAGMENTS = {
    "groundtruth",
    "ground_truth",
    "verifier",
    "reward",
    "score",
    "error",
    "prediction",
}


class ExemplarMemoryError(ValueError):
    """Raised when exemplar retrieval cannot be performed deterministically."""


class TargetWorkRecordError(ValueError):
    """Raised when target evidence cannot be resolved from the work record."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def with_digest(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    if field in result:
        raise ExemplarMemoryError(f"payload already contains {field}")
    result[field] = canonical_digest(result)
    return result


def validate_digest(document: Mapping[str, Any], field: str, label: str) -> None:
    actual = document.get(field)
    expected = canonical_digest(
        {key: value for key, value in document.items() if key != field}
    )
    if actual != expected:
        raise ExemplarMemoryError(
            f"{label} has stale {field}: expected {expected}, got {actual}"
        )


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExemplarMemoryError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExemplarMemoryError(f"{label} must be a JSON object")
    return value


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    destination = path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass
        raise


def validate_target_work_record(record: Mapping[str, Any]) -> None:
    """Validate only the evidence fields consumed by the portable memory runtime.

    The procedural capability audit remains the authority for the complete work
    record.  This deliberately narrow validator prevents the retrieval runtime
    from accepting separate prediction, verifier, score, or ground-truth inputs.
    """

    _reject_forbidden_target_input_keys(record)
    required = {
        "schema_version",
        "protocol_id",
        "source_coverage",
        "inventory",
        "claims",
        "state_signatures",
        "event_records",
        "drafts",
    }
    if set(record) != required:
        raise TargetWorkRecordError(
            "work record fields differ; "
            f"missing={sorted(required - set(record))}, "
            f"extra={sorted(set(record) - required)}"
        )
    if record.get("schema_version") != "libstruct.libgen_capability_work_record.v1":
        raise TargetWorkRecordError("unsupported target work-record schema_version")
    if not isinstance(record.get("protocol_id"), str) or not record["protocol_id"]:
        raise TargetWorkRecordError("target work record has an invalid protocol_id")
    coverage = record.get("source_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise TargetWorkRecordError("target work record source_coverage is empty")
    source_ids: set[str] = set()
    source_locator_strings: set[str] = set()
    usable_source_ids: set[str] = set()
    usable_source_locator_strings: set[str] = set()
    for index, source in enumerate(coverage):
        if not isinstance(source, Mapping):
            raise TargetWorkRecordError(f"source_coverage/{index} must be an object")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise TargetWorkRecordError(f"source_coverage/{index} has invalid source_id")
        source_ids.add(source_id)
        artifact = source.get("artifact")
        locators = source.get("locators")
        coverage_status = source.get("coverage_status")
        if coverage_status not in {
            "inspected",
            "partially_inspected",
            "unavailable",
        }:
            raise TargetWorkRecordError(
                f"source_coverage/{index} has invalid coverage_status"
            )
        if not isinstance(artifact, str) or not artifact:
            raise TargetWorkRecordError(
                f"source_coverage/{index} has invalid artifact"
            )
        if not isinstance(locators, list) or any(
            not isinstance(locator, str) or not locator for locator in locators
        ):
            raise TargetWorkRecordError(
                f"source_coverage/{index} has invalid locators"
            )
        source_locator_strings.add(artifact)
        source_locator_strings.update(locators)
        if coverage_status in {"inspected", "partially_inspected"}:
            usable_source_ids.add(source_id)
            usable_source_locator_strings.add(artifact)
            usable_source_locator_strings.update(locators)
    drafts = record.get("drafts")
    if not isinstance(drafts, Mapping) or set(drafts) != {"t2", "t3"}:
        raise TargetWorkRecordError("target work record drafts must contain t2 and t3")
    for target in ("t2", "t3"):
        document = drafts[target]
        if not isinstance(document, Mapping) or document.get("protocol_id") != record["protocol_id"]:
            raise TargetWorkRecordError(f"target work record {target} draft is invalid")
    seen_claim_ids: set[str] = set()
    claims = record.get("claims")
    if not isinstance(claims, list):
        raise TargetWorkRecordError("target work record claims must be an array")
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise TargetWorkRecordError(f"claims/{index} must be an object")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in seen_claim_ids:
            raise TargetWorkRecordError(f"claims/{index} has invalid claim_id")
        seen_claim_ids.add(claim_id)
        target = claim.get("target")
        pointer = claim.get("json_pointer")
        if target not in {"t2", "t3"} or not isinstance(pointer, str):
            raise TargetWorkRecordError(f"claims/{index} target pointer is invalid")
        resolve_json_pointer(drafts[target], pointer)
        _validate_support_record(claim, f"claims/{index}", source_ids=None)
        known_locators = (
            usable_source_locator_strings
            if claim["support"] in {"explicit", "derivable"}
            else source_locator_strings
        )
        for locator in claim["source_locators"]:
            if not any(
                locator == known
                or locator.startswith(f"{known} ")
                or locator.startswith(f"{known}:")
                for known in known_locators
            ):
                raise TargetWorkRecordError(
                    f"claims/{index} locator is not linked to source_coverage"
                )
    seen_inventory_ids: set[str] = set()
    inventory = record.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        raise TargetWorkRecordError("target work record inventory is empty")
    for index, item in enumerate(inventory):
        if not isinstance(item, Mapping):
            raise TargetWorkRecordError(f"inventory/{index} must be an object")
        inventory_id = item.get("inventory_id")
        if (
            not isinstance(inventory_id, str)
            or not inventory_id
            or inventory_id in seen_inventory_ids
        ):
            raise TargetWorkRecordError(f"inventory/{index} has invalid inventory_id")
        seen_inventory_ids.add(inventory_id)
        _validate_support_record(item, f"inventory/{index}", source_ids=source_ids)
        if item["support"] in {"explicit", "derivable"} and not any(
            locator["source_id"] in usable_source_ids
            for locator in item["source_locators"]
        ):
            raise TargetWorkRecordError(
                f"inventory/{index} has no inspected source locator"
            )
        pointers = item.get("target_pointers")
        if not isinstance(pointers, list):
            raise TargetWorkRecordError(f"inventory/{index}/target_pointers is invalid")
        for pointer_index, pointer in enumerate(pointers):
            if not isinstance(pointer, Mapping):
                raise TargetWorkRecordError(
                    f"inventory/{index}/target_pointers/{pointer_index} is invalid"
                )
            target = pointer.get("target")
            json_pointer = pointer.get("json_pointer")
            if target not in {"t2", "t3"} or not isinstance(json_pointer, str):
                raise TargetWorkRecordError(
                    f"inventory/{index}/target_pointers/{pointer_index} is invalid"
                )
            resolve_json_pointer(drafts[target], json_pointer)
    for field in ("state_signatures", "event_records"):
        if not isinstance(record.get(field), list):
            raise TargetWorkRecordError(f"target work record {field} must be an array")


def _validate_support_record(
    record: Mapping[str, Any],
    location: str,
    *,
    source_ids: set[str] | None,
) -> None:
    support = record.get("support")
    if support not in {"explicit", "derivable", "ambiguous", "unsupported"}:
        raise TargetWorkRecordError(f"{location} has invalid support")
    if support == "derivable" and (
        not isinstance(record.get("derivation"), str)
        or not record["derivation"].strip()
    ):
        raise TargetWorkRecordError(f"{location} lacks a mechanical derivation")
    locators = record.get("source_locators")
    if not isinstance(locators, list) or not locators:
        raise TargetWorkRecordError(f"{location} has no source locators")
    if source_ids is None:
        if any(not isinstance(locator, str) or not locator for locator in locators):
            raise TargetWorkRecordError(f"{location} source locators are invalid")
        return
    for locator in locators:
        if (
            not isinstance(locator, Mapping)
            or locator.get("source_id") not in source_ids
            or not isinstance(locator.get("locator"), str)
            or not locator["locator"]
        ):
            raise TargetWorkRecordError(f"{location} source locators are invalid")


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise TargetWorkRecordError(f"invalid JSON pointer {pointer!r}")
    value = document
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            if token not in value:
                raise TargetWorkRecordError(f"JSON pointer does not resolve: {pointer}")
            value = value[token]
        elif isinstance(value, list):
            try:
                index = int(token)
            except ValueError as error:
                raise TargetWorkRecordError(
                    f"JSON pointer does not resolve: {pointer}"
                ) from error
            if index < 0 or index >= len(value):
                raise TargetWorkRecordError(f"JSON pointer does not resolve: {pointer}")
            value = value[index]
        else:
            raise TargetWorkRecordError(f"JSON pointer does not resolve: {pointer}")
    return value


def validate_query(
    query: Mapping[str, Any],
    work_record: Mapping[str, Any],
) -> dict[str, list[str]]:
    expected = {"schema_version", *FEATURE_GROUPS, "feature_evidence"}
    if set(query) != expected:
        raise ExemplarMemoryError(
            "query fields differ; "
            f"missing={sorted(expected - set(query))}, "
            f"extra={sorted(set(query) - expected)}"
        )
    if query.get("schema_version") != QUERY_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-query schema_version")
    normalized: dict[str, list[str]] = {}
    requested: set[tuple[str, str]] = set()
    for group in FEATURE_GROUPS:
        values = query.get(group)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ExemplarMemoryError(f"query {group} must be an array of strings")
        if len(values) != len(set(values)):
            raise ExemplarMemoryError(f"query {group} contains duplicate features")
        unknown = sorted(set(values) - ALLOWED_FEATURES[group])
        if unknown:
            raise ExemplarMemoryError(
                f"query {group} contains unsupported features: {', '.join(unknown)}"
            )
        normalized[group] = sorted(values)
        requested.update((group, value) for value in values)
    if not requested:
        raise ExemplarMemoryError("query must contain at least one source-derived feature")

    try:
        validate_target_work_record(work_record)
    except TargetWorkRecordError as error:
        raise ExemplarMemoryError(f"invalid target work record: {error}") from error
    claims = {item["claim_id"]: item for item in work_record["claims"]}
    inventory = {
        item["inventory_id"]: item for item in work_record["inventory"]
    }
    documents = work_record["drafts"]
    links = query.get("feature_evidence")
    if not isinstance(links, list) or not links:
        raise ExemplarMemoryError("query feature_evidence must be a non-empty array")
    linked_features: set[tuple[str, str]] = set()
    for index, link in enumerate(links):
        location = f"feature_evidence/{index}"
        if not isinstance(link, Mapping) or set(link) != {
            "feature_group",
            "feature_value",
            "evidence_refs",
        }:
            raise ExemplarMemoryError(f"{location} has invalid fields")
        group = link.get("feature_group")
        value = link.get("feature_value")
        feature = (group, value)
        if feature not in requested:
            raise ExemplarMemoryError(
                f"{location} references a feature absent from the query"
            )
        if feature in linked_features:
            raise ExemplarMemoryError(f"{location} duplicates feature evidence")
        refs = link.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ExemplarMemoryError(f"{location}/evidence_refs must not be empty")
        seen_refs: set[tuple[str, str]] = set()
        feature_supported = False
        for ref_index, ref in enumerate(refs):
            ref_location = f"{location}/evidence_refs/{ref_index}"
            if not isinstance(ref, Mapping) or set(ref) != {"record_kind", "record_id"}:
                raise ExemplarMemoryError(f"{ref_location} has invalid fields")
            key = (ref.get("record_kind"), ref.get("record_id"))
            if key in seen_refs:
                raise ExemplarMemoryError(f"{ref_location} is duplicated")
            seen_refs.add(key)
            evidence = _resolve_feature_evidence(
                key,
                claims=claims,
                inventory=inventory,
                documents=documents,
            )
            if feature in evidence:
                feature_supported = True
        if not feature_supported:
            raise ExemplarMemoryError(
                f"{location} feature is not mechanically derived from its "
                "explicit or derivable target evidence"
            )
        linked_features.add(feature)
    if linked_features != requested:
        missing = sorted(requested - linked_features)
        raise ExemplarMemoryError(
            "every query feature needs exact target evidence; missing="
            + ", ".join(f"{group}:{value}" for group, value in missing)
        )
    return normalized


def _resolve_feature_evidence(
    key: tuple[Any, Any],
    *,
    claims: Mapping[str, Mapping[str, Any]],
    inventory: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Any],
) -> set[tuple[str, str]]:
    kind, record_id = key
    if not isinstance(record_id, str) or not record_id:
        raise ExemplarMemoryError("feature evidence record_id must be non-empty")
    if kind == "claim":
        record = claims.get(record_id)
        if record is None:
            raise ExemplarMemoryError(f"unknown target claim {record_id!r}")
        _require_supported_record(record, f"claim {record_id!r}")
        try:
            value = resolve_json_pointer(
                documents[record["target"]], record["json_pointer"]
            )
        except (KeyError, TargetWorkRecordError) as error:
            raise ExemplarMemoryError(
                f"target claim {record_id!r} does not resolve"
            ) from error
        return derive_feature_pairs(value)
    if kind == "inventory":
        record = inventory.get(record_id)
        if record is None:
            raise ExemplarMemoryError(f"unknown target inventory item {record_id!r}")
        _require_supported_record(record, f"inventory item {record_id!r}")
        values: list[Any] = [record.get("name")]
        for pointer in record["target_pointers"]:
            try:
                values.append(
                    resolve_json_pointer(
                        documents[pointer["target"]], pointer["json_pointer"]
                    )
                )
            except (KeyError, TargetWorkRecordError) as error:
                raise ExemplarMemoryError(
                    f"inventory item {record_id!r} has an unresolved target pointer"
                ) from error
        return derive_feature_pairs(values)
    raise ExemplarMemoryError(f"unsupported feature evidence kind {kind!r}")


def _require_supported_record(record: Mapping[str, Any], label: str) -> None:
    support = record.get("support")
    if support not in {"explicit", "derivable"}:
        raise ExemplarMemoryError(
            f"{label} is {support!r}; query features require explicit or derivable support"
        )
    locators = record.get("source_locators")
    if not isinstance(locators, list) or not locators:
        raise ExemplarMemoryError(f"{label} has no target source locator")
    if support == "derivable" and (
        not isinstance(record.get("derivation"), str)
        or not record["derivation"].strip()
    ):
        raise ExemplarMemoryError(f"{label} lacks a mechanical derivation")


def derive_feature_pairs(value: Any) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    _derive_feature_pairs(value, result)
    return result


def _derive_feature_pairs(value: Any, result: set[tuple[str, str]]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _derive_feature_pairs(key, result)
            _derive_feature_pairs(child, result)
        _derive_graph_features(value, result)
        return
    if isinstance(value, list):
        for child in value:
            _derive_feature_pairs(child, result)
        return
    if not isinstance(value, str):
        return
    if value in MODALITIES:
        result.add(("modalities", value))
    if value in OPERATIONS:
        result.add(("operations", value))
        chemistry = OPERATION_CHEMISTRIES.get(value)
        if chemistry:
            result.add(("chemistries", chemistry))
        selection = OPERATION_SELECTION.get(value)
        if selection:
            result.add(("selection_branching", selection))
    if value in ARCHITECTURES:
        result.add(("architectures", value))
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    phrase_rules = {
        ("barcoding_partitioning", "cell_barcode"): (
            "cell_barcode",
            "cellular_barcode",
        ),
        ("barcoding_partitioning", "molecular_barcode"): (
            "molecular_barcode",
            "unique_molecular_identifier",
            "umi",
        ),
        ("barcoding_partitioning", "sample_index"): (
            "sample_index",
            "sample_barcode",
        ),
        ("barcoding_partitioning", "round_barcode"): ("round_barcode",),
        ("barcoding_partitioning", "droplet_partitioning"): ("droplet",),
        ("barcoding_partitioning", "microwell_partitioning"): ("microwell",),
        ("barcoding_partitioning", "plate_partitioning"): ("plate_partition",),
        ("barcoding_partitioning", "bead_partitioning"): ("bead_partition",),
        ("barcoding_partitioning", "split_pool_partitioning"): (
            "split_pool",
            "split_and_pool",
        ),
        ("barcoding_partitioning", "combinatorial_indexing"): (
            "combinatorial_index",
            "combinatorial_barcode",
        ),
        ("chemistries", "template_switching"): (
            "template_switch",
            "template_switching",
        ),
        ("chemistries", "restriction"): (
            "restriction_digest",
            "restriction_enzyme",
        ),
        ("chemistries", "conversion"): (
            "conversion",
            "bisulfite",
        ),
    }
    for feature, needles in phrase_rules.items():
        if any(needle in normalized for needle in needles):
            result.add(feature)


def _derive_graph_features(
    value: Mapping[str, Any],
    result: set[tuple[str, str]],
) -> None:
    operation = value.get("operation")
    if operation in OPERATION_SELECTION:
        result.add(("selection_branching", OPERATION_SELECTION[operation]))
    carried = value.get("carried_forward_product_ids")
    discarded = value.get("discarded_product_ids")
    products = value.get("product_state_ids")
    if isinstance(discarded, list) and discarded:
        result.add(("selection_branching", "discard_branching"))
    if isinstance(carried, list) and len(carried) > 1:
        result.add(("selection_branching", "alternative_branching"))
    if isinstance(products, list) and len(products) > 1 and not discarded:
        result.add(("selection_branching", "alternative_branching"))
    final_outputs = value.get("final_outputs")
    if isinstance(final_outputs, list):
        modalities = {
            item.get("modality")
            for item in final_outputs
            if isinstance(item, Mapping) and isinstance(item.get("modality"), str)
        }
        if len(modalities) > 1:
            result.add(("selection_branching", "modality_branching"))


def load_catalog(
    catalog_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    path = catalog_path.expanduser()
    if path.parent.is_symlink():
        raise ExemplarMemoryError("exemplar memory root may not be a symlink")
    if path.is_symlink() or not path.is_file():
        raise ExemplarMemoryError(f"exemplar catalog is not a regular file: {path}")
    if path.name != "catalog.json" or path.parent.name != "memory":
        raise ExemplarMemoryError(
            "exemplar catalog must use checkpoint/memory/catalog.json"
        )
    manifest = load_memory_manifest(path.parent / "manifest.json")
    catalog = load_json_object(path, "exemplar catalog")
    expected = {
        "schema_version",
        "identity_map_commitment",
        "exemplar_count",
        "exemplars",
        "catalog_digest",
    }
    _require_exact_fields(catalog, expected, "exemplar catalog")
    if catalog["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-catalog schema_version")
    _require_sha256(catalog["identity_map_commitment"], "identity_map_commitment")
    validate_digest(catalog, "catalog_digest", "exemplar catalog")
    if manifest["catalog_digest"] != catalog["catalog_digest"]:
        raise ExemplarMemoryError("memory manifest binds another exemplar catalog")
    if manifest["identity_map_commitment"] != catalog["identity_map_commitment"]:
        raise ExemplarMemoryError("memory manifest and catalog identity commitments differ")
    items = catalog["exemplars"]
    if not isinstance(items, list):
        raise ExemplarMemoryError("exemplar catalog exemplars must be an array")
    if catalog["exemplar_count"] != len(items):
        raise ExemplarMemoryError("exemplar catalog count does not match its entries")
    if manifest["exemplar_count"] != len(items):
        raise ExemplarMemoryError("memory manifest exemplar count differs from catalog")
    expected_files = {
        "catalog.json",
        "runtime/schemas/exemplar_query.schema.json",
        "runtime/schemas/exemplar_retrieval.schema.json",
        "runtime/schemas/exemplar_usage.schema.json",
        "runtime/schemas/target_evidence_guard_report.schema.json",
        "runtime/tools/exemplar_memory.py",
        "runtime/tools/guard_target_evidence.py",
        "runtime/tools/query_exemplars.py",
    }
    for record in items:
        if not isinstance(record, Mapping):
            raise ExemplarMemoryError("exemplar catalog item must be an object")
        for role in ("mechanism_summary", "t2_example", "t3_example"):
            file_ref = record.get(role)
            if not isinstance(file_ref, Mapping) or not isinstance(
                file_ref.get("path"), str
            ):
                raise ExemplarMemoryError(
                    f"exemplar catalog item has invalid {role} reference"
                )
            expected_files.add(file_ref["path"])
    observed_files = {record["path"] for record in manifest["files"]}
    if observed_files != expected_files:
        raise ExemplarMemoryError(
            "exemplar memory inventory is not closed; "
            f"missing={sorted(expected_files - observed_files)}, "
            f"extra={sorted(observed_files - expected_files)}"
        )
    exemplar_ids: set[str] = set()
    loaded: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        label = f"exemplar catalog item {index}"
        if not isinstance(item, Mapping):
            raise ExemplarMemoryError(f"{label} must be an object")
        _require_exact_fields(
            item,
            {
                "exemplar_id",
                "mechanism_summary",
                "t2_example",
                "t3_example",
                "exemplar_digest",
            },
            label,
        )
        exemplar_id = _require_exemplar_id(item["exemplar_id"], label)
        if exemplar_id in exemplar_ids:
            raise ExemplarMemoryError(f"duplicate exemplar_id {exemplar_id!r}")
        exemplar_ids.add(exemplar_id)
        validate_digest(item, "exemplar_digest", label)
        files: dict[str, dict[str, Any]] = {}
        for role, filename in (
            ("mechanism_summary", "mechanism_summary.json"),
            ("t2_example", "t2_example.json"),
            ("t3_example", "t3_example.json"),
        ):
            record = item[role]
            if not isinstance(record, Mapping):
                raise ExemplarMemoryError(f"{label}/{role} must be an object")
            _require_exact_fields(record, {"path", "sha256"}, f"{label}/{role}")
            expected_path = f"exemplars/{exemplar_id}/{filename}"
            if record["path"] != expected_path:
                raise ExemplarMemoryError(
                    f"{label}/{role} must resolve to {expected_path}"
                )
            _require_sha256(record["sha256"], f"{label}/{role}/sha256")
            file_path = _resolve_catalog_file(path.parent.resolve(), record["path"])
            actual_sha256 = file_sha256(file_path)
            if actual_sha256 != record["sha256"]:
                raise ExemplarMemoryError(
                    f"{label}/{role} has stale file hash: expected "
                    f"{record['sha256']}, got {actual_sha256}"
                )
            document = load_json_object(file_path, f"{label}/{role}")
            _reject_forbidden_memory_keys(
                document,
                f"{label}/{role}",
                exemplar_id=exemplar_id,
            )
            files[role] = document
        summary = _validate_summary(files["mechanism_summary"], exemplar_id)
        t2 = _validate_example(files["t2_example"], exemplar_id, target="t2")
        t3 = _validate_example(files["t3_example"], exemplar_id, target="t3")
        _validate_summary_counts(summary, t2=t2, t3=t3)
        loaded.append(
            {
                "exemplar_id": exemplar_id,
                "features": summary_features(summary),
                "summary": summary,
                "t2": t2,
                "t3": t3,
            }
        )
    if [item["exemplar_id"] for item in loaded] != sorted(exemplar_ids):
        raise ExemplarMemoryError(
            "exemplar catalog entries must be sorted by pseudonymous exemplar_id"
        )
    return manifest, catalog, loaded


def load_memory_manifest(manifest_path: Path) -> dict[str, Any]:
    path = manifest_path.expanduser()
    if path.parent.is_symlink():
        raise ExemplarMemoryError("exemplar memory root may not be a symlink")
    if path.is_symlink() or not path.is_file():
        raise ExemplarMemoryError(f"exemplar memory manifest is missing: {path}")
    if path.name != "manifest.json" or path.parent.name != "memory":
        raise ExemplarMemoryError("exemplar memory must use checkpoint/memory/manifest.json")
    root = path.parent.resolve()
    manifest = load_json_object(path, "exemplar memory manifest")
    _require_exact_fields(
        manifest,
        {
            "schema_version",
            "manifest_self_excluded",
            "catalog_path",
            "catalog_digest",
            "identity_map_commitment",
            "exemplar_count",
            "files",
            "memory_digest",
        },
        "exemplar memory manifest",
    )
    if manifest["schema_version"] != MEMORY_MANIFEST_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-memory manifest schema_version")
    if manifest["manifest_self_excluded"] is not True:
        raise ExemplarMemoryError("exemplar memory manifest must exclude itself")
    if manifest["catalog_path"] != "catalog.json":
        raise ExemplarMemoryError("exemplar memory catalog_path must be catalog.json")
    _require_sha256(manifest["catalog_digest"], "manifest catalog_digest")
    _require_sha256(
        manifest["identity_map_commitment"], "manifest identity_map_commitment"
    )
    if (
        isinstance(manifest["exemplar_count"], bool)
        or not isinstance(manifest["exemplar_count"], int)
        or manifest["exemplar_count"] not in {0, 5, 10, 15, 20, 25}
    ):
        raise ExemplarMemoryError(
            "manifest exemplar_count must follow the frozen 0/5/.../25 schedule"
        )
    validate_digest(manifest, "memory_digest", "exemplar memory manifest")
    files = manifest["files"]
    if not isinstance(files, list):
        raise ExemplarMemoryError("exemplar memory manifest files must be an array")
    recorded: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(files):
        label = f"memory manifest files/{index}"
        if not isinstance(record, Mapping):
            raise ExemplarMemoryError(f"{label} must be an object")
        _require_exact_fields(record, {"path", "sha256", "size_bytes", "mode"}, label)
        relative = _safe_memory_relative_path(record["path"])
        relative_string = relative.as_posix()
        if relative_string == "manifest.json":
            raise ExemplarMemoryError("memory manifest must exclude itself")
        if relative_string in recorded:
            raise ExemplarMemoryError(f"memory manifest repeats {relative_string}")
        _require_sha256(record["sha256"], f"{label}/sha256")
        if (
            isinstance(record["size_bytes"], bool)
            or not isinstance(record["size_bytes"], int)
            or record["size_bytes"] < 0
        ):
            raise ExemplarMemoryError(f"{label}/size_bytes must be nonnegative")
        if record["mode"] not in {"0444", "0555"}:
            raise ExemplarMemoryError(f"{label}/mode must be 0444 or 0555")
        recorded[relative_string] = dict(record)
    if list(recorded) != sorted(recorded):
        raise ExemplarMemoryError("memory manifest files must be path-sorted")
    actual: dict[str, dict[str, Any]] = {}
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise ExemplarMemoryError(f"symlink is forbidden in exemplar memory: {candidate}")
        if candidate.is_dir():
            if candidate.name == "__pycache__":
                raise ExemplarMemoryError("generated cache is forbidden in exemplar memory")
            continue
        if not candidate.is_file():
            raise ExemplarMemoryError(f"non-regular exemplar memory entry: {candidate}")
        relative = candidate.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            raise ExemplarMemoryError(f"hidden exemplar memory entry is forbidden: {relative}")
        mode = stat.S_IMODE(candidate.stat().st_mode)
        canonical_mode = "0555" if mode & stat.S_IXUSR else "0444"
        actual[relative] = {
            "path": relative,
            "sha256": file_sha256(candidate),
            "size_bytes": candidate.stat().st_size,
            "mode": canonical_mode,
        }
    if actual != recorded:
        changed = sorted(set(actual) | set(recorded))
        changed = [item for item in changed if actual.get(item) != recorded.get(item)]
        raise ExemplarMemoryError(
            "exemplar memory bytes differ from manifest"
            + (": " + ", ".join(changed) if changed else "")
        )
    if "catalog.json" not in recorded:
        raise ExemplarMemoryError("memory manifest does not inventory catalog.json")
    return copy.deepcopy(manifest)


def _validate_summary(
    summary: Mapping[str, Any],
    exemplar_id: str,
) -> dict[str, Any]:
    expected = {
        "schema_version",
        "exemplar_id",
        "counts",
        "operation_counts",
        "architecture_counts",
        "modality_counts",
        "graph_features",
        "barcoding_partitioning",
        "selection_branching",
        "chemistry_flags",
        "summary_digest",
    }
    _require_exact_fields(summary, expected, f"summary {exemplar_id}")
    if summary["schema_version"] != SUMMARY_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-summary schema_version")
    if summary["exemplar_id"] != exemplar_id:
        raise ExemplarMemoryError("exemplar summary belongs to another exemplar")
    validate_digest(summary, "summary_digest", f"summary {exemplar_id}")
    _validate_count_object(
        summary["counts"],
        {"oligo_families", "workflows", "states", "transitions"},
        "summary counts",
    )
    _validate_count_array(summary["operation_counts"], "operation", OPERATIONS)
    _validate_count_array(
        summary["architecture_counts"], "strand_architecture", ARCHITECTURES
    )
    _validate_count_array(summary["modality_counts"], "modality", MODALITIES)
    _validate_boolean_object(
        summary["barcoding_partitioning"],
        set(SUMMARY_BARCODING_MAP),
        "summary barcoding_partitioning",
    )
    _validate_boolean_object(
        summary["selection_branching"],
        set(SUMMARY_SELECTION_MAP),
        "summary selection_branching",
    )
    _validate_boolean_object(
        summary["chemistry_flags"],
        set(SUMMARY_CHEMISTRY_MAP),
        "summary chemistry_flags",
    )
    graph = summary["graph_features"]
    if not isinstance(graph, Mapping) or set(graph) != {
        "has_branching",
        "has_discarded_products",
        "has_multiple_workflows",
        "max_branch_factor",
    }:
        raise ExemplarMemoryError("summary graph_features has invalid fields")
    for field in ("has_branching", "has_discarded_products", "has_multiple_workflows"):
        if not isinstance(graph[field], bool):
            raise ExemplarMemoryError(f"summary graph_features/{field} must be boolean")
    if not isinstance(graph["max_branch_factor"], int) or graph["max_branch_factor"] < 0:
        raise ExemplarMemoryError(
            "summary graph_features/max_branch_factor must be a nonnegative integer"
        )
    return copy.deepcopy(dict(summary))


def _validate_example(
    document: Mapping[str, Any],
    exemplar_id: str,
    *,
    target: str,
) -> dict[str, Any]:
    collection = "oligos" if target == "t2" else "workflows"
    version = T2_EXAMPLE_SCHEMA_VERSION if target == "t2" else T3_EXAMPLE_SCHEMA_VERSION
    _require_exact_fields(
        document,
        {"schema_version", "exemplar_id", "example", "example_digest"},
        f"{target} example {exemplar_id}",
    )
    if document["schema_version"] != version:
        raise ExemplarMemoryError(f"unsupported {target}-example schema_version")
    if document["exemplar_id"] != exemplar_id:
        raise ExemplarMemoryError(f"{target} example belongs to another exemplar")
    example = document.get("example")
    if not isinstance(example, Mapping) or set(example) != {"protocol_id", collection}:
        raise ExemplarMemoryError(
            f"{target} example payload must contain only protocol_id and {collection}"
        )
    if example["protocol_id"] != exemplar_id:
        raise ExemplarMemoryError(
            f"{target} example payload belongs to another exemplar"
        )
    if not isinstance(example[collection], list):
        raise ExemplarMemoryError(f"{target} example {collection} must be an array")
    validate_digest(document, "example_digest", f"{target} example {exemplar_id}")
    return copy.deepcopy(dict(example))


def _validate_summary_counts(
    summary: Mapping[str, Any],
    *,
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
) -> None:
    workflows = t3["workflows"]
    states = [state for workflow in workflows for state in workflow.get("states", [])]
    transitions = [
        transition
        for workflow in workflows
        for transition in workflow.get("transitions", [])
    ]
    expected_counts = {
        "oligo_families": len(t2["oligos"]),
        "workflows": len(workflows),
        "states": len(states),
        "transitions": len(transitions),
    }
    if summary["counts"] != expected_counts:
        raise ExemplarMemoryError("exemplar summary counts differ from T2/T3 examples")
    _require_counter(
        summary["operation_counts"],
        Counter(
            transition.get("operation")
            for transition in transitions
            if isinstance(transition.get("operation"), str)
        ),
        "operation",
    )
    _require_counter(
        summary["architecture_counts"],
        Counter(
            state.get("strand_architecture")
            for state in states
            if isinstance(state.get("strand_architecture"), str)
        ),
        "strand_architecture",
    )
    _require_counter(
        summary["modality_counts"],
        Counter(
            output.get("modality")
            for workflow in workflows
            for output in workflow.get("final_outputs", [])
            if isinstance(output, Mapping) and isinstance(output.get("modality"), str)
        ),
        "modality",
    )


def summary_features(summary: Mapping[str, Any]) -> dict[str, list[str]]:
    features = {group: [] for group in FEATURE_GROUPS}
    features["modalities"] = sorted(
        item["modality"] for item in summary["modality_counts"] if item["count"] > 0
    )
    features["operations"] = sorted(
        item["operation"] for item in summary["operation_counts"] if item["count"] > 0
    )
    features["architectures"] = sorted(
        item["strand_architecture"]
        for item in summary["architecture_counts"]
        if item["count"] > 0
    )
    features["barcoding_partitioning"] = sorted(
        feature
        for field, feature in SUMMARY_BARCODING_MAP.items()
        if summary["barcoding_partitioning"][field]
    )
    features["selection_branching"] = sorted(
        feature
        for field, feature in SUMMARY_SELECTION_MAP.items()
        if summary["selection_branching"][field]
    )
    features["chemistries"] = sorted(
        feature
        for field, feature in SUMMARY_CHEMISTRY_MAP.items()
        if summary["chemistry_flags"][field]
    )
    return features


def retrieve(
    query: Mapping[str, Any],
    work_record: Mapping[str, Any],
    memory_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    exemplars: Sequence[Mapping[str, Any]],
    *,
    target_work_record_sha256: str,
    max_results: int = 3,
) -> dict[str, Any]:
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 3:
        raise ExemplarMemoryError("max_results must be an integer from 1 through 3")
    normalized_query = validate_query(query, work_record)
    _require_sha256(
        target_work_record_sha256,
        "target_work_record_sha256",
    )
    query_digest = canonical_digest(query)
    candidates: list[tuple[int, int, str, Mapping[str, Any], dict[str, list[str]]]] = []
    for exemplar in exemplars:
        numerator, denominator, matched = _weighted_similarity(
            normalized_query,
            exemplar["features"],
        )
        if numerator == 0:
            continue
        candidates.append(
            (
                numerator,
                denominator,
                exemplar["exemplar_id"],
                exemplar,
                matched,
            )
        )
    candidates.sort(key=cmp_to_key(_compare_candidates))
    matches: list[dict[str, Any]] = []
    for numerator, denominator, exemplar_id, exemplar, matched in candidates:
        subgraphs = extract_donor_subgraphs(
            exemplar["t2"],
            exemplar["t3"],
            normalized_query,
            transition_cap=3,
        )
        if not subgraphs:
            continue
        matches.append(
            {
                "rank": len(matches) + 1,
                "exemplar_id": exemplar_id,
                "score": {
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": round(numerator / denominator, 12),
                },
                "matched_features": matched,
                "mechanism_summary": {
                    "counts": copy.deepcopy(exemplar["summary"]["counts"]),
                    "features": copy.deepcopy(exemplar["features"]),
                    "graph_features": copy.deepcopy(
                        exemplar["summary"]["graph_features"]
                    ),
                },
                "donor_subgraphs": subgraphs,
            }
        )
        if len(matches) == max_results:
            break
    payload = {
        "schema_version": RETRIEVAL_SCHEMA_VERSION,
        "query_digest": query_digest,
        "target_work_record_sha256": target_work_record_sha256,
        "memory_digest": memory_manifest["memory_digest"],
        "catalog_digest": catalog["catalog_digest"],
        "ranking_policy": RANKING_POLICY,
        "max_results": max_results,
        "match_count": len(matches),
        "matches": matches,
    }
    return with_digest(payload, "retrieval_digest")


def build_usage(retrieval: Mapping[str, Any]) -> dict[str, Any]:
    validate_retrieval(retrieval)
    payload = {
        "schema_version": USAGE_SCHEMA_VERSION,
        "query_digest": retrieval["query_digest"],
        "target_work_record_sha256": retrieval["target_work_record_sha256"],
        "retrieval_digest": retrieval["retrieval_digest"],
        "scoring_scope": "diagnostic_only_excluded_from_benchmark_scores",
        "score_inclusion": False,
        "retrieved_exemplars": [
            {"rank": item["rank"], "exemplar_id": item["exemplar_id"]}
            for item in retrieval["matches"]
        ],
    }
    return with_digest(payload, "usage_digest")


def validate_usage(
    usage: Mapping[str, Any],
    retrieval: Mapping[str, Any],
) -> None:
    validate_retrieval(retrieval)
    expected = {
        "schema_version",
        "query_digest",
        "target_work_record_sha256",
        "retrieval_digest",
        "scoring_scope",
        "score_inclusion",
        "retrieved_exemplars",
        "usage_digest",
    }
    _require_exact_fields(usage, expected, "exemplar usage")
    if usage["schema_version"] != USAGE_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-usage schema_version")
    if usage["scoring_scope"] != "diagnostic_only_excluded_from_benchmark_scores":
        raise ExemplarMemoryError("exemplar usage must be excluded from scoring")
    if usage["score_inclusion"] is not False:
        raise ExemplarMemoryError("exemplar usage score_inclusion must be false")
    for field in (
        "query_digest",
        "target_work_record_sha256",
        "retrieval_digest",
    ):
        _require_sha256(usage[field], f"exemplar usage {field}")
        if usage[field] != retrieval[field]:
            raise ExemplarMemoryError(
                f"exemplar usage {field} differs from its retrieval"
            )
    expected_exemplars = [
        {"rank": item["rank"], "exemplar_id": item["exemplar_id"]}
        for item in retrieval["matches"]
    ]
    if usage["retrieved_exemplars"] != expected_exemplars:
        raise ExemplarMemoryError(
            "exemplar usage retrieved_exemplars differ from its retrieval"
        )
    validate_digest(usage, "usage_digest", "exemplar usage")


def validate_retrieval(retrieval: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "query_digest",
        "target_work_record_sha256",
        "memory_digest",
        "catalog_digest",
        "ranking_policy",
        "max_results",
        "match_count",
        "matches",
        "retrieval_digest",
    }
    _require_exact_fields(retrieval, expected, "exemplar retrieval")
    if retrieval["schema_version"] != RETRIEVAL_SCHEMA_VERSION:
        raise ExemplarMemoryError("unsupported exemplar-retrieval schema_version")
    if retrieval["ranking_policy"] != RANKING_POLICY:
        raise ExemplarMemoryError("unsupported exemplar-retrieval ranking policy")
    _require_sha256(retrieval["query_digest"], "retrieval query_digest")
    _require_sha256(
        retrieval["target_work_record_sha256"],
        "retrieval target_work_record_sha256",
    )
    _require_sha256(retrieval["memory_digest"], "retrieval memory_digest")
    _require_sha256(retrieval["catalog_digest"], "retrieval catalog_digest")
    max_results = retrieval["max_results"]
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 3:
        raise ExemplarMemoryError("retrieval max_results must be from 1 through 3")
    matches = retrieval["matches"]
    if not isinstance(matches, list) or len(matches) > max_results or len(matches) > 3:
        raise ExemplarMemoryError("retrieval exceeds its three-exemplar cap")
    if retrieval["match_count"] != len(matches):
        raise ExemplarMemoryError("retrieval match_count differs from matches")
    exemplar_ids: set[str] = set()
    previous_fraction: tuple[int, int] | None = None
    previous_id: str | None = None
    for index, match in enumerate(matches, start=1):
        if not isinstance(match, Mapping):
            raise ExemplarMemoryError("retrieval match must be an object")
        if match.get("rank") != index:
            raise ExemplarMemoryError("retrieval ranks must be contiguous")
        exemplar_id = _require_exemplar_id(match.get("exemplar_id"), "retrieval match")
        if exemplar_id in exemplar_ids:
            raise ExemplarMemoryError("retrieval repeats an exemplar")
        exemplar_ids.add(exemplar_id)
        score = match.get("score")
        if not isinstance(score, Mapping) or set(score) != {
            "numerator",
            "denominator",
            "value",
        }:
            raise ExemplarMemoryError("retrieval score is invalid")
        numerator = score["numerator"]
        denominator = score["denominator"]
        if (
            isinstance(numerator, bool)
            or not isinstance(numerator, int)
            or isinstance(denominator, bool)
            or not isinstance(denominator, int)
            or numerator < 1
            or denominator < numerator
            or score["value"] != round(numerator / denominator, 12)
        ):
            raise ExemplarMemoryError("retrieval score is internally inconsistent")
        if previous_fraction is not None:
            previous_numerator, previous_denominator = previous_fraction
            comparison = numerator * previous_denominator - (
                previous_numerator * denominator
            )
            if comparison > 0 or (
                comparison == 0 and exemplar_id <= (previous_id or "")
            ):
                raise ExemplarMemoryError("retrieval order is not deterministic")
        previous_fraction = (numerator, denominator)
        previous_id = exemplar_id
        _validate_retrieval_mechanism_summary(match.get("mechanism_summary"))
        matched_features = match.get("matched_features")
        _validate_feature_map(matched_features, "retrieval matched_features")
        if not any(matched_features[group] for group in FEATURE_GROUPS):
            raise ExemplarMemoryError("retrieval match has no matched feature")
        subgraphs = match.get("donor_subgraphs")
        if not isinstance(subgraphs, list) or not subgraphs:
            raise ExemplarMemoryError("retrieval match lacks donor subgraphs")
        transition_count = sum(
            len(subgraph.get("transitions", []))
            for subgraph in subgraphs
            if isinstance(subgraph, Mapping)
        )
        if transition_count > 3:
            raise ExemplarMemoryError(
                "retrieval exceeds the three-transition per-donor cap"
            )
        for subgraph in subgraphs:
            _validate_subgraph(subgraph)
    validate_digest(retrieval, "retrieval_digest", "exemplar retrieval")


def _validate_retrieval_mechanism_summary(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "counts",
        "features",
        "graph_features",
    }:
        raise ExemplarMemoryError("retrieval mechanism_summary has invalid fields")
    _validate_count_object(
        value["counts"],
        {"oligo_families", "workflows", "states", "transitions"},
        "retrieval mechanism_summary counts",
    )
    _validate_feature_map(value["features"], "retrieval mechanism_summary features")
    graph = value["graph_features"]
    if not isinstance(graph, Mapping) or set(graph) != {
        "has_branching",
        "has_discarded_products",
        "has_multiple_workflows",
        "max_branch_factor",
    }:
        raise ExemplarMemoryError(
            "retrieval mechanism_summary graph_features has invalid fields"
        )
    for field in (
        "has_branching",
        "has_discarded_products",
        "has_multiple_workflows",
    ):
        if not isinstance(graph[field], bool):
            raise ExemplarMemoryError(
                f"retrieval mechanism_summary graph_features/{field} must be boolean"
            )
    if (
        isinstance(graph["max_branch_factor"], bool)
        or not isinstance(graph["max_branch_factor"], int)
        or graph["max_branch_factor"] < 0
    ):
        raise ExemplarMemoryError(
            "retrieval mechanism_summary max_branch_factor must be nonnegative"
        )


def _validate_feature_map(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(FEATURE_GROUPS):
        raise ExemplarMemoryError(f"{label} has invalid feature groups")
    for group in FEATURE_GROUPS:
        items = value[group]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) for item in items)
            or items != sorted(set(items))
            or any(item not in ALLOWED_FEATURES[group] for item in items)
        ):
            raise ExemplarMemoryError(f"{label}/{group} is invalid")


def _weighted_similarity(
    query: Mapping[str, Sequence[str]],
    candidate: Mapping[str, Sequence[str]],
) -> tuple[int, int, dict[str, list[str]]]:
    numerator = 0
    denominator = 0
    matched: dict[str, list[str]] = {}
    for group in FEATURE_GROUPS:
        query_values = set(query[group])
        candidate_values = set(candidate[group])
        intersection = query_values & candidate_values
        union = query_values | candidate_values
        weight = FEATURE_GROUP_WEIGHTS[group]
        numerator += weight * len(intersection)
        denominator += weight * len(union)
        matched[group] = sorted(intersection)
    if denominator == 0:
        raise ExemplarMemoryError("feature union cannot be empty")
    return numerator, denominator, matched


def _compare_candidates(
    left: tuple[int, int, str, Mapping[str, Any], dict[str, list[str]]],
    right: tuple[int, int, str, Mapping[str, Any], dict[str, list[str]]],
) -> int:
    comparison = left[0] * right[1] - right[0] * left[1]
    if comparison:
        return -1 if comparison > 0 else 1
    if left[2] == right[2]:
        return 0
    return -1 if left[2] < right[2] else 1


def extract_donor_subgraphs(
    t2: Mapping[str, Any],
    t3: Mapping[str, Any],
    query: Mapping[str, Sequence[str]],
    *,
    transition_cap: int,
) -> list[dict[str, Any]]:
    if transition_cap < 1 or transition_cap > 3:
        raise ExemplarMemoryError("donor transition cap must be from 1 through 3")
    t2_oligos = t2.get("oligos")
    workflows = t3.get("workflows")
    if not isinstance(t2_oligos, list) or not isinstance(workflows, list):
        raise ExemplarMemoryError("T2/T3 exemplar collections are incomplete")
    t2_by_id = {
        item.get("oligo_id"): item
        for item in t2_oligos
        if isinstance(item, Mapping) and isinstance(item.get("oligo_id"), str)
    }
    if len(t2_by_id) != len(t2_oligos):
        raise ExemplarMemoryError("T2 exemplar oligo IDs are invalid or duplicated")
    requested = {
        (group, feature)
        for group in FEATURE_GROUPS
        for feature in query[group]
    }
    ranked: list[
        tuple[int, str, str, Mapping[str, Any], Mapping[str, Any]]
    ] = []
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            raise ExemplarMemoryError("T3 exemplar workflow must be an object")
        workflow_id = _required_string(workflow, "workflow_id", "T3 workflow")
        states = workflow.get("states")
        transitions = workflow.get("transitions")
        if not isinstance(states, list) or not isinstance(transitions, list):
            raise ExemplarMemoryError("T3 exemplar workflow is incomplete")
        state_ids = {
            state.get("state_id")
            for state in states
            if isinstance(state, Mapping) and isinstance(state.get("state_id"), str)
        }
        if len(state_ids) != len(states):
            raise ExemplarMemoryError("T3 exemplar state IDs are invalid or duplicated")
        states_by_id = {state["state_id"]: state for state in states}
        consumer_counts = Counter(
            state_id
            for transition in transitions
            if isinstance(transition, Mapping)
            for state_id in transition.get("substrate_state_ids", [])
            if isinstance(state_id, str)
        )
        reachable_modalities = _reachable_transition_modalities(workflow)
        workflow_modalities = {
            output.get("modality")
            for output in workflow.get("final_outputs", [])
            if isinstance(output, Mapping) and output.get("modality") in MODALITIES
        }
        for transition in transitions:
            if not isinstance(transition, Mapping):
                raise ExemplarMemoryError("T3 exemplar transition must be an object")
            transition_id = _required_string(
                transition, "transition_id", "T3 transition"
            )
            adjacent_ids = {
                state_id
                for field in ("substrate_state_ids", "product_state_ids")
                for state_id in _required_string_array(
                    transition, field, "T3 transition"
                )
            }
            if not adjacent_ids <= set(states_by_id):
                raise ExemplarMemoryError(
                    "T3 exemplar transition references an unknown state"
                )
            linked_oligo_ids = set(
                _required_string_array(transition, "oligo_ids", "T3 transition")
            )
            for state_id in adjacent_ids:
                linked_oligo_ids.update(_state_oligo_ids(states_by_id[state_id]))
            missing_oligos = sorted(linked_oligo_ids - set(t2_by_id))
            if missing_oligos:
                raise ExemplarMemoryError(
                    "T3 exemplar transition references missing T2 oligos: "
                    + ", ".join(missing_oligos)
                )
            local_values: list[Any] = [
                transition,
                *[states_by_id[state_id] for state_id in sorted(adjacent_ids)],
                *[t2_by_id[oligo_id] for oligo_id in sorted(linked_oligo_ids)],
                *sorted(reachable_modalities.get(transition_id, set())),
            ]
            local_features = derive_feature_pairs(local_values)
            if any(
                consumer_counts.get(state_id, 0) > 1
                for state_id in transition.get("substrate_state_ids", [])
            ):
                local_features.add(("selection_branching", "alternative_branching"))
            if (
                len(workflow_modalities) > 1
                and reachable_modalities.get(transition_id, set())
            ):
                local_features.add(("selection_branching", "modality_branching"))
            matched = requested & local_features
            match_weight = sum(
                FEATURE_GROUP_WEIGHTS[group] for group, _ in matched
            )
            if match_weight:
                ranked.append(
                    (-match_weight, workflow_id, transition_id, workflow, transition)
                )
    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = ranked[:transition_cap]
    selected_by_workflow: dict[str, list[Mapping[str, Any]]] = {}
    workflow_records: dict[str, Mapping[str, Any]] = {}
    for _, workflow_id, _, workflow, transition in selected:
        selected_by_workflow.setdefault(workflow_id, []).append(transition)
        workflow_records[workflow_id] = workflow
    subgraphs: list[dict[str, Any]] = []
    for workflow_id in sorted(selected_by_workflow):
        workflow = workflow_records[workflow_id]
        transitions = selected_by_workflow[workflow_id]
        state_ids = {
            state_id
            for transition in transitions
            for field in ("substrate_state_ids", "product_state_ids")
            for state_id in _required_string_array(transition, field, "T3 transition")
        }
        states_by_id = {
            state.get("state_id"): state
            for state in workflow["states"]
            if isinstance(state, Mapping) and isinstance(state.get("state_id"), str)
        }
        missing_states = sorted(state_ids - set(states_by_id))
        if missing_states:
            raise ExemplarMemoryError(
                "donor transition references missing adjacent states: "
                + ", ".join(missing_states)
            )
        states = [
            copy.deepcopy(state)
            for state in workflow["states"]
            if state.get("state_id") in state_ids
        ]
        oligo_ids = {
            oligo_id
            for transition in transitions
            for oligo_id in _required_string_array(
                transition, "oligo_ids", "T3 transition"
            )
        }
        for state in states:
            oligo_ids.update(_state_oligo_ids(state))
        missing_oligos = sorted(oligo_ids - set(t2_by_id))
        if missing_oligos:
            raise ExemplarMemoryError(
                "donor subgraph references missing linked T2 oligos: "
                + ", ".join(missing_oligos)
            )
        subgraph = {
            "workflow_id": workflow_id,
            "focus_transition_ids": [item["transition_id"] for item in transitions],
            "states": states,
            "transitions": [copy.deepcopy(item) for item in transitions],
            "linked_t2": [
                copy.deepcopy(item)
                for item in t2_oligos
                if item.get("oligo_id") in oligo_ids
            ],
        }
        _validate_subgraph(subgraph)
        subgraphs.append(subgraph)
    return subgraphs


def _reachable_transition_modalities(
    workflow: Mapping[str, Any],
) -> dict[str, set[str]]:
    transitions = workflow.get("transitions", [])
    final_outputs = workflow.get("final_outputs", [])
    terminal_modalities: dict[str, set[str]] = defaultdict(set)
    for output in final_outputs:
        if (
            isinstance(output, Mapping)
            and isinstance(output.get("state_id"), str)
            and output.get("modality") in MODALITIES
        ):
            terminal_modalities[output["state_id"]].add(output["modality"])
    downstream: dict[str, set[str]] = defaultdict(set)
    for transition in transitions:
        if not isinstance(transition, Mapping):
            continue
        substrates = transition.get("substrate_state_ids", [])
        products = transition.get("product_state_ids", [])
        if isinstance(substrates, list) and isinstance(products, list):
            for substrate in substrates:
                if isinstance(substrate, str):
                    downstream[substrate].update(
                        product for product in products if isinstance(product, str)
                    )

    def reachable(start: Sequence[str]) -> set[str]:
        pending = list(start)
        seen: set[str] = set()
        result: set[str] = set()
        while pending:
            state_id = pending.pop()
            if state_id in seen:
                continue
            seen.add(state_id)
            result.update(terminal_modalities.get(state_id, set()))
            pending.extend(sorted(downstream.get(state_id, set()) - seen))
        return result

    result: dict[str, set[str]] = {}
    for transition in transitions:
        if not isinstance(transition, Mapping):
            continue
        transition_id = transition.get("transition_id")
        products = transition.get("product_state_ids")
        if isinstance(transition_id, str) and isinstance(products, list):
            result[transition_id] = reachable(
                [item for item in products if isinstance(item, str)]
            )
    return result


def _validate_subgraph(subgraph: Any) -> None:
    if not isinstance(subgraph, Mapping) or set(subgraph) != {
        "workflow_id",
        "focus_transition_ids",
        "states",
        "transitions",
        "linked_t2",
    }:
        raise ExemplarMemoryError("donor subgraph has invalid fields")
    _required_string(subgraph, "workflow_id", "donor subgraph")
    focus = _required_string_array(
        subgraph, "focus_transition_ids", "donor subgraph"
    )
    if not focus or len(focus) > 3 or len(focus) != len(set(focus)):
        raise ExemplarMemoryError("donor subgraph focus transitions are invalid")
    states = subgraph["states"]
    transitions = subgraph["transitions"]
    linked_t2 = subgraph["linked_t2"]
    if (
        not isinstance(states, list)
        or not states
        or not isinstance(transitions, list)
        or not transitions
        or len(transitions) > 3
        or not isinstance(linked_t2, list)
    ):
        raise ExemplarMemoryError("donor subgraph collections are invalid")
    state_ids = {
        state.get("state_id")
        for state in states
        if isinstance(state, Mapping) and isinstance(state.get("state_id"), str)
    }
    transition_ids = {
        transition.get("transition_id")
        for transition in transitions
        if isinstance(transition, Mapping)
        and isinstance(transition.get("transition_id"), str)
    }
    oligo_ids = {
        oligo.get("oligo_id")
        for oligo in linked_t2
        if isinstance(oligo, Mapping) and isinstance(oligo.get("oligo_id"), str)
    }
    if len(state_ids) != len(states) or len(transition_ids) != len(transitions):
        raise ExemplarMemoryError("donor subgraph IDs are invalid or duplicated")
    if set(focus) != transition_ids:
        raise ExemplarMemoryError("donor subgraph focus IDs differ from transitions")
    referenced_states: set[str] = set()
    referenced_oligos: set[str] = set()
    for transition in transitions:
        for field in ("substrate_state_ids", "product_state_ids"):
            referenced_states.update(
                _required_string_array(transition, field, "donor transition")
            )
        referenced_oligos.update(
            _required_string_array(transition, "oligo_ids", "donor transition")
        )
    for state in states:
        referenced_oligos.update(_state_oligo_ids(state))
    if referenced_states != state_ids:
        raise ExemplarMemoryError(
            "donor subgraph must contain exactly the focus transitions' adjacent states"
        )
    if referenced_oligos != oligo_ids:
        raise ExemplarMemoryError(
            "donor subgraph must contain exactly its transitively linked T2 oligos"
        )


def _state_oligo_ids(state: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for strand in state.get("strands", []):
        if not isinstance(strand, Mapping):
            continue
        for segment in strand.get("segments", []):
            if not isinstance(segment, Mapping):
                continue
            for derivation in segment.get("oligo_derivations", []):
                if isinstance(derivation, Mapping) and isinstance(
                    derivation.get("oligo_id"), str
                ):
                    result.add(derivation["oligo_id"])
    return result


def _chemistry_operations(chemistry: str) -> set[str]:
    mapping = {
        "reverse_transcription": {"reverse_transcription"},
        "template_switching": {"reverse_transcription", "extension", "other"},
        "ligation": {"ligation"},
        "tagmentation": {"tagmentation"},
        "pcr": {"pcr", "amplification"},
        "restriction": {"fragmentation", "other"},
        "conversion": {"other"},
    }
    return mapping[chemistry]


def _resolve_catalog_file(memory_root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ExemplarMemoryError("catalog path must be a non-empty string")
    pure = _safe_memory_relative_path(relative)
    candidate = memory_root.joinpath(*pure.parts)
    current = memory_root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ExemplarMemoryError(f"symlink is forbidden in exemplar memory: {relative}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(memory_root) or not resolved.is_file():
        raise ExemplarMemoryError(f"exemplar file is missing or escapes memory: {relative}")
    return resolved


def _safe_memory_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ExemplarMemoryError("memory path must be a non-empty string")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\x00" in value
    ):
        raise ExemplarMemoryError(f"unsafe exemplar memory path {value!r}")
    return pure


def _reject_forbidden_memory_keys(
    value: Any,
    location: str,
    *,
    exemplar_id: str,
    pointer: str = "",
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            child_pointer = f"{pointer}/{key}"
            allowed_pseudonymous_protocol = (
                key == "protocol_id"
                and pointer == "/example"
                and child == exemplar_id
            )
            if (
                not allowed_pseudonymous_protocol
                and any(
                    fragment in normalized
                    for fragment in FORBIDDEN_MEMORY_KEY_FRAGMENTS
                )
            ):
                raise ExemplarMemoryError(
                    f"{location} contains forbidden memory key {key!r}"
                )
            _reject_forbidden_memory_keys(
                child,
                f"{location}/{key}",
                exemplar_id=exemplar_id,
                pointer=child_pointer,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_memory_keys(
                child,
                f"{location}/{index}",
                exemplar_id=exemplar_id,
                pointer=f"{pointer}/{index}",
            )


def _reject_forbidden_target_input_keys(value: Any, pointer: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(
                fragment in normalized
                for fragment in FORBIDDEN_TARGET_INPUT_KEY_FRAGMENTS
            ):
                raise TargetWorkRecordError(
                    f"target work record contains forbidden input key "
                    f"{pointer}/{key}"
                )
            _reject_forbidden_target_input_keys(child, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_target_input_keys(child, f"{pointer}/{index}")


def _validate_count_object(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ExemplarMemoryError(f"{label} has invalid fields")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value.values()):
        raise ExemplarMemoryError(f"{label} values must be nonnegative integers")


def _validate_count_array(value: Any, field: str, allowed: set[str]) -> None:
    if not isinstance(value, list):
        raise ExemplarMemoryError(f"summary {field}_counts must be an array")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {field, "count"}:
            raise ExemplarMemoryError(f"summary {field}_counts item is invalid")
        key = item[field]
        if key not in allowed or key in seen:
            raise ExemplarMemoryError(f"summary {field}_counts key is invalid")
        seen.add(key)
        if isinstance(item["count"], bool) or not isinstance(item["count"], int) or item["count"] < 1:
            raise ExemplarMemoryError(f"summary {field}_counts count must be positive")
    if [item[field] for item in value] != sorted(seen):
        raise ExemplarMemoryError(f"summary {field}_counts must be sorted")


def _validate_boolean_object(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ExemplarMemoryError(f"{label} has invalid fields")
    if any(not isinstance(item, bool) for item in value.values()):
        raise ExemplarMemoryError(f"{label} values must be booleans")


def _require_counter(value: Any, expected: Counter[str], field: str) -> None:
    actual = Counter({item[field]: item["count"] for item in value})
    if actual != expected:
        raise ExemplarMemoryError(f"summary {field}_counts differ from T3 example")


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ExemplarMemoryError(
            f"{label} fields differ; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ExemplarMemoryError(f"{label} must be a SHA-256 digest")
    return value


def _require_exemplar_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not EXEMPLAR_ID_RE.fullmatch(value):
        raise ExemplarMemoryError(f"{label} has an invalid pseudonymous exemplar_id")
    return value


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ExemplarMemoryError(f"{label}/{key} must be a non-empty string")
    return result


def _required_string_array(
    value: Mapping[str, Any],
    key: str,
    label: str,
) -> list[str]:
    result = value.get(key)
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ExemplarMemoryError(f"{label}/{key} must be an array of strings")
    if len(result) != len(set(result)):
        raise ExemplarMemoryError(f"{label}/{key} contains duplicates")
    return result


def iter_json_values(value: Any, pointer: str = "") -> Iterable[tuple[str, Any]]:
    yield pointer or "/", value
    if isinstance(value, Mapping):
        for key, child in value.items():
            encoded = str(key).replace("~", "~0").replace("/", "~1")
            yield from iter_json_values(child, f"{pointer}/{encoded}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_json_values(child, f"{pointer}/{index}")
