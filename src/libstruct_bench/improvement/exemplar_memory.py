from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import shutil
import stat
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.audit.connected_process import (
    ConnectedProcessMigrationError,
    migrate_connected_process_t3,
)

from .artifacts import (
    CapabilityImprovementError,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    repository_root,
    safe_relative_path,
    thaw_tree,
    validate_digest,
    with_digest,
)
from .split_design import (
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_TRANSFER_PANEL,
    FIXED_VALIDATION_PANEL,
)


IDENTITY_MAP_SCHEMA_VERSION = "libstruct.libgen_exemplar_identity_map.v1"
IDENTITY_MAP_LINEAGE_ID = "cumulative-C0-C25-v1"
MEMORY_CATALOG_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory.v1"
MEMORY_MANIFEST_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory_manifest.v1"
MECHANISM_SUMMARY_SCHEMA_VERSION = "libstruct.libgen_exemplar_mechanism_summary.v1"
T2_EXAMPLE_SCHEMA_VERSION = "libstruct.libgen_t2_example.v1"
T3_EXAMPLE_SCHEMA_VERSION = "libstruct.libgen_t3_example.v1"

IDENTITY_MAP_RELATIVE_PATH = Path("private/exemplar_identity_map.json")
MEMORY_RUNTIME_SOURCE = repository_root() / "improvement" / "exemplar_memory_runtime"
MEMORY_RUNTIME_FILES = (
    "runtime/schemas/exemplar_query.schema.json",
    "runtime/schemas/exemplar_retrieval.schema.json",
    "runtime/schemas/exemplar_usage.schema.json",
    "runtime/schemas/target_evidence_guard_report.schema.json",
    "runtime/tools/exemplar_memory.py",
    "runtime/tools/guard_target_evidence.py",
    "runtime/tools/query_exemplars.py",
)
APPROVED_T2_FILENAME = "groundtruth_oligos.json"
APPROVED_T3_FILENAME = "groundtruth_library_generation_workflow.json"
EXEMPLAR_ID_RE = re.compile(r"^exm-[a-f0-9]{32}$")
PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:file://|[A-Za-z]:\\\\|/(?:Users|home|private|var|tmp|data)/)[^\s]*"
)
URL_RE = re.compile(r"(?i)https?://[^\s]*")


class _DuplicateJsonKey(ValueError):
    pass


def training_protocol_ids() -> tuple[str, ...]:
    return tuple(
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    )


