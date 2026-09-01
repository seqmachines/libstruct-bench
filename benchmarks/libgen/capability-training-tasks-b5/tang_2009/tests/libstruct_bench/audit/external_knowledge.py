from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from libstruct_bench.libgen.prediction_validation import (
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)
from libstruct_bench.libgen.scoring import _canonical_oligo_signature


PRECEDENCE_RULE = "Target-protocol evidence overrides all external knowledge."
MEMORY_WARNING = (
    "Donor records are worked examples from other protocols, not evidence for the "
    "target. Do not copy a donor sequence, state, operation, or topology unless the "
    "target protocol’s own sources support it. Target-specific evidence is authoritative."
)

DONOR_PROTOCOL_IDS = (
    "s3_atac",
    "10x_chromium_3_gene_expression_v4",
    "drop_seq",
    "split_seq",
    "sci_rna_seq",
)
TARGET_PROTOCOL_IDS = (
    "sci_atac_seq",
    "scrrbs",
    "smart_seq",
    "share_seq",
    "ddseq_single_cell_3_rna_seq_kit",
)

T2_GROUNDTRUTH_FILENAME = "groundtruth_oligos.json"
T3_GROUNDTRUTH_FILENAME = "groundtruth_library_generation_workflow.json"
T2_PREDICTION_FILENAME = "t2_prediction.json"
T3_PREDICTION_FILENAME = "t3_prediction.json"

SOURCE_FILES = (
    (
        "lodish_mcb_5e",
        "Lodish H. Molecular Cell Biology (5ed, Freeman, 2003)(ISBN 0716743663)(C)(967s).pdf",
        "foundation",
    ),
    ("pcr_protocols", "PCRProtocols.pdf", "foundation"),
    ("protocols_for_gene_analysis", "ProtocolsForGeneAnalysis.pdf", "foundation"),
    ("nrg_2015_16", "nrg.2015.16.pdf", "review"),
    ("nrg_2017_15", "nrg.2017.15.pdf", "review"),
    ("nrg3980", "nrg3980.pdf", "review"),
    ("s41576_019_0150_2", "s41576-019-0150-2.pdf", "focused"),
    ("s41576_023_00580_2", "s41576-023-00580-2.pdf", "focused"),
    ("s43586_020_00008_9", "s43586-020-00008-9.pdf", "focused"),
    ("gks1128", "gks1128.pdf", "focused"),
    ("s43586_022_00157_z", "s43586-022-00157-z.pdf", "focused"),
)

EVIDENCE_COLUMNS = (
    "evidence_id",
    "card_id",
    "operation",
    "source_id",
    "source_file",
    "source_sha256",
    "coverage_status",
    "pdf_page_1based",
    "printed_page",
    "section",
    "table",
    "figure",
    "support_summary",
)

PRIMER_SECTION_HEADINGS = (
    "## 1. PCR, primer extension, and primer-tail incorporation",
    "## 2. Restriction digestion, fragmentation, end repair, and adaptor addition",
    "## 3. Ligation, hybridization, splinting, nicks, gaps, and terminal chemistry",
    "## 4. Reverse transcription and template switching",
    "## 5. Tn5 tagmentation and subsequent repair",
    "## 6. Split-pool and combinatorial indexing",
    "## 7. Size selection, affinity selection, capture, and sample splitting",
    "## 8. Bisulfite conversion and adapter/oligo modifications",
    "## 9. Sequencing-ready library architecture",
)

PRIMER_CARD_FIELDS = (
    "Molecular inputs",
    "Physical or covalent event",
    "Immediate molecular product",
    "Strand and topology consequences",
    "Incorporated versus transient components",
    "Generic variants and limits",
    "Exact source locator",
)

PRIMER_REVISION_CHANGED_PATHS = frozenset(
    {
        "knowledge/general_molecular_methods_v1.md",
        "knowledge/general_molecular_methods_v1_evidence.tsv",
        "knowledge/general_molecular_methods_v1_manifest.json",
        "conditions/general_methods_v1/manifest.json",
        "conditions/general_methods_plus_memory_v1/manifest.json",
    }
)

_ARCHITECTURE_PART_RE = re.compile(r"(\[[^\]]+\]|[ACGTURYSWKMBDHVN]+)", re.I)
_IUPAC_COMPLEMENT = str.maketrans(
    "ACGTRYSWKMBDHVN",
    "TGCAYRSWMKVHDBN",
)


class ExternalKnowledgeBuildError(ValueError):
    """Raised when a frozen external-knowledge asset cannot be built safely."""