def build_exemplar_identity_map(
    *,
    split_digest: str,
    mapping_nonce: str,
) -> dict[str, Any]:
    """Build the frozen private 25-protocol map from one random pinned nonce."""

    if not re.fullmatch(r"[a-f0-9]{64}", split_digest):
        raise CapabilityImprovementError(
            "exemplar identity map requires a valid frozen split digest"
        )
    if not re.fullmatch(r"[a-f0-9]{64}", mapping_nonce):
        raise CapabilityImprovementError(
            "exemplar identity-map nonce must be 256 random bits encoded as hex"
        )
    entries = []
    for protocol_id in sorted(training_protocol_ids()):
        opaque = hmac.new(
            bytes.fromhex(mapping_nonce),
            protocol_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        entries.append({"protocol_id": protocol_id, "exemplar_id": f"exm-{opaque}"})
    result = with_digest(
        {
            "schema_version": IDENTITY_MAP_SCHEMA_VERSION,
            "lineage_id": IDENTITY_MAP_LINEAGE_ID,
            "split_digest": split_digest,
            "mapping_nonce": mapping_nonce,
            "protocol_count": 25,
            "entries": entries,
        },
        "identity_map_digest",
    )
    _validate_identity_map_document(result, split_digest=split_digest)
    return result


def ensure_exemplar_identity_map(
    *,
    experiment_root: Path,
    split_digest: str,
) -> dict[str, Any]:
    """Create once or validate the canonical orchestrator-only identity map."""

    root = experiment_root.expanduser().resolve()
    map_path = root / IDENTITY_MAP_RELATIVE_PATH
    if map_path.is_symlink():
        raise CapabilityImprovementError(
            f"private exemplar identity map may not be a symlink: {map_path}"
        )
    if map_path.exists():
        return validate_exemplar_identity_map(map_path, split_digest=split_digest)
    private_root = root / "private"
    if private_root.is_symlink():
        raise CapabilityImprovementError(
            f"experiment private directory may not be a symlink: {private_root}"
        )
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = build_exemplar_identity_map(
        split_digest=split_digest,
        mapping_nonce=secrets.token_hex(32),
    )
    write_json_atomic(map_path, result, mode=0o400)
    return result


def validate_exemplar_identity_map(
    path: Path,
    *,
    split_digest: str,
) -> dict[str, Any]:
    unresolved = path.expanduser()
    if unresolved.is_symlink():
        raise CapabilityImprovementError(
            f"private exemplar identity map may not be a symlink: {unresolved}"
        )
    document = load_and_validate(
        unresolved,
        schema_filename="exemplar_identity_map.schema.json",
        digest_field="identity_map_digest",
        label="private exemplar identity map",
    )
    _validate_identity_map_document(document, split_digest=split_digest)
    return document


def _validate_identity_map_document(
    document: Mapping[str, Any],
    *,
    split_digest: str,
) -> None:
    try:
        validate_document(
            dict(document),
            improvement_schema_root() / "exemplar_identity_map.schema.json",
            label="private exemplar identity map",
        )
        validate_digest(document, "identity_map_digest")
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    if (
        document["lineage_id"] != IDENTITY_MAP_LINEAGE_ID
        or document["split_digest"] != split_digest
    ):
        raise CapabilityImprovementError(
            "private exemplar identity map belongs to another split lineage"
        )
    entries = document["entries"]
    observed_protocols = [item["protocol_id"] for item in entries]
    observed_exemplars = [item["exemplar_id"] for item in entries]
    expected_protocols = sorted(training_protocol_ids())
    if observed_protocols != expected_protocols:
        raise CapabilityImprovementError(
            "private exemplar identity map does not exactly cover training protocols"
        )
    if len(observed_exemplars) != len(set(observed_exemplars)):
        raise CapabilityImprovementError(
            "private exemplar identity map contains duplicate pseudonyms"
        )
    forbidden = set(FIXED_VALIDATION_PANEL) | set(FINAL_TRANSFER_PANEL)
    if set(observed_protocols) & forbidden:
        raise CapabilityImprovementError(
            "validation or final-test protocol entered exemplar identity map"
        )
    expected_pairs = []
    for protocol_id in expected_protocols:
        opaque = hmac.new(
            bytes.fromhex(document["mapping_nonce"]),
            protocol_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        expected_pairs.append(
            {"protocol_id": protocol_id, "exemplar_id": f"exm-{opaque}"}
        )
    if entries != expected_pairs:
        raise CapabilityImprovementError(
            "private exemplar identity map pseudonyms do not match its pinned nonce"
        )


def create_empty_exemplar_memory(
    *,
    memory_root: Path,
    identity_map: Mapping[str, Any],
    runtime_source: Path = MEMORY_RUNTIME_SOURCE,
) -> dict[str, Any]:
    """Create a C0 memory tree with runtime controls and no donor exemplars."""

    root = memory_root.expanduser().resolve()
    if root.exists():
        raise CapabilityImprovementError(f"exemplar memory already exists: {root}")
    root.mkdir(parents=True)
    (root / "exemplars").mkdir()
    _copy_runtime_seed(runtime_source=runtime_source, memory_root=root)
    catalog = with_digest(
        {
            "schema_version": MEMORY_CATALOG_SCHEMA_VERSION,
            "identity_map_commitment": identity_map["identity_map_digest"],
            "exemplar_count": 0,
            "exemplars": [],
        },
        "catalog_digest",
    )
    _validate_catalog_document(catalog)
    write_json_atomic(root / "catalog.json", catalog)
    write_exemplar_memory_manifest(root)
    freeze_tree(root)
    return validate_exemplar_memory(
        root,
        expected_count=0,
        identity_map=identity_map,
    )


def extend_exemplar_memory_from_packet(
    *,
    parent_memory_root: Path,
    output_memory_root: Path,
    packet_path: Path,
    identity_map: Mapping[str, Any],
    experiment_digest: str,
    batch_id: str,
    expected_count: int,
) -> dict[str, Any]:
    """Append exactly one packet's five approved GT exemplars to memory."""

    parent_expected_count = expected_count - 5
    if parent_expected_count not in {0, 5, 10, 15, 20}:
        raise CapabilityImprovementError(
            "learned exemplar memory must advance by one five-protocol batch"
        )
    validate_exemplar_memory(
        parent_memory_root,
        expected_count=parent_expected_count,
        identity_map=identity_map,
    )
    packet = load_and_validate(
        packet_path,
        schema_filename="batch_packet.schema.json",
        digest_field="packet_digest",
        label="capability batch packet",
    )
    batch = next(
        (item for item in FINAL_DEVELOPMENT_BATCHES if item["batch_id"] == batch_id),
        None,
    )
    if batch is None:
        raise CapabilityImprovementError(f"unknown training batch: {batch_id}")
    expected_protocols = list(batch["protocol_ids"])
    if (
        packet["experiment_digest"] != experiment_digest
        or packet["batch_id"] != batch_id
        or packet["branch"] != "cumulative"
        or packet["reveal_state"] != "revealed"
        or packet["eligibility_status"] != "eligible_for_improvement"
        or packet["protocol_ids"] != expected_protocols
        or packet["phase"] != batch["phase"]
    ):
        raise CapabilityImprovementError(
            "exemplar projection packet differs from the frozen training batch"
        )
    forbidden = set(FIXED_VALIDATION_PANEL) | set(FINAL_TRANSFER_PANEL)
    if set(packet["protocol_ids"]) & forbidden:
        raise CapabilityImprovementError(
            "validation or final-test protocol cannot enter exemplar memory"
        )

    output = output_memory_root.expanduser().resolve()
    if output.exists():
        raise CapabilityImprovementError(f"exemplar memory already exists: {output}")
    shutil.copytree(parent_memory_root.expanduser().resolve(), output)
    thaw_tree(output)
    (output / "manifest.json").unlink()
    catalog = _load_catalog(output / "catalog.json")
    known_ids = {item["exemplar_id"] for item in catalog["exemplars"]}
    mapping = {
        item["protocol_id"]: item["exemplar_id"] for item in identity_map["entries"]
    }
    new_items: list[dict[str, Any]] = []
    for protocol_id in packet["protocol_ids"]:
        exemplar_id = mapping.get(protocol_id)
        if exemplar_id is None:
            raise CapabilityImprovementError(
                f"training protocol lacks a private exemplar pseudonym: {protocol_id}"
            )
        if exemplar_id in known_ids:
            raise CapabilityImprovementError(
                f"packet attempts to reuse an existing exemplar: {exemplar_id}"
            )
        t2_source, t3_source = _approved_groundtruth_pair(packet, protocol_id)
        t2_document = _load_groundtruth_json(
            t2_source,
            schema_filename="oligo_groundtruth.schema.json",
            label=f"approved T2 ground truth for {protocol_id}",
        )
        t3_document = _load_groundtruth_json(
            t3_source,
            schema_filename="library_generation_workflow.schema.json",
            label=f"approved T3 ground truth for {protocol_id}",
            allow_legacy_connected_process=True,
        )
        if (
            t2_document.get("protocol_id") != protocol_id
            or t3_document.get("protocol_id") != protocol_id
        ):
            raise CapabilityImprovementError(
                f"approved ground truth identity differs from packet: {protocol_id}"
            )
        summary, t2_example, t3_example = project_groundtruth_exemplar(
            protocol_id=protocol_id,
            exemplar_id=exemplar_id,
            t2_groundtruth=t2_document,
            t3_groundtruth=t3_document,
        )
        new_items.append(
            _write_exemplar_item(
                memory_root=output,
                exemplar_id=exemplar_id,
                mechanism_summary=summary,
                t2_example=t2_example,
                t3_example=t3_example,
            )
        )
    catalog_payload = {
        key: copy.deepcopy(value)
        for key, value in catalog.items()
        if key != "catalog_digest"
    }
    catalog_payload["exemplars"] = sorted(
        [*catalog["exemplars"], *new_items],
        key=lambda item: item["exemplar_id"],
    )
    catalog_payload["exemplar_count"] = len(catalog_payload["exemplars"])
    updated_catalog = with_digest(catalog_payload, "catalog_digest")
    if updated_catalog["exemplar_count"] != expected_count:
        raise CapabilityImprovementError(
            "exemplar memory did not advance to the expected cumulative count"
        )
    _validate_catalog_document(updated_catalog)
    write_json_atomic(output / "catalog.json", updated_catalog)
    write_exemplar_memory_manifest(output)
    freeze_tree(output)
    return validate_exemplar_memory(
        output,
        expected_count=expected_count,
        identity_map=identity_map,
    )


def validate_exemplar_memory_projections(
    *,
    memory_root: Path,
    packet_paths: Sequence[Path],
    identity_map: Mapping[str, Any],
    experiment_digest: str,
    expected_count: int,
) -> dict[str, str]:
    """Prove every public exemplar is the deterministic training-GT projection.

    The ordinary memory validator proves closed inventory, public schemas, and
    pseudonymous cumulative membership.  This stronger validator additionally
    recomputes each prediction-shaped document from the approved ground-truth
    bytes pinned by the canonical training packets.  It is used by isolation
    audits to distinguish legitimate training-derived motifs from validation
    material that merely happens to share the same sequence or scaffold.

    The return value contains only verified public projection paths and hashes;
    private protocol identities and source paths never leave this function.
    """

    if expected_count not in {0, 5, 10, 15, 20, 25}:
        raise CapabilityImprovementError(
            "projected exemplar verification requires a cumulative checkpoint count"
        )
    validate_exemplar_memory(
        memory_root,
        expected_count=expected_count,
        identity_map=identity_map,
    )
    expected_batches = list(FINAL_DEVELOPMENT_BATCHES[: expected_count // 5])
    if len(packet_paths) != len(expected_batches):
        raise CapabilityImprovementError(
            "projected exemplar verification requires the exact training packet prefix"
        )

    packet_by_protocol: dict[str, Mapping[str, Any]] = {}
    for expected_batch, packet_path in zip(expected_batches, packet_paths, strict=True):
        packet = load_and_validate(
            packet_path,
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label="capability batch packet for exemplar verification",
        )
        expected_protocols = list(expected_batch["protocol_ids"])
        if (
            packet["experiment_digest"] != experiment_digest
            or packet["batch_id"] != expected_batch["batch_id"]
            or packet["branch"] != "cumulative"
            or packet["phase"] != expected_batch["phase"]
            or packet["protocol_ids"] != expected_protocols
            or packet["reveal_state"] != "revealed"
            or packet["eligibility_status"] != "eligible_for_improvement"
        ):
            raise CapabilityImprovementError(
                "exemplar verification packet differs from the frozen training batch"
            )
        for protocol_id in expected_protocols:
            if protocol_id in packet_by_protocol:
                raise CapabilityImprovementError(
                    "exemplar verification repeats a training protocol"
                )
            packet_by_protocol[protocol_id] = packet

    expected_protocols = list(training_protocol_ids()[:expected_count])
    if set(packet_by_protocol) != set(expected_protocols):
        raise CapabilityImprovementError(
            "exemplar verification packets differ from the cumulative training prefix"
        )
    protocol_to_exemplar = {
        item["protocol_id"]: item["exemplar_id"] for item in identity_map["entries"]
    }
    catalog = _load_catalog(memory_root.expanduser().resolve() / "catalog.json")
    catalog_by_id = {item["exemplar_id"]: item for item in catalog["exemplars"]}
    expected_ids = {protocol_to_exemplar[item] for item in expected_protocols}
    if set(catalog_by_id) != expected_ids:
        raise CapabilityImprovementError(
            "exemplar catalog differs from the cumulative training identity prefix"
        )

    verified: dict[str, str] = {}
    schema_by_key = {
        "mechanism_summary": (
            "exemplar_mechanism_summary.schema.json",
            "summary_digest",
        ),
        "t2_example": ("t2_example.schema.json", "example_digest"),
        "t3_example": ("t3_example.schema.json", "example_digest"),
    }
    for protocol_id in expected_protocols:
        exemplar_id = protocol_to_exemplar[protocol_id]
        packet = packet_by_protocol[protocol_id]
        t2_source, t3_source = _approved_groundtruth_pair(packet, protocol_id)
        t2_document = _load_groundtruth_json(
            t2_source,
            schema_filename="oligo_groundtruth.schema.json",
            label=f"approved T2 ground truth for {protocol_id}",
        )
        t3_document = _load_groundtruth_json(
            t3_source,
            schema_filename="library_generation_workflow.schema.json",
            label=f"approved T3 ground truth for {protocol_id}",
            allow_legacy_connected_process=True,
        )
        if (
            t2_document.get("protocol_id") != protocol_id
            or t3_document.get("protocol_id") != protocol_id
        ):
            raise CapabilityImprovementError(
                "approved ground-truth identity differs during exemplar verification"
            )
        summary, t2_example, t3_example = project_groundtruth_exemplar(
            protocol_id=protocol_id,
            exemplar_id=exemplar_id,
            t2_groundtruth=t2_document,
            t3_groundtruth=t3_document,
        )
        expected_documents = {
            "mechanism_summary": summary,
            "t2_example": t2_example,
            "t3_example": t3_example,
        }
        catalog_item = catalog_by_id[exemplar_id]
        for key, expected_document in expected_documents.items():
            reference = catalog_item[key]
            relative = safe_relative_path(reference["path"])
            public_path = memory_root.expanduser().resolve() / relative
            schema_filename, digest_field = schema_by_key[key]
            observed_document = _load_public_document(
                public_path,
                schema_filename=schema_filename,
                digest_field=digest_field,
                label=f"verified {key} for {exemplar_id}",
            )
            if observed_document != expected_document:
                raise CapabilityImprovementError(
                    "exemplar differs from its deterministic approved-GT projection"
                )
            verified[relative.as_posix()] = sha256_file(public_path)
    return dict(sorted(verified.items()))


def project_groundtruth_exemplar(
    *,
    protocol_id: str,
    exemplar_id: str,
    t2_groundtruth: Mapping[str, Any],
    t3_groundtruth: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Deterministically project validated canonical GT into public examples."""

    if protocol_id not in set(training_protocol_ids()):
        raise CapabilityImprovementError(
            "only frozen training protocols may become exemplars"
        )
    if protocol_id in set(FIXED_VALIDATION_PANEL) | set(FINAL_TRANSFER_PANEL):
        raise CapabilityImprovementError(
            "validation or final-test protocol cannot become an exemplar"
        )
    if not EXEMPLAR_ID_RE.fullmatch(exemplar_id):
        raise CapabilityImprovementError("invalid opaque exemplar ID")
    if (
        t2_groundtruth.get("protocol_id") != protocol_id
        or t3_groundtruth.get("protocol_id") != protocol_id
    ):
        raise CapabilityImprovementError(
            "ground-truth documents do not match the projected training protocol"
        )
    protocol_names = {
        value
        for value in (
            t2_groundtruth.get("protocol_name"),
            t3_groundtruth.get("protocol_name"),
        )
        if isinstance(value, str) and value.strip()
    }
    protocol_aliases = {
        protocol_id,
        protocol_id.replace("_", " "),
        protocol_id.replace("_", "-"),
        *protocol_names,
    }
    redactions = _public_identity_literals() | protocol_aliases
    oligo_map = {
        item["oligo_id"]: f"oligo-{index:03d}"
        for index, item in enumerate(t2_groundtruth.get("oligos", []), start=1)
    }
    if len(oligo_map) != len(t2_groundtruth.get("oligos", [])):
        raise CapabilityImprovementError("approved T2 has duplicate oligo IDs")
    t2_example = _project_t2(
        exemplar_id=exemplar_id,
        groundtruth=t2_groundtruth,
        oligo_map=oligo_map,
        redactions=redactions,
    )
    t3_example = _project_t3(
        exemplar_id=exemplar_id,
        groundtruth=t3_groundtruth,
        oligo_map=oligo_map,
        redactions=redactions,
    )
    summary = _build_mechanism_summary(
        exemplar_id=exemplar_id,
        t2_groundtruth=t2_groundtruth,
        t3_groundtruth=t3_groundtruth,
    )
    for document, schema_filename, digest_field, label in (
        (
            summary,
            "exemplar_mechanism_summary.schema.json",
            "summary_digest",
            "exemplar mechanism summary",
        ),
        (t2_example, "t2_example.schema.json", "example_digest", "T2 exemplar"),
        (t3_example, "t3_example.schema.json", "example_digest", "T3 exemplar"),
    ):
        try:
            validate_document(
                document,
                improvement_schema_root() / schema_filename,
                label=label,
            )
            validate_digest(document, digest_field)
        except AuditArtifactError as error:
            raise CapabilityImprovementError(str(error)) from error
        _assert_public_projection(document, redactions=redactions, label=label)
        if document is t2_example:
            _validate_nested_benchmark_example(document, target="t2")
        elif document is t3_example:
            _validate_nested_benchmark_example(document, target="t3")
    _validate_linked_examples(t2_example, t3_example)
    return summary, t2_example, t3_example


def _project_t2(
    *,
    exemplar_id: str,
    groundtruth: Mapping[str, Any],
    oligo_map: Mapping[str, str],
    redactions: set[str],
) -> dict[str, Any]:
    oligos = []
    for item in groundtruth.get("oligos", []):
        projected: dict[str, Any] = {
            "oligo_id": oligo_map[item["oligo_id"]],
            "name": _redact_text(item["name"], redactions),
            "aliases": [_redact_text(value, redactions) for value in item["aliases"]],
            "role": _redact_text(item["role"], redactions),
            "kind": item["kind"],
            "orientation": item["orientation"],
            "components": [],
            "modifications": [
                _redact_text(value, redactions) for value in item["modifications"]
            ],
        }
        if isinstance(item.get("sequence"), str):
            projected["sequence"] = item["sequence"]
        for component in item["components"]:
            projected_component: dict[str, Any] = {
                "name": _redact_text(component["name"], redactions),
                "role": _redact_text(component["role"], redactions),
                "orientation": component["orientation"],
                "modifications": [
                    _redact_text(value, redactions)
                    for value in component["modifications"]
                ],
            }
            for key in ("sequence", "length", "placeholder"):
                value = component.get(key)
                if value is not None:
                    projected_component[key] = value
            projected["components"].append(projected_component)
        oligos.append(projected)
    return with_digest(
        {
            "schema_version": T2_EXAMPLE_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "example": {
                "protocol_id": exemplar_id,
                "oligos": oligos,
            },
        },
        "example_digest",
    )


def _project_t3(
    *,
    exemplar_id: str,
    groundtruth: Mapping[str, Any],
    oligo_map: Mapping[str, str],
    redactions: set[str],
) -> dict[str, Any]:
    workflows = []
    transition_index = 0
    for workflow_index, workflow in enumerate(groundtruth["workflows"], start=1):
        states = workflow["states"]
        state_map = _sequential_map((item["state_id"] for item in states), "state")
        strand_map = _sequential_map(
            (strand["strand_id"] for state in states for strand in state["strands"]),
            "strand",
        )
        segment_map = _sequential_map(
            (
                segment["segment_id"]
                for state in states
                for strand in state["strands"]
                for segment in strand["segments"]
            ),
            "segment",
        )
        pair_map = _sequential_map(
            (
                pair["paired_region_id"]
                for state in states
                for pair in state["paired_regions"]
            ),
            "paired-region",
        )
        discontinuity_map = _sequential_map(
            (
                item["discontinuity_id"]
                for state in states
                for item in state["discontinuities"]
            ),
            "discontinuity",
        )
        projected_states = []
        for state in states:
            projected_strands = []
            for strand in state["strands"]:
                projected_segments = []
                for segment in strand["segments"]:
                    value: dict[str, Any] = {
                        "segment_id": _mapped(
                            segment_map, segment["segment_id"], "segment"
                        ),
                        "role": _redact_text(segment["role"], redactions),
                        "structural_role": segment["structural_role"],
                    }
                    for key in ("sequence", "length", "placeholder"):
                        if key in segment:
                            value[key] = segment[key]
                    if "oligo_derivations" in segment:
                        value["oligo_derivations"] = [
                            {
                                "oligo_id": _mapped(
                                    oligo_map,
                                    derivation["oligo_id"],
                                    "T2 oligo",
                                ),
                                "orientation_to_source": derivation[
                                    "orientation_to_source"
                                ],
                            }
                            for derivation in segment["oligo_derivations"]
                        ]
                    projected_segments.append(value)
                strand_value: dict[str, Any] = {
                    "strand_id": _mapped(strand_map, strand["strand_id"], "strand"),
                    "name": _redact_text(strand["name"], redactions),
                    "molecule_type": strand["molecule_type"],
                    "orientation": strand["orientation"],
                    "segments": projected_segments,
                }
                if isinstance(strand.get("sequence_architecture"), str):
                    strand_value["sequence_architecture"] = _redact_text(
                        strand["sequence_architecture"], redactions
                    )
                projected_strands.append(strand_value)
            projected_states.append(
                {
                    "state_id": _mapped(state_map, state["state_id"], "state"),
                    "name": _redact_text(state["name"], redactions),
                    "molecule_type": _redact_text(state["molecule_type"], redactions),
                    "strand_architecture": state["strand_architecture"],
                    "reference_strand_id": _mapped(
                        strand_map, state["reference_strand_id"], "reference strand"
                    ),
                    "physical_state": _redact_text(state["physical_state"], redactions),
                    "strands": projected_strands,
                    "paired_regions": [
                        {
                            "paired_region_id": _mapped(
                                pair_map, pair["paired_region_id"], "paired region"
                            ),
                            "side_1": _project_pair_side(
                                pair["side_1"], strand_map, segment_map
                            ),
                            "side_2": _project_pair_side(
                                pair["side_2"], strand_map, segment_map
                            ),
                            "relationship": pair["relationship"],
                        }
                        for pair in state["paired_regions"]
                    ],
                    "discontinuities": [
                        {
                            "discontinuity_id": _mapped(
                                discontinuity_map,
                                item["discontinuity_id"],
                                "discontinuity",
                            ),
                            "strand_id": _mapped(
                                strand_map, item["strand_id"], "strand"
                            ),
                            "after_segment_id": _mapped(
                                segment_map,
                                item["after_segment_id"],
                                "segment",
                            ),
                            "before_segment_id": _mapped(
                                segment_map,
                                item["before_segment_id"],
                                "segment",
                            ),
                            "kind": item["kind"],
                        }
                        for item in state["discontinuities"]
                    ],
                    "properties": [
                        _redact_text(value, redactions) for value in state["properties"]
                    ],
                }
            )
        projected_transitions = []
        for transition in workflow["transitions"]:
            transition_index += 1
            transition_value: dict[str, Any] = {
                "transition_id": f"transition-{transition_index:03d}",
                "substrate_state_ids": [
                    _mapped(state_map, value, "substrate state")
                    for value in transition["substrate_state_ids"]
                ],
                "operation": transition["operation"],
                "oligo_ids": [
                    _mapped(oligo_map, value, "T2 oligo")
                    for value in transition["oligo_ids"]
                ],
                "major_reagents": [
                    {
                        "name": _redact_text(reagent["name"], redactions),
                        **(
                            {
                                "role": _redact_text(reagent["role"], redactions)
                                if isinstance(reagent.get("role"), str)
                                else None
                            }
                            if "role" in reagent
                            else {}
                        ),
                    }
                    for reagent in transition["major_reagents"]
                ],
                "product_state_ids": [
                    _mapped(state_map, value, "product state")
                    for value in transition["product_state_ids"]
                ],
                "carried_forward_product_ids": [
                    _mapped(state_map, value, "carried state")
                    for value in transition["carried_forward_product_ids"]
                ],
                "discarded_product_ids": [
                    _mapped(state_map, value, "discarded state")
                    for value in transition["discarded_product_ids"]
                ],
            }
            if "operation_detail" in transition:
                detail = transition["operation_detail"]
                transition_value["operation_detail"] = (
                    _redact_text(detail, redactions)
                    if isinstance(detail, str)
                    else None
                )
            projected_transitions.append(transition_value)
        workflows.append(
            {
                "workflow_id": f"workflow-{workflow_index:03d}",
                "states": projected_states,
                "transitions": projected_transitions,
                "initial_state_ids": [
                    _mapped(state_map, value, "initial state")
                    for value in workflow["initial_state_ids"]
                ],
                "final_outputs": [
                    {
                        "state_id": _mapped(
                            state_map, output["state_id"], "terminal state"
                        ),
                        "modality": output["modality"],
                    }
                    for output in workflow["final_outputs"]
                ],
            }
        )
    return with_digest(
        {
            "schema_version": T3_EXAMPLE_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "example": {
                "protocol_id": exemplar_id,
                "workflows": workflows,
            },
        },
        "example_digest",
    )


def _build_mechanism_summary(
    *,
    exemplar_id: str,
    t2_groundtruth: Mapping[str, Any],
    t3_groundtruth: Mapping[str, Any],
) -> dict[str, Any]:
    workflows = t3_groundtruth["workflows"]
    states = [state for workflow in workflows for state in workflow["states"]]
    transitions = [
        transition for workflow in workflows for transition in workflow["transitions"]
    ]
    finals = [output for workflow in workflows for output in workflow["final_outputs"]]
    operations = Counter(item["operation"] for item in transitions)
    architectures = Counter(item["strand_architecture"] for item in states)
    modalities = Counter(item["modality"] for item in finals)
    consumer_counts: Counter[tuple[int, str]] = Counter(
        (workflow_index, state_id)
        for workflow_index, workflow in enumerate(workflows)
        for transition in workflow["transitions"]
        for state_id in transition["substrate_state_ids"]
    )
    branch_factors = [
        len(transition["carried_forward_product_ids"])
        + len(transition["discarded_product_ids"])
        for transition in transitions
    ] + list(consumer_counts.values())
    max_branch_factor = max(branch_factors, default=0)
    has_discarded = any(item["discarded_product_ids"] for item in transitions)
    semantic_text = _mechanism_semantic_text(t2_groundtruth, t3_groundtruth)
    round_barcode = _contains_any(
        semantic_text,
        "round barcode",
        "barcode round",
        "round 1 barcode",
        "round 2 barcode",
        "round 3 barcode",
    )
    split_pool = _contains_any(
        semantic_text, "split pool", "split-pool", "split and pool"
    )
    sample_split = "sample_split" in operations
    modality_branching = len(modalities) > 1
    has_branching = max_branch_factor > 1
    payload: dict[str, Any] = {
        "schema_version": MECHANISM_SUMMARY_SCHEMA_VERSION,
        "exemplar_id": exemplar_id,
        "counts": {
            "oligo_families": len(t2_groundtruth["oligos"]),
            "workflows": len(workflows),
            "states": len(states),
            "transitions": len(transitions),
        },
        "operation_counts": [
            {"operation": key, "count": operations[key]} for key in sorted(operations)
        ],
        "architecture_counts": [
            {"strand_architecture": key, "count": architectures[key]}
            for key in sorted(architectures)
        ],
        "modality_counts": [
            {"modality": key, "count": modalities[key]} for key in sorted(modalities)
        ],
        "graph_features": {
            "has_branching": has_branching,
            "has_discarded_products": has_discarded,
            "has_multiple_workflows": len(workflows) > 1,
            "max_branch_factor": max_branch_factor,
        },
        "barcoding_partitioning": {
            "cell_barcode": _contains_any(
                semantic_text, "cell barcode", "cell_barcode"
            ),
            "umi": _contains_any(
                semantic_text, "[umi", "umi", "unique molecular identifier"
            ),
            "sample_index": _contains_any(
                semantic_text,
                "sample index",
                "sample_index",
                "i5 index",
                "i5_index",
                "i7 index",
                "i7_index",
            ),
            "round_barcode": round_barcode,
            "combinatorial": _contains_any(semantic_text, "combinatorial")
            or round_barcode
            or split_pool,
            "droplet_partitioning": _contains_any(semantic_text, "droplet", "emulsion"),
            "microwell_partitioning": _contains_any(
                semantic_text, "microwell", "micro-well"
            ),
            "plate_partitioning": _contains_any(semantic_text, "plate", "well plate"),
            "bead_partitioning": _contains_any(semantic_text, "bead"),
            "split_pool_partitioning": split_pool,
        },
        "selection_branching": {
            "affinity_selection": "affinity_selection" in operations,
            "size_selection": "size_selection" in operations,
            "capture": "capture" in operations,
            "discarded_product_branch": has_discarded,
            "sample_split": sample_split,
            "modality_branching": modality_branching,
            "alternative_branching": has_branching
            and not sample_split
            and not modality_branching,
        },
        "chemistry_flags": {
            "reverse_transcription": "reverse_transcription" in operations,
            "template_switching": _contains_any(
                semantic_text,
                "template switch",
                "template switching",
                "template-switch",
            ),
            "ligation": "ligation" in operations,
            "tagmentation": "tagmentation" in operations,
            "pcr": "pcr" in operations,
            "restriction": _contains_any(
                semantic_text, "restriction", "endonuclease", "digest"
            ),
            "conversion": _contains_any(
                semantic_text, "conversion", "bisulfite", "deamination"
            ),
        },
    }
    return with_digest(payload, "summary_digest")


def _mechanism_semantic_text(
    t2_groundtruth: Mapping[str, Any],
    t3_groundtruth: Mapping[str, Any],
) -> str:
    values: list[str] = []
    for oligo in t2_groundtruth["oligos"]:
        values.extend(_strings(oligo.get("role"), oligo.get("sequence")))
        for component in oligo["components"]:
            values.extend(
                _strings(
                    component.get("role"),
                    component.get("placeholder"),
                )
            )
    for workflow in t3_groundtruth["workflows"]:
        for state in workflow["states"]:
            values.extend(
                _strings(
                    state.get("molecule_type"),
                    state.get("strand_architecture"),
                    state.get("physical_state"),
                    *state.get("properties", []),
                )
            )
            for strand in state["strands"]:
                values.extend(_strings(strand.get("sequence_architecture")))
                for segment in strand["segments"]:
                    values.extend(
                        _strings(segment.get("role"), segment.get("placeholder"))
                    )
        for transition in workflow["transitions"]:
            values.extend(
                _strings(
                    transition.get("operation"),
                    transition.get("operation_detail"),
                    *(item.get("role") for item in transition["major_reagents"]),
                )
            )
        values.extend(item["modality"] for item in workflow["final_outputs"])
    return " ".join(values).casefold().replace("_", " ")


def _approved_groundtruth_pair(
    packet: Mapping[str, Any], protocol_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    artifacts = [
        item for item in packet["artifacts"] if item["protocol_id"] == protocol_id
    ]
    selected: dict[str, Mapping[str, Any]] = {}
    for filename in (APPROVED_T2_FILENAME, APPROVED_T3_FILENAME):
        impostors = [
            item
            for item in artifacts
            if Path(item["path"]).name == filename
            and item["role"] != "approved_groundtruth"
        ]
        if impostors:
            raise CapabilityImprovementError(
                f"{protocol_id}/{filename} appears under a non-ground-truth role"
            )
        matches = [
            item
            for item in artifacts
            if item["role"] == "approved_groundtruth"
            and Path(item["path"]).name == filename
        ]
        if len(matches) != 1 or matches[0]["visibility"] != "agent_after_reveal":
            raise CapabilityImprovementError(
                f"packet must pin exactly one revealed approved {filename} for {protocol_id}"
            )
        source = Path(matches[0]["path"]).expanduser()
        if source.is_symlink() or not source.is_file():
            raise CapabilityImprovementError(
                f"approved ground truth is not a regular non-symlink file: {source}"
            )
        if sha256_file(source) != matches[0]["sha256"]:
            raise CapabilityImprovementError(
                f"approved ground-truth hash is stale: {protocol_id}/{filename}"
            )
        selected[filename] = matches[0]
    return selected[APPROVED_T2_FILENAME], selected[APPROVED_T3_FILENAME]


def _load_groundtruth_json(
    artifact: Mapping[str, Any],
    *,
    schema_filename: str,
    label: str,
    allow_legacy_connected_process: bool = False,
) -> dict[str, Any]:
    path = Path(artifact["path"]).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
    ) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    schema_path = repository_root() / "schemas" / "groundtruth" / schema_filename
    try:
        validate_document(document, schema_path, label=label)
    except AuditArtifactError as original_error:
        if not allow_legacy_connected_process:
            raise CapabilityImprovementError(str(original_error)) from original_error
        if schema_filename != "library_generation_workflow.schema.json":
            raise CapabilityImprovementError(
                "legacy connected-process migration is valid only for approved T3"
            ) from original_error
        try:
            migrated = migrate_connected_process_t3(document)
            validate_document(migrated, schema_path, label=label)
        except (ConnectedProcessMigrationError, AuditArtifactError) as error:
            raise CapabilityImprovementError(str(original_error)) from error
        return migrated
    return document


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _write_exemplar_item(
    *,
    memory_root: Path,
    exemplar_id: str,
    mechanism_summary: Mapping[str, Any],
    t2_example: Mapping[str, Any],
    t3_example: Mapping[str, Any],
) -> dict[str, Any]:
    item_root = memory_root / "exemplars" / exemplar_id
    if item_root.exists():
        raise CapabilityImprovementError(f"exemplar already exists: {exemplar_id}")
    item_root.mkdir(parents=True)
    documents = (
        ("mechanism_summary.json", mechanism_summary),
        ("t2_example.json", t2_example),
        ("t3_example.json", t3_example),
    )
    refs: dict[str, dict[str, str]] = {}
    for filename, document in documents:
        path = item_root / filename
        write_json_atomic(path, document)
        refs[filename.removesuffix(".json")] = {
            "path": path.relative_to(memory_root).as_posix(),
            "sha256": sha256_file(path),
        }
    return with_digest(
        {
            "exemplar_id": exemplar_id,
            "mechanism_summary": refs["mechanism_summary"],
            "t2_example": refs["t2_example"],
            "t3_example": refs["t3_example"],
        },
        "exemplar_digest",
    )


def build_exemplar_memory_manifest(memory_root: Path) -> dict[str, Any]:
    root = memory_root.expanduser().resolve()
    catalog = _load_catalog(root / "catalog.json")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"symlink is forbidden in exemplar memory: {path}"
            )
        if path.is_dir():
            if path.name == "__pycache__":
                raise CapabilityImprovementError(
                    f"generated cache is forbidden in exemplar memory: {path}"
                )
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        if any(part.startswith(".") for part in Path(relative).parts):
            raise CapabilityImprovementError(
                f"hidden exemplar-memory entry is forbidden: {relative}"
            )
        mode = stat.S_IMODE(path.stat().st_mode)
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "mode": "0555" if mode & stat.S_IXUSR else "0444",
            }
        )
    expected_files = {"catalog.json", *MEMORY_RUNTIME_FILES}
    expected_files.update(
        ref["path"]
        for item in catalog["exemplars"]
        for ref in (
            item["mechanism_summary"],
            item["t2_example"],
            item["t3_example"],
        )
    )
    actual_files = {item["path"] for item in files}
    if actual_files != expected_files:
        raise CapabilityImprovementError(
            "exemplar-memory inventory is not closed; missing="
            + ",".join(sorted(expected_files - actual_files))
            + "; extra="
            + ",".join(sorted(actual_files - expected_files))
        )
    return with_digest(
        {
            "schema_version": MEMORY_MANIFEST_SCHEMA_VERSION,
            "manifest_self_excluded": True,
            "catalog_path": "catalog.json",
            "catalog_digest": catalog["catalog_digest"],
            "identity_map_commitment": catalog["identity_map_commitment"],
            "exemplar_count": catalog["exemplar_count"],
            "files": files,
        },
        "memory_digest",
    )


def write_exemplar_memory_manifest(memory_root: Path) -> Path:
    root = memory_root.expanduser().resolve()
    manifest = build_exemplar_memory_manifest(root)
    try:
        validate_document(
            manifest,
            improvement_schema_root() / "exemplar_memory_manifest.schema.json",
            label="exemplar-memory manifest",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    path = root / "manifest.json"
    write_json_atomic(path, manifest)
    return path


def validate_exemplar_memory(
    memory_root: Path,
    *,
    expected_count: int | None = None,
    identity_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    unresolved = memory_root.expanduser()
    if unresolved.is_symlink():
        raise CapabilityImprovementError(
            f"exemplar memory root may not be a symlink: {unresolved}"
        )
    root = unresolved.resolve()
    if not root.is_dir():
        raise CapabilityImprovementError(f"exemplar memory is missing: {root}")
    manifest = load_and_validate(
        root / "manifest.json",
        schema_filename="exemplar_memory_manifest.schema.json",
        digest_field="memory_digest",
        label="exemplar-memory manifest",
    )
    expected_manifest = build_exemplar_memory_manifest(root)
    if manifest != expected_manifest:
        raise CapabilityImprovementError(
            "exemplar-memory bytes do not match their manifest"
        )
    catalog = _load_catalog(root / manifest["catalog_path"])
    if (
        manifest["catalog_digest"] != catalog["catalog_digest"]
        or manifest["identity_map_commitment"] != catalog["identity_map_commitment"]
        or manifest["exemplar_count"] != catalog["exemplar_count"]
    ):
        raise CapabilityImprovementError(
            "exemplar-memory manifest and catalog disagree"
        )
    if expected_count is not None and catalog["exemplar_count"] != expected_count:
        raise CapabilityImprovementError(
            "exemplar-memory count differs from checkpoint protocol count"
        )
    if catalog["exemplar_count"] != len(catalog["exemplars"]):
        raise CapabilityImprovementError(
            "exemplar-memory catalog count differs from its item count"
        )
    ids = [item["exemplar_id"] for item in catalog["exemplars"]]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise CapabilityImprovementError(
            "exemplar-memory catalog IDs must be unique and sorted"
        )
    if identity_map is not None:
        if catalog["identity_map_commitment"] != identity_map["identity_map_digest"]:
            raise CapabilityImprovementError(
                "exemplar memory uses another private identity map"
            )
        mapping = {
            item["protocol_id"]: item["exemplar_id"] for item in identity_map["entries"]
        }
        prefix_count = catalog["exemplar_count"]
        expected_ids = {
            mapping[protocol_id]
            for protocol_id in training_protocol_ids()[:prefix_count]
        }
        if set(ids) != expected_ids:
            raise CapabilityImprovementError(
                "exemplar memory differs from the exact cumulative training prefix"
            )
    for item in catalog["exemplars"]:
        validate_digest(item, "exemplar_digest")
        exemplar_id = item["exemplar_id"]
        expected_item_paths = {
            "mechanism_summary": f"exemplars/{exemplar_id}/mechanism_summary.json",
            "t2_example": f"exemplars/{exemplar_id}/t2_example.json",
            "t3_example": f"exemplars/{exemplar_id}/t3_example.json",
        }
        loaded_files: dict[str, dict[str, Any]] = {}
        for key, schema_filename, digest_field in (
            (
                "mechanism_summary",
                "exemplar_mechanism_summary.schema.json",
                "summary_digest",
            ),
            ("t2_example", "t2_example.schema.json", "example_digest"),
            ("t3_example", "t3_example.schema.json", "example_digest"),
        ):
            ref = item[key]
            if ref["path"] != expected_item_paths[key]:
                raise CapabilityImprovementError(
                    f"exemplar catalog has a noncanonical {key} path"
                )
            relative = safe_relative_path(ref["path"])
            path = root / relative
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(root)
            ):
                raise CapabilityImprovementError(
                    f"exemplar catalog path escapes memory: {ref['path']}"
                )
            if sha256_file(path) != ref["sha256"]:
                raise CapabilityImprovementError(
                    f"exemplar catalog has a stale {key} hash"
                )
            document = _load_public_document(
                path,
                schema_filename=schema_filename,
                digest_field=digest_field,
                label=f"{key} for {exemplar_id}",
            )
            if document["exemplar_id"] != exemplar_id:
                raise CapabilityImprovementError(
                    f"exemplar file belongs to another pseudonym: {ref['path']}"
                )
            loaded_files[key] = document
            if key == "t2_example":
                _validate_nested_benchmark_example(document, target="t2")
            elif key == "t3_example":
                _validate_nested_benchmark_example(document, target="t3")
            _assert_public_projection(
                document,
                redactions=_public_identity_literals(),
                label=f"{key} for {exemplar_id}",
            )
        _validate_linked_examples(
            loaded_files["t2_example"], loaded_files["t3_example"]
        )
        _validate_summary_against_examples(
            loaded_files["mechanism_summary"],
            loaded_files["t2_example"],
            loaded_files["t3_example"],
        )
    return manifest


def exemplar_memory_record(memory_root: Path) -> dict[str, Any]:
    manifest = validate_exemplar_memory(memory_root)
    return {
        "path": "memory",
        "manifest_path": "memory/manifest.json",
        "catalog_path": "memory/catalog.json",
        "exemplar_count": manifest["exemplar_count"],
        "memory_digest": manifest["memory_digest"],
        "memory_manifest_sha256": sha256_file(
            memory_root.expanduser().resolve() / "manifest.json"
        ),
        "catalog_digest": manifest["catalog_digest"],
        "catalog_sha256": sha256_file(
            memory_root.expanduser().resolve() / "catalog.json"
        ),
        "identity_map_commitment": manifest["identity_map_commitment"],
    }


def _load_catalog(path: Path) -> dict[str, Any]:
    document = _load_public_document(
        path,
        schema_filename="exemplar_memory_catalog.schema.json",
        digest_field="catalog_digest",
        label="exemplar-memory catalog",
    )
    _validate_catalog_document(document)
    return document


def _validate_catalog_document(document: Mapping[str, Any]) -> None:
    try:
        validate_document(
            dict(document),
            improvement_schema_root() / "exemplar_memory_catalog.schema.json",
            label="exemplar-memory catalog",
        )
        validate_digest(document, "catalog_digest")
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _load_public_document(
    path: Path,
    *,
    schema_filename: str,
    digest_field: str,
    label: str,
) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
    ) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    try:
        validate_document(
            document,
            improvement_schema_root() / schema_filename,
            label=label,
        )
        validate_digest(document, digest_field)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    return document


def _validate_nested_benchmark_example(
    document: Mapping[str, Any],
    *,
    target: str,
) -> None:
    example = document.get("example")
    if not isinstance(example, dict) or example.get("protocol_id") != document.get(
        "exemplar_id"
    ):
        raise CapabilityImprovementError(
            f"{target.upper()} exemplar does not bind its pseudonymous protocol ID"
        )
    schema_filename = (
        "oligo_prediction.schema.json"
        if target == "t2"
        else "library_generation_workflow_prediction.schema.json"
    )
    try:
        validate_document(
            example,
            repository_root() / "schemas" / "benchmark" / schema_filename,
            label=f"nested benchmark-shaped {target.upper()} exemplar",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _validate_linked_examples(
    t2_document: Mapping[str, Any],
    t3_document: Mapping[str, Any],
) -> None:
    t2 = t2_document["example"]
    t3 = t3_document["example"]
    if (
        t2_document["exemplar_id"] != t3_document["exemplar_id"]
        or t2["protocol_id"] != t3["protocol_id"]
    ):
        raise CapabilityImprovementError("linked T2/T3 exemplars use different IDs")
    oligo_ids = [item["oligo_id"] for item in t2["oligos"]]
    if len(oligo_ids) != len(set(oligo_ids)):
        raise CapabilityImprovementError("T2 exemplar contains duplicate oligo IDs")
    known_oligos = set(oligo_ids)
    workflow_ids = [item["workflow_id"] for item in t3["workflows"]]
    if len(workflow_ids) != len(set(workflow_ids)):
        raise CapabilityImprovementError("T3 exemplar contains duplicate workflow IDs")
    all_transition_ids: set[str] = set()
    for workflow in t3["workflows"]:
        state_ids = [item["state_id"] for item in workflow["states"]]
        known_states = set(state_ids)
        if len(state_ids) != len(known_states):
            raise CapabilityImprovementError(
                "T3 exemplar workflow contains duplicate state IDs"
            )
        if not set(workflow["initial_state_ids"]) <= known_states:
            raise CapabilityImprovementError(
                "T3 exemplar initial state reference does not resolve"
            )
        if not {item["state_id"] for item in workflow["final_outputs"]} <= known_states:
            raise CapabilityImprovementError(
                "T3 exemplar terminal state reference does not resolve"
            )
        adjacency: dict[str, set[str]] = defaultdict(set)
        for transition in workflow["transitions"]:
            transition_id = transition["transition_id"]
            if transition_id in all_transition_ids:
                raise CapabilityImprovementError(
                    "T3 exemplar contains duplicate transition IDs"
                )
            all_transition_ids.add(transition_id)
            substrates = set(transition["substrate_state_ids"])
            products = set(transition["product_state_ids"])
            carried = set(transition["carried_forward_product_ids"])
            discarded = set(transition["discarded_product_ids"])
            if (
                not substrates
                or not products
                or not (substrates | products) <= known_states
            ):
                raise CapabilityImprovementError(
                    "T3 exemplar transition state reference does not resolve"
                )
            if carried & discarded or carried | discarded != products:
                raise CapabilityImprovementError(
                    "T3 exemplar transition product disposition is not an exact partition"
                )
            if not set(transition["oligo_ids"]) <= known_oligos:
                raise CapabilityImprovementError(
                    "T3 exemplar transition references an unknown T2 oligo"
                )
            for left in substrates:
                for right in products:
                    adjacency[left].add(right)
                    adjacency[right].add(left)
        for state in workflow["states"]:
            strands = {item["strand_id"]: item for item in state["strands"]}
            if len(strands) != len(state["strands"]):
                raise CapabilityImprovementError(
                    "T3 exemplar state contains duplicate strand IDs"
                )
            if state["reference_strand_id"] not in strands:
                raise CapabilityImprovementError(
                    "T3 exemplar reference strand does not resolve"
                )
            segments = {
                item["segment_id"]: (strand_id, item)
                for strand_id, strand in strands.items()
                for item in strand["segments"]
            }
            if len(segments) != sum(
                len(strand["segments"]) for strand in strands.values()
            ):
                raise CapabilityImprovementError(
                    "T3 exemplar state contains duplicate segment IDs"
                )
            for _, segment in segments.values():
                if (
                    not {
                        item["oligo_id"]
                        for item in segment.get("oligo_derivations", [])
                    }
                    <= known_oligos
                ):
                    raise CapabilityImprovementError(
                        "T3 exemplar segment references an unknown T2 oligo"
                    )
            for paired in state["paired_regions"]:
                for side in (paired["side_1"], paired["side_2"]):
                    strand = strands.get(side["strand_id"])
                    if strand is None:
                        raise CapabilityImprovementError(
                            "T3 exemplar paired-region strand does not resolve"
                        )
                    strand_segments = {
                        item["segment_id"] for item in strand["segments"]
                    }
                    if not set(side["segment_ids"]) <= strand_segments:
                        raise CapabilityImprovementError(
                            "T3 exemplar paired-region segment does not resolve"
                        )
            for discontinuity in state["discontinuities"]:
                strand = strands.get(discontinuity["strand_id"])
                if strand is None:
                    raise CapabilityImprovementError(
                        "T3 exemplar discontinuity strand does not resolve"
                    )
                strand_segments = {item["segment_id"] for item in strand["segments"]}
                if (
                    not {
                        discontinuity["after_segment_id"],
                        discontinuity["before_segment_id"],
                    }
                    <= strand_segments
                ):
                    raise CapabilityImprovementError(
                        "T3 exemplar discontinuity segment does not resolve"
                    )
        if known_states:
            visited: set[str] = set()
            pending = [next(iter(known_states))]
            while pending:
                state_id = pending.pop()
                if state_id in visited:
                    continue
                visited.add(state_id)
                pending.extend(adjacency[state_id] - visited)
            if visited != known_states:
                raise CapabilityImprovementError(
                    "T3 exemplar workflow is not weakly connected"
                )


def _validate_summary_against_examples(
    summary: Mapping[str, Any],
    t2_document: Mapping[str, Any],
    t3_document: Mapping[str, Any],
) -> None:
    t2 = t2_document["example"]
    t3 = t3_document["example"]
    workflows = t3["workflows"]
    states = [state for workflow in workflows for state in workflow["states"]]
    transitions = [
        transition for workflow in workflows for transition in workflow["transitions"]
    ]
    expected_counts = {
        "oligo_families": len(t2["oligos"]),
        "workflows": len(workflows),
        "states": len(states),
        "transitions": len(transitions),
    }
    if summary["counts"] != expected_counts:
        raise CapabilityImprovementError(
            "mechanism-summary counts differ from linked T2/T3 exemplars"
        )
    expected_counters = (
        (
            "operation_counts",
            "operation",
            Counter(item["operation"] for item in transitions),
        ),
        (
            "architecture_counts",
            "strand_architecture",
            Counter(item["strand_architecture"] for item in states),
        ),
        (
            "modality_counts",
            "modality",
            Counter(
                output["modality"]
                for workflow in workflows
                for output in workflow["final_outputs"]
            ),
        ),
    )
    for collection, key, expected in expected_counters:
        observed = Counter({item[key]: item["count"] for item in summary[collection]})
        if observed != expected:
            raise CapabilityImprovementError(
                f"mechanism-summary {collection} differs from T3 exemplar"
            )


def _copy_runtime_seed(*, runtime_source: Path, memory_root: Path) -> None:
    source = runtime_source.expanduser().resolve()
    expected_source_files = {
        relative.removeprefix("runtime/") for relative in MEMORY_RUNTIME_FILES
    }
    if source.is_symlink() or not source.is_dir():
        raise CapabilityImprovementError(
            f"exemplar-memory runtime seed is missing: {source}"
        )
    actual_source_files = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(source).parts
        and path.suffix != ".pyc"
    }
    if actual_source_files != expected_source_files:
        raise CapabilityImprovementError(
            "exemplar-memory runtime seed inventory differs from the frozen contract"
        )
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"symlink is forbidden in exemplar-memory runtime seed: {path}"
            )
        if (
            not path.is_file()
            or "__pycache__" in path.relative_to(source).parts
            or path.suffix == ".pyc"
        ):
            continue
        relative = path.relative_to(source)
        target = memory_root / "runtime" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        target.chmod(0o555 if target.suffix == ".py" else 0o444)


def _assert_public_projection(
    value: Any,
    *,
    redactions: Iterable[str],
    label: str,
) -> None:
    identity_pattern = _identity_phrase_union(
        tuple(sorted({item for item in redactions if item}))
    )
    for text in _walk_strings(value):
        if identity_pattern is not None and identity_pattern.search(text):
            raise CapabilityImprovementError(
                f"{label} contains a private protocol identity"
            )
        if PRIVATE_PATH_RE.search(text) or URL_RE.search(text):
            raise CapabilityImprovementError(
                f"{label} contains a private path or provenance URL"
            )


def _public_identity_literals() -> set[str]:
    """Return only canonical protocol identities and source-artifact names."""

    values = {
        APPROVED_T2_FILENAME,
        APPROVED_T3_FILENAME,
        "groundtruth_final_lib_struct.json",
    }
    for protocol_id in (
        *training_protocol_ids(),
        *FIXED_VALIDATION_PANEL,
        *FINAL_TRANSFER_PANEL,
    ):
        values.update(
            {
                protocol_id,
                protocol_id.replace("_", " "),
                protocol_id.replace("_", "-"),
            }
        )
    return values


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk_strings(item)


def _redact_text(value: str, redactions: Iterable[str]) -> str:
    result = value
    identity_pattern = _identity_phrase_union(
        tuple(sorted({item for item in redactions if item}))
    )
    if identity_pattern is not None:
        result = identity_pattern.sub(
            "[REDACTED_PROTOCOL]",
            result,
        )
    result = PRIVATE_PATH_RE.sub("[REDACTED_PATH]", result)
    result = URL_RE.sub("[REDACTED_URL]", result)
    return result


@lru_cache(maxsize=64)
def _identity_phrase_union(
    values: tuple[str, ...],
) -> re.Pattern[str] | None:
    """Compile one cached complete-phrase matcher for an identity set."""

    alternatives = sorted(
        {value for value in values if value},
        key=lambda value: (-len(value), value.casefold(), value),
    )
    if not alternatives:
        return None
    escaped = "|".join(re.escape(value) for value in alternatives)
    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{escaped})(?![A-Za-z0-9])",
        flags=re.I,
    )


def _sequential_map(values: Iterable[str], prefix: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if value not in result:
            result[value] = f"{prefix}-{len(result) + 1:03d}"
    return result


def _mapped(mapping: Mapping[str, str], value: str, label: str) -> str:
    try:
        return mapping[value]
    except KeyError as error:
        raise CapabilityImprovementError(
            f"approved ground truth has an unresolved {label} reference"
        ) from error


def _project_pair_side(
    side: Mapping[str, Any],
    strand_map: Mapping[str, str],
    segment_map: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "strand_id": _mapped(strand_map, side["strand_id"], "paired strand"),
        "segment_ids": [
            _mapped(segment_map, value, "paired segment")
            for value in side["segment_ids"]
        ],
    }


def _strings(*values: Any) -> list[str]:
    return [value for value in values if isinstance(value, str)]


def _contains_any(value: str, *needles: str) -> bool:
    for needle in needles:
        normalized = needle.casefold().replace("_", " ")
        if normalized.startswith("["):
            if normalized in value:
                return True
            continue
        if re.search(
            rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
            value,
        ):
            return True
    return False