@dataclass(frozen=True)
class ProjectionResult:
    document: dict[str, Any]
    removed_json_pointers: tuple[str, ...]
    retained_leaf_count: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def project_document_to_schema(
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> ProjectionResult:
    """Allowlist a ground-truth document through an agent prediction schema."""

    removed: list[str] = []
    retained_leaf_count = 0

    def resolve(node_schema: Mapping[str, Any]) -> Mapping[str, Any]:
        reference = node_schema.get("$ref")
        if not isinstance(reference, str):
            return node_schema
        if not reference.startswith("#/"):
            raise ExternalKnowledgeBuildError(
                f"unsupported non-local schema reference: {reference}"
            )
        current: Any = schema
        for token in reference[2:].split("/"):
            current = current[token.replace("~1", "/").replace("~0", "~")]
        if not isinstance(current, Mapping):
            raise ExternalKnowledgeBuildError(
                f"schema reference does not resolve to an object: {reference}"
            )
        return current

    def pointer(path: Sequence[str]) -> str:
        return "/" + "/".join(
            token.replace("~", "~0").replace("/", "~1") for token in path
        )

    def project(value: Any, node_schema: Mapping[str, Any], path: tuple[str, ...]) -> Any:
        nonlocal retained_leaf_count
        node_schema = resolve(node_schema)
        properties = node_schema.get("properties")
        if isinstance(value, Mapping) and isinstance(properties, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                child_path = (*path, str(key))
                child_schema = properties.get(key)
                if not isinstance(child_schema, Mapping):
                    removed.append(pointer(child_path))
                    continue
                result[str(key)] = project(child, child_schema, child_path)
            return result
        if isinstance(value, list):
            item_schema = node_schema.get("items")
            if not isinstance(item_schema, Mapping):
                retained_leaf_count += len(value)
                return copy.deepcopy(value)
            return [
                project(child, item_schema, (*path, str(index)))
                for index, child in enumerate(value)
            ]
        retained_leaf_count += 1
        return copy.deepcopy(value)

    projected = project(document, schema, ())
    if not isinstance(projected, dict):
        raise ExternalKnowledgeBuildError("prediction projection must produce an object")
    return ProjectionResult(
        document=projected,
        removed_json_pointers=tuple(sorted(removed)),
        retained_leaf_count=retained_leaf_count,
    )


def validate_evidence_tsv(
    evidence_path: Path,
    *,
    source_root: Path,
) -> list[dict[str, str]]:
    source_specs = {source_id: filename for source_id, filename, _ in SOURCE_FILES}
    source_hashes = {
        source_id: sha256_file(source_root / filename)
        for source_id, filename in source_specs.items()
    }
    with evidence_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != EVIDENCE_COLUMNS:
            raise ExternalKnowledgeBuildError(
                "evidence TSV columns do not match the frozen evidence contract"
            )
        rows = [dict(row) for row in reader]
    if not rows:
        raise ExternalKnowledgeBuildError("evidence TSV is empty")

    seen_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        evidence_id = row["evidence_id"]
        if not evidence_id or evidence_id in seen_ids:
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} has a missing or duplicate evidence_id"
            )
        seen_ids.add(evidence_id)
        source_id = row["source_id"]
        if source_id not in source_specs:
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} uses unknown source_id {source_id!r}"
            )
        if row["source_file"] != source_specs[source_id]:
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} source filename does not match {source_id}"
            )
        if row["source_sha256"] != source_hashes[source_id]:
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} source hash is stale for {source_id}"
            )
        if row["coverage_status"] not in {"direct", "partial", "missing"}:
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} has invalid coverage_status"
            )
        page = row["pdf_page_1based"]
        if page and (not page.isdigit() or int(page) < 1):
            raise ExternalKnowledgeBuildError(
                f"evidence TSV row {row_number} has invalid PDF page"
            )
        if row["coverage_status"] == "missing" and page:
            raise ExternalKnowledgeBuildError(
                f"missing-coverage row {row_number} must not claim a PDF page"
            )
        if row["coverage_status"] != "missing" and not page:
            raise ExternalKnowledgeBuildError(
                f"supported row {row_number} must identify a PDF page"
            )
    covered_sources = {row["source_id"] for row in rows}
    missing_sources = set(source_specs) - covered_sources
    if missing_sources:
        raise ExternalKnowledgeBuildError(
            "evidence TSV omits supplied sources: " + ", ".join(sorted(missing_sources))
        )
    return rows


def validate_primer_markdown(
    primer_text: str,
    *,
    evidence_rows: Sequence[Mapping[str, str]],
) -> None:
    if "# Source coverage" not in primer_text:
        raise ExternalKnowledgeBuildError(
            "primer Markdown must begin with a source-coverage section"
        )
    positions = [primer_text.find(heading) for heading in PRIMER_SECTION_HEADINGS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ExternalKnowledgeBuildError(
            "primer Markdown is missing or reorders a required molecular-operation section"
        )
    card_matches = list(
        re.finditer(r"(?m)^### ([A-Z][A-Z0-9-]+)\s+[—–-]\s+.+$", primer_text)
    )
    if not card_matches:
        raise ExternalKnowledgeBuildError("primer Markdown contains no operation cards")
    card_ids: set[str] = set()
    expected_labels = {f"- **{field}:**" for field in PRIMER_CARD_FIELDS}
    for index, match in enumerate(card_matches):
        card_id = match.group(1)
        if card_id in card_ids:
            raise ExternalKnowledgeBuildError(f"duplicate primer card ID: {card_id}")
        card_ids.add(card_id)
        end = (
            card_matches[index + 1].start()
            if index + 1 < len(card_matches)
            else len(primer_text)
        )
        block = primer_text[match.end() : end]
        labels = {
            line.split("**", 2)[1].removesuffix(":")
            for line in block.splitlines()
            if line.startswith("- **") and ":**" in line
        }
        expected_names = set(PRIMER_CARD_FIELDS)
        if labels != expected_names:
            missing = expected_names - labels
            extra = labels - expected_names
            raise ExternalKnowledgeBuildError(
                f"primer card {card_id} fields mismatch; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        for label in expected_labels:
            if block.count(label) != 1:
                raise ExternalKnowledgeBuildError(
                    f"primer card {card_id} must contain {label!r} exactly once"
                )
    evidence_card_ids = {row["card_id"] for row in evidence_rows}
    if card_ids != evidence_card_ids:
        raise ExternalKnowledgeBuildError(
            "primer/evidence card IDs differ; "
            f"primer_only={sorted(card_ids - evidence_card_ids)}, "
            f"evidence_only={sorted(evidence_card_ids - card_ids)}"
        )
    prohibited = {
        *TARGET_PROTOCOL_IDS,
        "sci-atac-seq",
        "scrrbs",
        "smart-seq",
        "share-seq",
        "ddseq",
    }
    lowered = primer_text.lower()
    present = sorted(value for value in prohibited if value.lower() in lowered)
    if present:
        raise ExternalKnowledgeBuildError(
            "primer contains prohibited target-protocol examples: " + ", ".join(present)
        )


def build_external_knowledge_assets(
    *,
    asset_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    audit_root: Path,
    schema_root: Path,
    primer_markdown: Path,
    primer_evidence_tsv: Path,
    created_at: str,
) -> dict[str, Any]:
    if asset_root.exists() and any(asset_root.iterdir()):
        raise ExternalKnowledgeBuildError(
            f"refusing to overwrite non-empty frozen asset root: {asset_root}"
        )
    for _, filename, _ in SOURCE_FILES:
        path = source_root / filename
        if not path.is_file():
            raise ExternalKnowledgeBuildError(f"missing primer source: {path}")
    for protocol_id in (*DONOR_PROTOCOL_IDS, *TARGET_PROTOCOL_IDS):
        for filename in (T2_GROUNDTRUTH_FILENAME, T3_GROUNDTRUTH_FILENAME):
            path = groundtruth_root / protocol_id / filename
            if not path.is_file():
                raise ExternalKnowledgeBuildError(f"missing ground truth: {path}")

    primer_text = primer_markdown.read_text(encoding="utf-8")
    evidence_rows = validate_evidence_tsv(
        primer_evidence_tsv,
        source_root=source_root,
    )
    validate_primer_markdown(primer_text, evidence_rows=evidence_rows)

    knowledge_root = asset_root / "knowledge"
    memory_root = asset_root / "memory" / "cross_protocol_memory_v1"
    condition_root = asset_root / "conditions"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    memory_root.mkdir(parents=True, exist_ok=True)
    condition_root.mkdir(parents=True, exist_ok=True)

    primer_output = knowledge_root / "general_molecular_methods_v1.md"
    evidence_output = knowledge_root / "general_molecular_methods_v1_evidence.tsv"
    shutil.copyfile(primer_markdown, primer_output)
    shutil.copyfile(primer_evidence_tsv, evidence_output)

    t2_schema_path = schema_root / "benchmark" / "oligo_prediction.schema.json"
    t3_schema_path = (
        schema_root
        / "benchmark"
        / "library_generation_workflow_prediction.schema.json"
    )
    t2_schema = load_json(t2_schema_path)
    t3_schema = load_json(t3_schema_path)

    lineage = _load_donor_lineage(audit_root=audit_root)
    validation_donors: list[dict[str, Any]] = []
    donor_documents: dict[str, dict[str, Any]] = {}
    donor_files: list[dict[str, Any]] = []
    for protocol_id in DONOR_PROTOCOL_IDS:
        source_t2_path = groundtruth_root / protocol_id / T2_GROUNDTRUTH_FILENAME
        source_t3_path = groundtruth_root / protocol_id / T3_GROUNDTRUTH_FILENAME
        source_t2 = load_json(source_t2_path)
        source_t3 = load_json(source_t3_path)
        source_t2_hash = sha256_file(source_t2_path)
        source_t3_hash = sha256_file(source_t3_path)
        donor_lineage = lineage[protocol_id]
        if source_t2_hash != donor_lineage["promoted_t2_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"current donor T2 is not the promoted record: {protocol_id}"
            )
        if source_t2_hash != donor_lineage["approved_current_t2_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"current donor T2 does not match the hash-pinned migration approval: "
                f"{protocol_id}"
            )
        if source_t3_hash != donor_lineage["approved_current_t3_sha256"]:
            raise ExternalKnowledgeBuildError(
                f"current donor T3 does not match the hash-pinned migration approval: "
                f"{protocol_id}"
            )
        projected_t2 = project_document_to_schema(source_t2, t2_schema)
        projected_t3 = project_document_to_schema(source_t3, t3_schema)
        validate_t2_prediction(projected_t2.document, protocol_id=protocol_id)
        validate_t3_prediction(projected_t3.document, protocol_id=protocol_id)
        validate_prediction_links(projected_t2.document, projected_t3.document)

        donor_root = memory_root / "donors" / protocol_id
        t2_output = donor_root / T2_PREDICTION_FILENAME
        t3_output = donor_root / T3_PREDICTION_FILENAME
        write_json(t2_output, projected_t2.document)
        write_json(t3_output, projected_t3.document)
        donor_documents[protocol_id] = {
            "t2": projected_t2.document,
            "t3": projected_t3.document,
        }

        relative_t2 = t2_output.relative_to(asset_root).as_posix()
        relative_t3 = t3_output.relative_to(asset_root).as_posix()
        donor_files.extend(
            [
                _file_entry(asset_root, relative_t2, visibility="agent"),
                _file_entry(asset_root, relative_t3, visibility="agent"),
            ]
        )
        t2_validation = _projection_validation_entry(
            source_path=source_t2_path,
            output_path=t2_output,
            source_document=source_t2,
            result=projected_t2,
            record_count=len(projected_t2.document["oligos"]),
        )
        t3_validation = _projection_validation_entry(
            source_path=source_t3_path,
            output_path=t3_output,
            source_document=source_t3,
            result=projected_t3,
            record_count=sum(
                len(workflow["states"]) + len(workflow["transitions"])
                for workflow in projected_t3.document["workflows"]
            ),
        )
        if any(
            entry["retained_value_fidelity"] != "pass"
            for entry in (t2_validation, t3_validation)
        ):
            raise ExternalKnowledgeBuildError(
                f"projection changed an allowlisted donor value: {protocol_id}"
            )
        validation_donors.append(
            {
                "protocol_id": protocol_id,
                "lineage": donor_lineage,
                "t2": t2_validation,
                "t3": t3_validation,
                "linked_prediction_validation": "pass",
            }
        )

    index_path = memory_root / "index.md"
    index_path.write_text(_memory_index(), encoding="utf-8")
    index_entry = _file_entry(
        asset_root,
        index_path.relative_to(asset_root).as_posix(),
        visibility="agent",
    )

    validation_report_path = memory_root / "projection_validation_report.json"
    validation_report = {
        "report_id": "cross_protocol_memory_v1:projection_validation",
        "status": "pass",
        "created_at": created_at,
        "projection_policy": (
            "Recursive allowlist projection through the current agent-facing T2/T3 "
            "prediction schemas; values and array order are preserved."
        ),
        "schemas": [
            {
                "path": t2_schema_path.relative_to(schema_root.parent).as_posix(),
                "sha256": sha256_file(t2_schema_path),
            },
            {
                "path": t3_schema_path.relative_to(schema_root.parent).as_posix(),
                "sha256": sha256_file(t3_schema_path),
            },
        ],
        "donors": validation_donors,
    }
    write_json(validation_report_path, validation_report)

    overlap_path = memory_root / "donor_target_overlap.tsv"
    overlap_rows = build_overlap_rows(
        donor_documents=donor_documents,
        target_protocol_ids=TARGET_PROTOCOL_IDS,
        groundtruth_root=groundtruth_root,
    )
    _write_overlap_tsv(overlap_path, overlap_rows)

    general_manifest_path = knowledge_root / "general_molecular_methods_v1_manifest.json"
    source_entries = [
        {
            "source_id": source_id,
            "filename": filename,
            "source_class": source_class,
            "sha256": sha256_file(source_root / filename),
        }
        for source_id, filename, source_class in SOURCE_FILES
    ]
    general_payload: dict[str, Any] = {
        "manifest_id": "general_molecular_methods_v1",
        "asset_type": "general_molecular_method_primer",
        "status": "draft_for_human_review",
        "created_at": created_at,
        "precedence_rule": PRECEDENCE_RULE,
        "source_protocol_ids": [],
        "source_files": source_entries,
        "files": [
            _file_entry(
                asset_root,
                primer_output.relative_to(asset_root).as_posix(),
                visibility="agent",
            ),
            _file_entry(
                asset_root,
                evidence_output.relative_to(asset_root).as_posix(),
                visibility="agent",
            ),
        ],
        "evidence_row_count": len(evidence_rows),
    }
    general_manifest = _with_digest(general_payload, "asset_digest")
    write_json(general_manifest_path, general_manifest)

    validation_entry = _file_entry(
        asset_root,
        validation_report_path.relative_to(asset_root).as_posix(),
        visibility="review_only",
    )
    overlap_entry = _file_entry(
        asset_root,
        overlap_path.relative_to(asset_root).as_posix(),
        visibility="review_only",
    )
    memory_manifest_path = memory_root / "manifest.json"
    memory_payload: dict[str, Any] = {
        "manifest_id": "cross_protocol_memory_v1",
        "asset_type": "cross_protocol_memory",
        "status": "draft_for_human_review",
        "created_at": created_at,
        "precedence_rule": PRECEDENCE_RULE,
        "donor_protocol_ids": list(DONOR_PROTOCOL_IDS),
        "target_protocol_ids": list(TARGET_PROTOCOL_IDS),
        "same_donor_pack_for_every_target": True,
        "files": [index_entry, *donor_files, validation_entry, overlap_entry],
        "review_only_files_must_not_be_exposed_to_agent": True,
    }
    memory_manifest = _with_digest(memory_payload, "asset_digest")
    write_json(memory_manifest_path, memory_manifest)

    general_agent_files = [
        *general_manifest["files"],
    ]
    memory_agent_files = [index_entry, *donor_files]
    condition_manifests = _write_condition_manifests(
        asset_root=asset_root,
        condition_root=condition_root,
        created_at=created_at,
        general_agent_files=general_agent_files,
        memory_agent_files=memory_agent_files,
        source_entries=source_entries,
    )

    audit_result = validate_external_knowledge_assets(asset_root)
    return {
        "asset_root": str(asset_root),
        "general_manifest": general_manifest,
        "memory_manifest": memory_manifest,
        "condition_manifests": condition_manifests,
        "validation": audit_result,
    }


def build_overlap_rows(
    *,
    donor_documents: Mapping[str, Mapping[str, Any]],
    target_protocol_ids: Sequence[str],
    groundtruth_root: Path,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for target_protocol_id in target_protocol_ids:
        target_path = (
            groundtruth_root / target_protocol_id / T2_GROUNDTRUTH_FILENAME
        )
        target_document = load_json(target_path)
        target_hash = sha256_file(target_path)
        for donor_protocol_id in DONOR_PROTOCOL_IDS:
            donor_document = donor_documents[donor_protocol_id]["t2"]
            donor_source_path = (
                groundtruth_root / donor_protocol_id / T2_GROUNDTRUTH_FILENAME
            )
            donor_hash = sha256_file(donor_source_path)
            for donor_oligo in donor_document["oligos"]:
                donor_raw = _normalized_signature(donor_oligo, family=False)
                donor_family = _normalized_signature(donor_oligo, family=True)
                donor_raw_rc = _reverse_complement_signature(donor_raw)
                donor_family_rc = _reverse_complement_signature(donor_family)
                for target_oligo in target_document["oligos"]:
                    target_raw = _normalized_signature(target_oligo, family=False)
                    target_family = _normalized_signature(target_oligo, family=True)
                    exact = donor_raw == target_raw and bool(donor_raw)
                    reverse = (
                        donor_raw_rc is not None
                        and donor_raw_rc == target_raw
                        and bool(target_raw)
                    )
                    family_same = donor_family == target_family and bool(donor_family)
                    family_reverse = (
                        donor_family_rc is not None
                        and donor_family_rc == target_family
                        and bool(target_family)
                    )
                    if not (exact or reverse or family_same or family_reverse):
                        continue
                    if exact and reverse:
                        orientation = "same_and_reverse_complement"
                    elif exact:
                        orientation = "same_orientation"
                    elif reverse:
                        orientation = "reverse_complement"
                    elif family_same:
                        orientation = "family_scaffold_same_orientation"
                    else:
                        orientation = "family_scaffold_reverse_complement"
                    notes = []
                    if donor_raw_rc is None:
                        notes.append("raw reverse-complement comparison not evaluable")
                    if donor_family_rc is None:
                        notes.append("family reverse-complement comparison not evaluable")
                    if not notes:
                        notes.append("all reported comparisons evaluable")
                    rows.append(
                        {
                            "target_protocol_id": target_protocol_id,
                            "target_t2_sha256": target_hash,
                            "donor_protocol_id": donor_protocol_id,
                            "donor_t2_sha256": donor_hash,
                            "donor_oligo_id": donor_oligo["oligo_id"],
                            "target_oligo_id": target_oligo["oligo_id"],
                            "exact_sequence": _tsv_bool(exact),
                            "reverse_complement": _tsv_bool(reverse),
                            "family_scaffold": _tsv_bool(family_same or family_reverse),
                            "family_scaffold_orientation": (
                                "same_orientation"
                                if family_same
                                else "reverse_complement"
                                if family_reverse
                                else "none"
                            ),
                            "orientation_relationship": orientation,
                            "donor_raw_signature": json.dumps(
                                donor_raw, separators=(",", ":")
                            ),
                            "target_raw_signature": json.dumps(
                                target_raw, separators=(",", ":")
                            ),
                            "donor_family_signature": json.dumps(
                                donor_family, separators=(",", ":")
                            ),
                            "target_family_signature": json.dumps(
                                target_family, separators=(",", ":")
                            ),
                            "fixed_base_count": str(
                                min(
                                    _fixed_base_count(donor_family),
                                    _fixed_base_count(target_family),
                                )
                            ),
                            "evaluability_note": "; ".join(notes),
                        }
                    )
    return sorted(
        rows,
        key=lambda row: (
            row["target_protocol_id"],
            row["donor_protocol_id"],
            row["donor_oligo_id"],
            row["target_oligo_id"],
        ),
    )


def validate_external_knowledge_assets(asset_root: Path) -> dict[str, Any]:
    general_manifest_path = (
        asset_root / "knowledge" / "general_molecular_methods_v1_manifest.json"
    )
    memory_manifest_path = (
        asset_root / "memory" / "cross_protocol_memory_v1" / "manifest.json"
    )
    general_manifest = load_json(general_manifest_path)
    memory_manifest = load_json(memory_manifest_path)
    _validate_manifest_digest(general_manifest, "asset_digest")
    _validate_manifest_digest(memory_manifest, "asset_digest")
    _validate_file_entries(asset_root, general_manifest["files"])
    _validate_file_entries(asset_root, memory_manifest["files"])

    condition_manifests: dict[str, dict[str, Any]] = {}
    condition_summaries: list[dict[str, Any]] = []
    for condition_id in (
        "general_methods_v1",
        "cross_protocol_memory_v1",
        "general_methods_plus_memory_v1",
    ):
        path = asset_root / "conditions" / condition_id / "manifest.json"
        manifest = load_json(path)
        condition_manifests[condition_id] = manifest
        _validate_manifest_digest(manifest, "condition_digest")
        _validate_file_entries(asset_root, manifest["included_files"])
        if manifest.get("precedence_rule") != PRECEDENCE_RULE:
            raise ExternalKnowledgeBuildError(
                f"condition {condition_id} has the wrong precedence rule"
            )
        if manifest.get("mount_mode") != "read_only":
            raise ExternalKnowledgeBuildError(
                f"condition {condition_id} is not declared read-only"
            )
        if manifest.get("contents_merged_or_rewritten") is not False:
            raise ExternalKnowledgeBuildError(
                f"condition {condition_id} rewrites or merges frozen contents"
            )
        for entry in manifest["included_files"]:
            if entry.get("visibility") != "agent":
                raise ExternalKnowledgeBuildError(
                    f"condition {condition_id} exposes a non-agent file"
                )
            relative = entry["path"]
            if relative.endswith("donor_target_overlap.tsv") or relative.endswith(
                "projection_validation_report.json"
            ):
                raise ExternalKnowledgeBuildError(
                    f"condition {condition_id} exposes a review-only report"
                )
        condition_summaries.append(
            {
                "condition_id": condition_id,
                "included_file_count": len(manifest["included_files"]),
                "condition_digest": manifest["condition_digest"],
            }
        )

    general_paths = {entry["path"] for entry in general_manifest["files"]}
    memory_paths = {
        entry["path"]
        for entry in memory_manifest["files"]
        if entry.get("visibility") == "agent"
    }
    condition_paths = {
        condition_id: {
            entry["path"] for entry in manifest["included_files"]
        }
        for condition_id, manifest in condition_manifests.items()
    }
    expected_condition_paths = {
        "general_methods_v1": general_paths,
        "cross_protocol_memory_v1": memory_paths,
        "general_methods_plus_memory_v1": general_paths | memory_paths,
    }
    if condition_paths != expected_condition_paths:
        raise ExternalKnowledgeBuildError(
            "condition file sets are not the exact frozen asset union"
        )

    validation_report = load_json(
        asset_root
        / "memory"
        / "cross_protocol_memory_v1"
        / "projection_validation_report.json"
    )
    if validation_report.get("status") != "pass":
        raise ExternalKnowledgeBuildError("projection validation report did not pass")
    for donor in validation_report["donors"]:
        if donor["linked_prediction_validation"] != "pass":
            raise ExternalKnowledgeBuildError(
                f"donor projection failed linked validation: {donor['protocol_id']}"
            )
        for task in ("t2", "t3"):
            entry = donor[task]
            for check in (
                "retained_value_fidelity",
                "prediction_schema_validation",
                "protocol_id_validation",
            ):
                if entry.get(check) != "pass":
                    raise ExternalKnowledgeBuildError(
                        f"donor projection failed {check}: "
                        f"{donor['protocol_id']} {task}"
                    )
    index = (
        asset_root
        / "memory"
        / "cross_protocol_memory_v1"
        / "index.md"
    ).read_text(encoding="utf-8")
    if MEMORY_WARNING not in index:
        raise ExternalKnowledgeBuildError("memory index is missing the required warning")
    return {
        "status": "pass",
        "condition_summaries": condition_summaries,
        "review_only_exposure_check": "pass",
        "memory_warning_check": "pass",
    }


def build_external_knowledge_review_candidate(
    *,
    prior_asset_root: Path,
    revised_asset_root: Path,
    review_request: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build a detached, hash-pinned review candidate for a primer-only revision.

    The frozen packages remain immutable. This comparison accepts only the two
    primer files and the three manifests whose hashes transitively depend on
    them. Donor projections, review-only overlap data, and the memory-only
    condition must remain byte-identical.
    """

    validate_external_knowledge_assets(prior_asset_root)
    validate_external_knowledge_assets(revised_asset_root)
    _validate_external_knowledge_review_request(review_request)

    prior_files = _package_file_inventory(prior_asset_root)
    revised_files = _package_file_inventory(revised_asset_root)
    if set(prior_files) != set(revised_files):
        raise ExternalKnowledgeBuildError(
            "review candidate changes the frozen package file set"
        )
    changed_paths = sorted(
        path for path in prior_files if prior_files[path] != revised_files[path]
    )
    if set(changed_paths) != PRIMER_REVISION_CHANGED_PATHS:
        raise ExternalKnowledgeBuildError(
            "primer revision changed an unexpected file set: "
            + ", ".join(changed_paths)
        )

    primer_path = revised_asset_root / "knowledge/general_molecular_methods_v1.md"
    primer_text = primer_path.read_text(encoding="utf-8")
    card_blocks = _primer_card_blocks(primer_text)
    primer_request = review_request["primer"]
    revised_card_ids = set(primer_request["revised_card_ids"])
    provisional_card_ids = set(
        primer_request["provisionally_accepted_card_ids"]
    )
    if revised_card_ids & provisional_card_ids:
        raise ExternalKnowledgeBuildError(
            "revised and provisionally accepted primer card sets overlap"
        )
    if revised_card_ids | provisional_card_ids != set(card_blocks):
        raise ExternalKnowledgeBuildError(
            "review request does not disposition every primer card"
        )
    for card_id in primer_request["caution_card_ids"]:
        if "**Source-limit/caution card:**" not in card_blocks[card_id]:
            raise ExternalKnowledgeBuildError(
                f"primer card {card_id} lacks the requested caution marker"
            )
    for card_id in primer_request["artifact_warning_card_ids"]:
        if "**Artifact warning:**" not in card_blocks[card_id]:
            raise ExternalKnowledgeBuildError(
                f"primer card {card_id} lacks the requested artifact marker"
            )

    condition_ids = (
        "general_methods_v1",
        "cross_protocol_memory_v1",
        "general_methods_plus_memory_v1",
    )
    prior_conditions = {
        condition_id: load_json(
            prior_asset_root / "conditions" / condition_id / "manifest.json"
        )["condition_digest"]
        for condition_id in condition_ids
    }
    revised_conditions = {
        condition_id: load_json(
            revised_asset_root / "conditions" / condition_id / "manifest.json"
        )["condition_digest"]
        for condition_id in condition_ids
    }
    if (
        prior_conditions["cross_protocol_memory_v1"]
        != revised_conditions["cross_protocol_memory_v1"]
    ):
        raise ExternalKnowledgeBuildError(
            "memory-only condition changed during a primer-only revision"
        )
    for condition_id in (
        "general_methods_v1",
        "general_methods_plus_memory_v1",
    ):
        if prior_conditions[condition_id] == revised_conditions[condition_id]:
            raise ExternalKnowledgeBuildError(
                f"affected condition digest did not change: {condition_id}"
            )

    projection_report_path = (
        revised_asset_root
        / "memory/cross_protocol_memory_v1/projection_validation_report.json"
    )
    projection_report = load_json(projection_report_path)
    donor_verification: list[dict[str, Any]] = []
    for donor in projection_report["donors"]:
        if donor["t2"]["source_sha256"] != donor["lineage"][
            "approved_current_t2_sha256"
        ]:
            raise ExternalKnowledgeBuildError(
                f"donor T2 is not approved-current: {donor['protocol_id']}"
            )
        if donor["t3"]["source_sha256"] != donor["lineage"][
            "approved_current_t3_sha256"
        ]:
            raise ExternalKnowledgeBuildError(
                f"donor T3 is not approved-current: {donor['protocol_id']}"
            )
        donor_verification.append(
            {
                "protocol_id": donor["protocol_id"],
                "approved_current_t2_sha256": donor["lineage"][
                    "approved_current_t2_sha256"
                ],
                "approved_current_t3_sha256": donor["lineage"][
                    "approved_current_t3_sha256"
                ],
                "projected_t2_sha256": donor["t2"]["output_sha256"],
                "projected_t3_sha256": donor["t3"]["output_sha256"],
                "retained_value_fidelity": "pass",
                "prediction_and_link_validation": "pass",
            }
        )

    overlap_path = (
        revised_asset_root
        / "memory/cross_protocol_memory_v1/donor_target_overlap.tsv"
    )
    with overlap_path.open(encoding="utf-8", newline="") as handle:
        overlap_rows = list(csv.DictReader(handle, delimiter="\t"))
    overlap_summary = {
        "row_count": len(overlap_rows),
        "exact_sequence_row_count": sum(
            row["exact_sequence"] == "true" for row in overlap_rows
        ),
        "reverse_complement_row_count": sum(
            row["reverse_complement"] == "true" for row in overlap_rows
        ),
        "family_scaffold_row_count": sum(
            row["family_scaffold"] == "true" for row in overlap_rows
        ),
    }

    evidence_path = (
        revised_asset_root
        / "knowledge/general_molecular_methods_v1_evidence.tsv"
    )
    with evidence_path.open(encoding="utf-8", newline="") as handle:
        evidence_row_count = sum(1 for _ in csv.DictReader(handle, delimiter="\t"))

    payload: dict[str, Any] = {
        "review_candidate_id": review_request["review_request_id"]
        + ":candidate-002",
        "status": "awaiting_source_locator_review_and_final_human_approval",
        "created_at": created_at,
        "review_request": copy.deepcopy(dict(review_request)),
        "prior_package": {
            "root_name": prior_asset_root.name,
            "package_digest": _package_inventory_digest(prior_files),
            "condition_digests": prior_conditions,
        },
        "revised_package": {
            "root_name": revised_asset_root.name,
            "package_digest": _package_inventory_digest(revised_files),
            "condition_digests": revised_conditions,
        },
        "change_scope": {
            "changed_files": [
                {
                    "path": path,
                    "prior_sha256": prior_files[path],
                    "revised_sha256": revised_files[path],
                }
                for path in changed_paths
            ],
            "unexpected_changed_file_count": 0,
            "donor_memory_byte_identical": True,
            "overlap_report_byte_identical": True,
        },
        "primer_review": {
            "card_count": len(card_blocks),
            "revised_card_ids": primer_request["revised_card_ids"],
            "provisionally_accepted_card_ids": primer_request[
                "provisionally_accepted_card_ids"
            ],
            "caution_card_ids": primer_request["caution_card_ids"],
            "artifact_warning_card_ids": primer_request[
                "artifact_warning_card_ids"
            ],
            "evidence_row_count": evidence_row_count,
            "source_locator_review_status": "pending",
        },
        "donor_projection_review": {
            "lineage_selection": "approved_current_t3_sha256",
            "projection_report_sha256": sha256_file(projection_report_path),
            "direct_report_verification": "pass",
            "donors": donor_verification,
        },
        "overlap_review": {
            "disposition": (
                "accepted_for_full_solved_protocol_memory_condition"
            ),
            "interpretation": (
                "Prior solved protocols include reusable molecular structures "
                "and shared exact oligo sequences; this is not a pure "
                "mechanistic-transfer-without-answer-overlap condition."
            ),
            "report_path": (
                "memory/cross_protocol_memory_v1/donor_target_overlap.tsv"
            ),
            "report_sha256": sha256_file(overlap_path),
            "agent_visibility": "review_only_hidden",
            **overlap_summary,
        },
        "analysis_preregistration": copy.deepcopy(
            review_request["analysis_requirements"]
        ),
        "authorization": {
            "harbor_integration_authorized": False,
            "experiment_run_authorized": False,
            "remaining_gate": (
                f"Complete all {evidence_row_count} source-locator checks, record "
                "reviewer identity, and explicitly approve the revised condition "
                "digests."
            ),
        },
    }
    candidate = _with_digest(payload, "review_candidate_digest")
    validate_external_knowledge_review_candidate(candidate)
    return candidate


def validate_external_knowledge_review_candidate(
    candidate: Mapping[str, Any],
) -> None:
    _validate_manifest_digest(candidate, "review_candidate_digest")
    if candidate.get("status") != (
        "awaiting_source_locator_review_and_final_human_approval"
    ):
        raise ExternalKnowledgeBuildError("invalid review candidate status")
    authorization = candidate.get("authorization")
    if not isinstance(authorization, Mapping):
        raise ExternalKnowledgeBuildError("review candidate lacks authorization")
    if authorization.get("harbor_integration_authorized") is not False:
        raise ExternalKnowledgeBuildError(
            "revision candidate must not authorize Harbor integration"
        )
    if authorization.get("experiment_run_authorized") is not False:
        raise ExternalKnowledgeBuildError(
            "revision candidate must not authorize an experiment run"
        )


def _validate_external_knowledge_review_request(
    review_request: Mapping[str, Any],
) -> None:
    if not isinstance(review_request.get("review_request_id"), str):
        raise ExternalKnowledgeBuildError("review request lacks an ID")
    primer = review_request.get("primer")
    if not isinstance(primer, Mapping) or primer.get("decision") != "revise":
        raise ExternalKnowledgeBuildError("review request must revise the primer")
    for key in (
        "revised_card_ids",
        "provisionally_accepted_card_ids",
        "caution_card_ids",
        "artifact_warning_card_ids",
    ):
        value = primer.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ExternalKnowledgeBuildError(
                f"review request primer field {key} must be a string list"
            )
        if len(value) != len(set(value)):
            raise ExternalKnowledgeBuildError(
                f"review request primer field {key} contains duplicates"
            )
    donor = review_request.get("donor_projection")
    if not isinstance(donor, Mapping) or donor.get("lineage_selection") != (
        "approved_current_t3_sha256"
    ):
        raise ExternalKnowledgeBuildError(
            "review request must select approved-current donor T3 lineage"
        )
    overlap = review_request.get("overlap")
    if not isinstance(overlap, Mapping):
        raise ExternalKnowledgeBuildError("review request lacks overlap disposition")
    if overlap.get("accepted") is not True or overlap.get("hidden_from_agents") is not True:
        raise ExternalKnowledgeBuildError(
            "review request must accept and hide the overlap report"
        )
    analysis = review_request.get("analysis_requirements")
    if not isinstance(analysis, Mapping):
        raise ExternalKnowledgeBuildError(
            "review request lacks analysis preregistration"
        )
    if analysis.get("primary_memory_outcome") != "t3_molecular_transition_f1":
        raise ExternalKnowledgeBuildError(
            "primary memory outcome must be T3 molecular-transition F1"
        )
    if analysis.get("t2_family_f1_strata") != [
        "donor_overlapping_target_families",
        "target_families_absent_from_donor_memory",
    ]:
        raise ExternalKnowledgeBuildError(
            "review request lacks the required overlap-stratified T2 analysis"
        )
    sensitivity = analysis.get("structure_only_memory_sensitivity")
    if not isinstance(sensitivity, Mapping) or sensitivity.get("status") != "planned":
        raise ExternalKnowledgeBuildError(
            "review request must preregister the structure-only sensitivity"
        )


def _package_file_inventory(asset_root: Path) -> dict[str, str]:
    return {
        path.relative_to(asset_root).as_posix(): sha256_file(path)
        for path in sorted(asset_root.rglob("*"))
        if path.is_file()
    }


def _package_inventory_digest(inventory: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            "files": [
                {"path": path, "sha256": inventory[path]}
                for path in sorted(inventory)
            ]
        }
    )


def _primer_card_blocks(primer_text: str) -> dict[str, str]:
    matches = list(
        re.finditer(r"(?m)^### ([A-Z][A-Z0-9-]+)\s+[—–-]\s+.+$", primer_text)
    )
    return {
        match.group(1): primer_text[
            match.start() : (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(primer_text)
            )
        ]
        for index, match in enumerate(matches)
    }


def _load_donor_lineage(*, audit_root: Path) -> dict[str, dict[str, Any]]:
    promotion_root = audit_root / "promotions"
    migration_approval_path = (
        audit_root
        / "runs"
        / "connected-process-migration-001"
        / "final-approval-preview-003.json"
    )
    migration_approval = load_json(migration_approval_path)
    if (
        migration_approval.get("status") != "final"
        or migration_approval.get("scientific_disposition") != "approved"
    ):
        raise ExternalKnowledgeBuildError(
            "connected-process migration does not have final scientific approval"
        )
    migration_by_protocol = {
        item["protocol_id"]: item for item in migration_approval["protocols"]
    }
    result: dict[str, dict[str, Any]] = {}
    for protocol_id in DONOR_PROTOCOL_IDS:
        promotion_paths = sorted((promotion_root / protocol_id).glob("*.json"))
        if not promotion_paths:
            raise ExternalKnowledgeBuildError(
                f"donor does not have a promotion record: {protocol_id}"
            )
        promotion_path = promotion_paths[-1]
        promotion = load_json(promotion_path)
        if promotion.get("protocol_id") != protocol_id:
            raise ExternalKnowledgeBuildError(
                f"promotion protocol mismatch for {protocol_id}"
            )
        promoted_hashes = {
            item["filename"]: item["sha256"] for item in promotion["artifacts"]
        }
        migration = migration_by_protocol.get(protocol_id)
        if migration is None:
            raise ExternalKnowledgeBuildError(
                f"donor missing from connected-process approval: {protocol_id}"
            )
        approved_hashes = {
            item["filename"]: item["candidate_sha256"]
            for item in migration["artifacts"]
        }
        result[protocol_id] = {
            "promotion_id": promotion["promotion_id"],
            "promotion_record_sha256": sha256_file(promotion_path),
            "promoted_t2_sha256": promoted_hashes[T2_GROUNDTRUTH_FILENAME],
            "promoted_t3_sha256": promoted_hashes[T3_GROUNDTRUTH_FILENAME],
            "connected_process_approval_id": migration_approval["approval_id"],
            "connected_process_approval_sha256": sha256_file(
                migration_approval_path
            ),
            "approved_current_t2_sha256": approved_hashes[
                T2_GROUNDTRUTH_FILENAME
            ],
            "approved_current_t3_sha256": approved_hashes[
                T3_GROUNDTRUTH_FILENAME
            ],
        }
    return result


def _projection_validation_entry(
    *,
    source_path: Path,
    output_path: Path,
    source_document: Mapping[str, Any],
    result: ProjectionResult,
    record_count: int,
) -> dict[str, Any]:
    return {
        "source_filename": source_path.name,
        "source_sha256": sha256_file(source_path),
        "output_filename": output_path.name,
        "output_sha256": sha256_file(output_path),
        "record_count": record_count,
        "removed_json_pointers": list(result.removed_json_pointers),
        "retained_leaf_count": result.retained_leaf_count,
        "retained_value_fidelity": (
            "pass"
            if _projection_is_value_faithful(
                source_document,
                result.document,
            )
            else "fail"
        ),
        "prediction_schema_validation": "pass",
        "protocol_id_validation": "pass",
    }


def _projection_is_value_faithful(source: Any, projected: Any) -> bool:
    if isinstance(projected, Mapping):
        if not isinstance(source, Mapping):
            return False
        return all(
            key in source and _projection_is_value_faithful(source[key], value)
            for key, value in projected.items()
        )
    if isinstance(projected, list):
        if not isinstance(source, list) or len(projected) != len(source):
            return False
        return all(
            _projection_is_value_faithful(source_value, projected_value)
            for source_value, projected_value in zip(source, projected, strict=True)
        )
    return source == projected


def _memory_index() -> str:
    donor_lines = "\n".join(
        f"- `{protocol_id}`: "
        f"[`t2_prediction.json`](donors/{protocol_id}/t2_prediction.json), "
        f"[`t3_prediction.json`](donors/{protocol_id}/t3_prediction.json)"
        for protocol_id in DONOR_PROTOCOL_IDS
    )
    return (
        "# Cross-protocol memory v1\n\n"
        f"> {MEMORY_WARNING}\n\n"
        "The records below are deterministic prediction-schema projections of five "
        "hash-pinned, human-reviewed donor records. They are molecular worked examples, "
        "not a source bundle and not target evidence.\n\n"
        "## Donor exemplars\n\n"
        f"{donor_lines}\n"
    )


def _write_condition_manifests(
    *,
    asset_root: Path,
    condition_root: Path,
    created_at: str,
    general_agent_files: Sequence[Mapping[str, Any]],
    memory_agent_files: Sequence[Mapping[str, Any]],
    source_entries: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    definitions = {
        "general_methods_v1": {
            "included_assets": ["general_molecular_methods_v1"],
            "included_files": list(general_agent_files),
            "source_protocol_ids": [],
            "donor_protocol_ids": [],
            "source_ids": [item["source_id"] for item in source_entries],
            "source_files": list(source_entries),
        },
        "cross_protocol_memory_v1": {
            "included_assets": ["cross_protocol_memory_v1"],
            "included_files": list(memory_agent_files),
            "source_protocol_ids": list(DONOR_PROTOCOL_IDS),
            "donor_protocol_ids": list(DONOR_PROTOCOL_IDS),
            "source_ids": [],
            "source_files": [],
        },
        "general_methods_plus_memory_v1": {
            "included_assets": [
                "general_molecular_methods_v1",
                "cross_protocol_memory_v1",
            ],
            "included_files": [*general_agent_files, *memory_agent_files],
            "source_protocol_ids": list(DONOR_PROTOCOL_IDS),
            "donor_protocol_ids": list(DONOR_PROTOCOL_IDS),
            "source_ids": [item["source_id"] for item in source_entries],
            "source_files": list(source_entries),
        },
    }
    result: dict[str, dict[str, Any]] = {}
    for condition_id, definition in definitions.items():
        payload = {
            "condition_id": condition_id,
            "status": "draft_for_human_review",
            "frozen": True,
            "created_at": created_at,
            "precedence_rule": PRECEDENCE_RULE,
            "target_protocol_ids": list(TARGET_PROTOCOL_IDS),
            "mount_mode": "read_only",
            "contents_merged_or_rewritten": False,
            **definition,
        }
        manifest = _with_digest(payload, "condition_digest")
        path = condition_root / condition_id / "manifest.json"
        write_json(path, manifest)
        result[condition_id] = manifest
    return result


def _with_digest(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result[field] = canonical_digest(result)
    return result


def _validate_manifest_digest(manifest: Mapping[str, Any], field: str) -> None:
    actual = manifest.get(field)
    payload = {key: value for key, value in manifest.items() if key != field}
    expected = canonical_digest(payload)
    if actual != expected:
        raise ExternalKnowledgeBuildError(
            f"manifest digest mismatch: expected {expected}, got {actual}"
        )


def _file_entry(asset_root: Path, relative: str, *, visibility: str) -> dict[str, str]:
    return {
        "path": relative,
        "sha256": sha256_file(asset_root / relative),
        "visibility": visibility,
    }


def _validate_file_entries(
    asset_root: Path,
    entries: Iterable[Mapping[str, Any]],
) -> None:
    seen: set[str] = set()
    for entry in entries:
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative or relative in seen:
            raise ExternalKnowledgeBuildError("manifest has an invalid duplicate path")
        seen.add(relative)
        path = asset_root / relative
        if not path.is_file():
            raise ExternalKnowledgeBuildError(f"manifest file is missing: {relative}")
        if entry.get("sha256") != sha256_file(path):
            raise ExternalKnowledgeBuildError(f"manifest hash is stale: {relative}")


def _normalized_signature(
    oligo: Mapping[str, Any],
    *,
    family: bool,
) -> tuple[str, ...]:
    return tuple(sorted(_canonical_oligo_signature(oligo, family_template=family)))


def _reverse_complement_signature(
    signature: Sequence[str],
) -> tuple[str, ...] | None:
    reversed_claims: list[str] = []
    for claim in signature:
        reverse = _reverse_complement_architecture(claim)
        if reverse is None:
            return None
        reversed_claims.append(reverse)
    return tuple(sorted(reversed_claims))


def _reverse_complement_architecture(sequence: str) -> str | None:
    if re.search(r"(?:r|\+)[ACGTUN]|/[^/]+/|\(dU\)", sequence):
        return None
    normalized = sequence.upper().replace("U", "T")
    parts = _ARCHITECTURE_PART_RE.findall(normalized)
    if not parts or "".join(parts) != normalized:
        return None
    result: list[str] = []
    for part in reversed(parts):
        if part.startswith("["):
            result.append(part)
        else:
            result.append(part.translate(_IUPAC_COMPLEMENT)[::-1])
    return "".join(result)


def _fixed_base_count(signature: Sequence[str]) -> int:
    total = 0
    for claim in signature:
        without_placeholders = re.sub(r"\[[^\]]+\]", "", claim)
        without_placeholders = re.sub(r"/[^/]+/", "", without_placeholders)
        without_placeholders = re.sub(
            r"(?:r|\+)(?=[ACGTUN])|\(dU\)",
            "",
            without_placeholders,
        )
        total += sum(base.upper() in "ACGTURYSWKMBDHVN" for base in without_placeholders)
    return total


def _tsv_bool(value: bool) -> str:
    return "true" if value else "false"


def _write_overlap_tsv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    columns = (
        "target_protocol_id",
        "target_t2_sha256",
        "donor_protocol_id",
        "donor_t2_sha256",
        "donor_oligo_id",
        "target_oligo_id",
        "exact_sequence",
        "reverse_complement",
        "family_scaffold",
        "family_scaffold_orientation",
        "orientation_relationship",
        "donor_raw_signature",
        "target_raw_signature",
        "donor_family_signature",
        "target_family_signature",
        "fixed_base_count",
        "evaluability_note",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
