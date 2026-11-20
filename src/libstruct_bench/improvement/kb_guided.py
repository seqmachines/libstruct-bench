from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.audit.connected_process import (
    ConnectedProcessMigrationError,
    migrate_connected_process_bundle,
)
from libstruct_bench.libgen.validation import validate_groundtruth_bundle
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    reject_private_output_in_repository,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .exemplar_memory import _build_mechanism_summary


DESIGN_SCHEMA_VERSION = "libstruct.libgen_kb_experiment_design.v1"
DESIGN_SCHEMA = "kb_experiment_design.schema.json"
IDENTITY_SCHEMA_VERSION = "libstruct.libgen_kb_identity_map.v1"
IDENTITY_SCHEMA = "kb_identity_map.schema.json"
OLIGO_CARD_SCHEMA_VERSION = "libstruct.libgen_kb_oligo_card.v1"
OLIGO_CARD_SCHEMA = "kb_oligo_card.schema.json"
GRAPH_CARD_SCHEMA_VERSION = "libstruct.libgen_kb_graph_card.v1"
GRAPH_CARD_SCHEMA = "kb_graph_card.schema.json"
CATALOG_SCHEMA_VERSION = "libstruct.libgen_kb_catalog.v1"
CATALOG_SCHEMA = "kb_catalog.schema.json"
EXPOSURE_CATALOG_SCHEMA_VERSION = "libstruct.libgen_kb_exposure_catalog.v1"
EXPOSURE_CATALOG_SCHEMA = "kb_exposure_catalog.schema.json"
MICROSTATE_SCHEMA_VERSION = "libstruct.libgen_kb_microstate.v1"
MICROSTATE_SCHEMA = "kb_microstate.schema.json"
C0_SCHEMA_VERSION = "libstruct.libgen_kb_c0_compatibility.v2"
C0_SCHEMA = "kb_c0_compatibility.schema.json"
CHECKPOINT_SCHEMA_VERSION = "libstruct.libgen_kb_checkpoint.v1"
CHECKPOINT_SCHEMA = "kb_checkpoint.schema.json"
EVALUATION_MATRIX_SCHEMA_VERSION = "libstruct.libgen_kb_evaluation_matrix.v1"
EVALUATION_MATRIX_SCHEMA = "kb_evaluation_matrix.schema.json"
ADJUDICATION_SCHEMA_VERSION = "libstruct.libgen_kb_training_adjudication.v1"
ADJUDICATION_SCHEMA = "kb_training_adjudication.schema.json"
TASK_REGISTRY_SCHEMA_VERSION = "libstruct.libgen_kb_task_registry.v1"
TASK_REGISTRY_SCHEMA = "kb_task_registry.schema.json"
TRAINING_PLAN_SCHEMA_VERSION = "libstruct.libgen_kb_training_run_plan.v1"
TRAINING_PLAN_SCHEMA = "kb_training_run_plan.schema.json"
TRAINING_BATCH_PLAN_SCHEMA_VERSION = (
    "libstruct.libgen_kb_training_batch_run_plan.v1"
)
TRAINING_BATCH_PLAN_SCHEMA = "kb_training_batch_run_plan.schema.json"
BASELINE_TRAINING_PLAN_SCHEMA_VERSION = (
    "libstruct.libgen_kb_training_baseline_plan.v1"
)
BASELINE_TRAINING_PLAN_SCHEMA = "kb_training_baseline_plan.schema.json"
C0_BACKFILL_PLAN_SCHEMA_VERSION = "libstruct.libgen_kb_c0_backfill_plan.v1"
C0_BACKFILL_PLAN_SCHEMA = "kb_c0_backfill_plan.schema.json"
EVALUATION_PLAN_SCHEMA_VERSION = "libstruct.libgen_kb_evaluation_run_plan.v1"
EVALUATION_PLAN_SCHEMA = "kb_evaluation_run_plan.schema.json"
COMPOSITE_ENDPOINT_SCHEMA_VERSION = "libstruct.libgen_kb_composite_endpoint.v1"
COMPOSITE_ENDPOINT_SCHEMA = "kb_composite_endpoint.schema.json"
FINAL_LOCK_SCHEMA_VERSION = "libstruct.libgen_kb_final_lock.v1"
FINAL_LOCK_SCHEMA = "kb_final_lock.schema.json"
VALIDATION_SELECTION_SCHEMA_VERSION = (
    "libstruct.libgen_kb_validation_selection.v1"
)
VALIDATION_SELECTION_SCHEMA = "kb_validation_selection.schema.json"
EXPERIMENT_SCHEMA_VERSION = "libstruct.libgen_kb_experiment.v1"
EXPERIMENT_SCHEMA = "kb_experiment_state.schema.json"
BEHAVIORAL_LINEAGE_SCHEMA_VERSION = "libstruct.libgen_kb_behavioral_lineage.v2"
BEHAVIORAL_LINEAGE_SCHEMA = "kb_behavioral_lineage.schema.json"

GROUNDTRUTH_FILES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}
RETRIEVAL_SUPPORT = frozenset({"explicit", "derivable"})
RETRIEVAL_POLICY_V2 = "source_evidenced_full_similarity_ranking_agent_selected_v2"
RETRIEVAL_POLICY_V3 = (
    "source_evidenced_full_similarity_ranking_agent_selected_unbounded_v3"
)
# New plans always use v3. v2 is recognized only so archived, hash-pinned
# two-card runs remain auditable; it is never selected for a new plan.
RETRIEVAL_POLICY = RETRIEVAL_POLICY_V3
SUPPORTED_RETRIEVAL_POLICIES = frozenset(
    {RETRIEVAL_POLICY_V2, RETRIEVAL_POLICY_V3}
)
IGNORED_TREE_PARTS = frozenset({"__pycache__", ".pytest_cache", ".DS_Store"})
PROTOCOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
KB_QUERY_FEATURE_VOCABULARY = (
    "Use only these controlled query values: modalities = gene expression, genomic "
    "DNA, feature barcode, sgRNA, chromatin accessibility; operations = "
    "reverse_transcription, ligation, tagmentation, extension, pcr, amplification, "
    "fragmentation, denaturation, strand_synthesis, cleanup, size_selection, "
    "affinity_selection, sample_split, pooling, indexing, circularization, capture, "
    "other; architectures = single_stranded, double_stranded, partially_duplex, "
    "rna_dna_hybrid, y_shaped_duplex, mixed_population, unknown; "
    "barcoding_partitioning = cell_barcode, molecular_barcode, sample_index, "
    "round_barcode, combinatorial_indexing, droplet_partitioning, "
    "microwell_partitioning, plate_partitioning, bead_partitioning, "
    "split_pool_partitioning; selection_branching = affinity_selection, "
    "size_selection, capture, sample_split, modality_branching, "
    "alternative_branching, discard_branching; chemistries = reverse_transcription, "
    "template_switching, ligation, tagmentation, pcr, restriction, conversion."
)
SOURCE_INSPECTION_EFFICIENCY = (
    "For throughput, do not inspect help or implementation source for parsers or the "
    "KB query tool when exact commands are supplied, and do not run three redundant "
    "whole-document conversions. Read every source file once with fast native/layout "
    "text first; use a complementary parser only for missing, ambiguous, or table-heavy "
    "passages, and render the pages that contain figures, diagrams, or unresolved "
    "sequence evidence rather than prose-only pages. This changes no evidence standard: "
    "every listed source must still be covered, and figure-only evidence must still be "
    "inspected."
)
KB_RETRIEVAL_WORKFLOW = """
First calculate the complete deterministic similarity ranking:

```bash
python /workspace/knowledge_base/query_kb.py rank \\
  --query /logs/artifacts/kb_query.json \\
  --output /logs/artifacts/kb_ranking.json
```

`kb_ranking.json` lists every admitted pseudonymous donor in weighted-Jaccard
order, including zero-overlap donors, with its controlled feature profile. It
does not contain oligo or graph cards. Inspect the full ranking, then write:

```json
{
  "schema_version": "libstruct.libgen_kb_selection.v1",
  "ranking_digest": "copy ranking_digest from kb_ranking.json",
  "selected_exemplar_ids": ["exm-..."],
  "rationale": "Why this agent-chosen subset is useful and sufficient"
}
```

You may select any number of distinct donors from the complete ranking, including
none, and you are not restricted to the highest-ranked donors. Prefer donors
whose oligo architecture, physical-state topology, or event mechanism resolves
a concrete target-source question. Fetch every card you judge useful and avoid
irrelevant cards only to conserve your own context. There is no cardinality cap.
Fetch the chosen cards:

```bash
python /workspace/knowledge_base/query_kb.py fetch \\
  --query /logs/artifacts/kb_query.json \\
  --ranking /logs/artifacts/kb_ranking.json \\
  --selection /logs/artifacts/kb_selection.json \\
  --output /logs/artifacts/kb_retrieval.json
```

Read donor content only from `/logs/artifacts/kb_retrieval.json`; do not browse
the mounted catalog or raw card files directly. Donors are pseudonymous
analogies, never evidence. Reconcile every borrowed idea against the target
sources.
""".strip()


def compile_kb_design(*, draft_path: Path, output_path: Path) -> dict[str, Any]:
    """Compile and hash-bind one private manifest-driven experiment design."""

    draft = load_json_object(draft_path, label="KB experiment design draft")
    if "design_digest" in draft:
        raise CapabilityImprovementError(
            "KB design draft must not contain design_digest"
        )
    document = copy.deepcopy(draft)
    document["schema_version"] = DESIGN_SCHEMA_VERSION
    document["created_at"] = normalize_timestamp(str(document.get("created_at", "")))
    document["benchmark_version"] = LIBGEN_BENCHMARK_VERSION
    document = with_digest(document, "design_digest")
    validate_kb_design(document)
    destination = output_path.expanduser().resolve()
    if destination.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite compiled KB design: {destination}"
        )
    write_json_atomic(destination, document, mode=0o444)
    return document


def validate_kb_design(document: Mapping[str, Any]) -> None:
    validate_document(
        dict(document),
        improvement_schema_root() / DESIGN_SCHEMA,
        label="KB experiment design",
    )
    validate_digest(document, "design_digest")
    counts = document["expected_counts"]
    protocols = list(document["protocol_ids"])
    split = document["split"]
    training = list(split["training"])
    validation = list(split["validation"])
    test = list(split["test"])
    if len(protocols) != counts["universe"] or len(set(protocols)) != len(protocols):
        raise CapabilityImprovementError(
            "KB design protocol universe does not match expected_counts.universe"
        )
    for label, values in (
        ("training", training),
        ("validation", validation),
        ("test", test),
    ):
        if len(values) != counts[label]:
            raise CapabilityImprovementError(
                f"KB design {label} count does not match expected_counts"
            )
    if (
        set(training) & set(validation)
        or set(training) & set(test)
        or set(validation) & set(test)
    ):
        raise CapabilityImprovementError("KB design split panels overlap")
    if set(training) | set(validation) | set(test) != set(protocols):
        raise CapabilityImprovementError(
            "KB design split does not exactly cover the protocol universe"
        )
    batch_ids = [item["batch_id"] for item in document["training_batches"]]
    if len(batch_ids) != len(set(batch_ids)):
        raise CapabilityImprovementError("KB design repeats a training batch ID")
    ordered_training = [
        protocol_id
        for batch in document["training_batches"]
        for protocol_id in batch["protocol_ids"]
    ]
    if ordered_training != training:
        raise CapabilityImprovementError(
            "KB design training_batches do not reproduce split.training order"
        )
    checkpoints = document["learned_checkpoints"]
    checkpoint_counts = [item["after_training_count"] for item in checkpoints]
    checkpoint_labels = [item["label"] for item in checkpoints]
    if checkpoint_counts != sorted(set(checkpoint_counts)):
        raise CapabilityImprovementError(
            "KB learned checkpoints must have strictly increasing counts"
        )
    if checkpoint_labels != [f"C{value}" for value in checkpoint_counts]:
        raise CapabilityImprovementError(
            "KB checkpoint labels must equal their cumulative training counts"
        )
    if checkpoint_counts[-1] != len(training):
        raise CapabilityImprovementError(
            "final learned checkpoint does not include all training protocols"
        )
    if len(checkpoints) != counts["learned_checkpoints"]:
        raise CapabilityImprovementError(
            "learned checkpoint count does not match expected_counts"
        )
    if counts["new_validation_cells"] != len(checkpoints) * len(validation):
        raise CapabilityImprovementError("new validation cell count is inconsistent")
    if counts["new_test_cells"] != len(checkpoints) * len(test):
        raise CapabilityImprovementError("new test cell count is inconsistent")
    family_protocols: set[str] = set()
    for family in document["families"]:
        values = family["ordered_protocol_ids"]
        if not set(values).issubset(protocols):
            raise CapabilityImprovementError(
                f"family {family['family_id']} contains an unknown protocol"
            )
        duplicates = family_protocols & set(values)
        if duplicates:
            raise CapabilityImprovementError(
                "protocol appears in multiple version families: "
                + ", ".join(sorted(duplicates))
            )
        family_protocols.update(values)
    execution = document["execution"]
    if not set(execution["slow_protocol_ids"]).issubset(protocols):
        raise CapabilityImprovementError(
            "KB design slow_protocol_ids contains an unknown protocol"
        )
    if (
        execution["slow_protocol_timeout_seconds"]
        <= execution["default_timeout_seconds"]
    ):
        raise CapabilityImprovementError(
            "slow-protocol timeout must exceed the default timeout"
        )


def initialize_kb_experiment(
    *,
    design_path: Path,
    output_root: Path,
    source_manifest_search_root: Path,
    groundtruth_root: Path,
    baseline_root: Path,
    repository_root: Path,
    run_root: Path,
    created_at: str,
    baseline_task_root: Path | None = None,
) -> dict[str, Any]:
    """Initialize an immutable C0 substrate without exposing private truth."""

    design = _load_validated(
        design_path,
        schema=DESIGN_SCHEMA,
        digest_field="design_digest",
        label="KB experiment design",
    )
    validate_kb_design(design)
    target = output_root.expanduser().resolve()
    reject_private_output_in_repository(target)
    if target.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite KB experiment: {target}"
        )
    source_search = _required_directory(
        source_manifest_search_root, "source-manifest search root"
    )
    truth_root = _required_directory(groundtruth_root, "ground-truth root")
    baseline = _required_directory(baseline_root, "baseline result root")
    repository = _required_directory(repository_root, "repository root")
    timestamp = normalize_timestamp(created_at)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.kb-stage-", dir=target.parent)
    )
    try:
        (stage / "design/source_manifests").mkdir(parents=True)
        (stage / "private").mkdir(mode=0o700)
        (stage / "knowledge/cards").mkdir(parents=True)
        (stage / "microstates").mkdir()
        (stage / "checkpoints/C0").mkdir(parents=True)
        shutil.copy2(design_path.expanduser().resolve(), stage / "design/design.json")

        universe = _build_universe_contract(
            protocol_ids=design["protocol_ids"],
            source_manifest_search_root=source_search,
            groundtruth_root=truth_root,
            output_manifest_root=stage / "design/source_manifests",
        )
        identity = _build_identity_map(design)
        identity_path = stage / "private/identity_map.json"
        write_json_atomic(identity_path, identity, mode=0o400)

        catalog = with_digest(
            {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "identity_map_digest": identity["identity_map_digest"],
                "training_count": 0,
                "entries": [],
            },
            "catalog_digest",
        )
        _validate_generated(catalog, CATALOG_SCHEMA, "catalog_digest", "KB catalog")
        catalog_path = stage / "knowledge/catalog.json"
        write_json_atomic(catalog_path, catalog, mode=0o444)

        c0 = build_c0_compatibility_registry(
            design=design,
            baseline_root=baseline,
            source_manifests={
                item["protocol_id"]: load_json_object(
                    stage / item["source_manifest_path"],
                    label=f"{item['protocol_id']} staged source manifest",
                )
                for item in universe["entries"]
            },
            repository_root=repository,
            baseline_task_root=baseline_task_root,
        )
        c0_path = stage / "design/c0_compatibility.json"
        write_json_atomic(c0_path, c0, mode=0o444)

        evaluation_matrix = build_kb_evaluation_matrix(design)
        evaluation_matrix_path = stage / "design/evaluation_matrix.json"
        write_json_atomic(evaluation_matrix_path, evaluation_matrix, mode=0o444)

        empty_pact = {"kind": "none"}
        microstate = with_digest(
            {
                "schema_version": MICROSTATE_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "state_id": "S000",
                "training_count": 0,
                "parent_state_id": None,
                "protocol_id": None,
                "adjudication": None,
                "pact": {
                    "kind": "none",
                    "digest": canonical_digest(empty_pact),
                },
                "catalog": _artifact_digest_record(
                    catalog_path,
                    relative_to=stage,
                    digest=catalog["catalog_digest"],
                ),
                "created_at": timestamp,
            },
            "microstate_digest",
        )
        _validate_generated(
            microstate, MICROSTATE_SCHEMA, "microstate_digest", "C0 microstate"
        )
        microstate_path = stage / "microstates/S000.json"
        write_json_atomic(microstate_path, microstate, mode=0o444)
        shutil.copy2(microstate_path, stage / "checkpoints/C0/microstate.json")
        checkpoint = with_digest(
            {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "label": "C0",
                "training_count": 0,
                "microstate": _artifact_digest_record(
                    microstate_path,
                    relative_to=stage,
                    digest=microstate["microstate_digest"],
                ),
                "catalog": _artifact_digest_record(
                    catalog_path,
                    relative_to=stage,
                    digest=catalog["catalog_digest"],
                ),
                "pact": microstate["pact"],
                "validation_protocol_ids": list(design["split"]["validation"]),
                "test_protocol_ids": list(design["split"]["test"]),
                "created_at": timestamp,
            },
            "checkpoint_digest",
        )
        _validate_generated(
            checkpoint,
            CHECKPOINT_SCHEMA,
            "checkpoint_digest",
            "C0 KB checkpoint",
        )
        write_json_atomic(
            stage / "checkpoints/C0/checkpoint.json", checkpoint, mode=0o444
        )

        repository_record = _repository_record(repository)
        manifest = with_digest(
            {
                "schema_version": EXPERIMENT_SCHEMA_VERSION,
                "experiment_id": design["experiment_id"],
                "created_at": timestamp,
                "design": _artifact_digest_record(
                    stage / "design/design.json",
                    relative_to=stage,
                    digest=design["design_digest"],
                ),
                "repository": repository_record,
                "private_roots": {
                    "groundtruth_root": truth_root.as_posix(),
                    "source_manifest_search_root": source_search.as_posix(),
                    "baseline_root": baseline.as_posix(),
                    "run_root": run_root.expanduser().resolve().as_posix(),
                },
                "universe": universe,
                "identity_map": _artifact_digest_record(
                    identity_path,
                    relative_to=stage,
                    digest=identity["identity_map_digest"],
                ),
                "c0_registry": _artifact_digest_record(
                    c0_path,
                    relative_to=stage,
                    digest=c0["registry_digest"],
                ),
                "evaluation_matrix": _artifact_digest_record(
                    evaluation_matrix_path,
                    relative_to=stage,
                    digest=evaluation_matrix["matrix_digest"],
                ),
                "current_microstate": _artifact_digest_record(
                    microstate_path,
                    relative_to=stage,
                    digest=microstate["microstate_digest"],
                ),
                "final_lock": False,
            },
            "experiment_digest",
        )
        _validate_generated(
            manifest,
            EXPERIMENT_SCHEMA,
            "experiment_digest",
            "KB experiment manifest",
        )
        write_json_atomic(stage / "experiment.json", manifest, mode=0o444)
        _make_design_read_only(stage / "design")
        os.replace(stage, target)
        return validate_kb_experiment(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def validate_kb_experiment(experiment_root: Path) -> dict[str, Any]:
    root = _required_directory(experiment_root, "KB experiment root")
    manifest = _load_validated(
        root / "experiment.json",
        schema=EXPERIMENT_SCHEMA,
        digest_field="experiment_digest",
        label="KB experiment manifest",
    )
    design = _load_validated(
        root / manifest["design"]["path"],
        schema=DESIGN_SCHEMA,
        digest_field="design_digest",
        label="KB experiment design",
    )
    validate_kb_design(design)
    _assert_artifact(root, manifest["design"], design["design_digest"])
    identity = _load_validated(
        root / manifest["identity_map"]["path"],
        schema=IDENTITY_SCHEMA,
        digest_field="identity_map_digest",
        label="KB identity map",
    )
    _assert_artifact(root, manifest["identity_map"], identity["identity_map_digest"])
    c0 = _load_validated(
        root / manifest["c0_registry"]["path"],
        schema=C0_SCHEMA,
        digest_field="registry_digest",
        label="C0 compatibility registry",
    )
    _assert_artifact(root, manifest["c0_registry"], c0["registry_digest"])
    matrix = _load_validated(
        root / manifest["evaluation_matrix"]["path"],
        schema=EVALUATION_MATRIX_SCHEMA,
        digest_field="matrix_digest",
        label="KB evaluation matrix",
    )
    _assert_artifact(root, manifest["evaluation_matrix"], matrix["matrix_digest"])
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="current KB microstate",
    )
    _assert_artifact(root, manifest["current_microstate"], state["microstate_digest"])
    catalog = _load_validated(
        root / state["catalog"]["path"],
        schema=CATALOG_SCHEMA,
        digest_field="catalog_digest",
        label="current KB catalog",
    )
    _assert_artifact(root, state["catalog"], catalog["catalog_digest"])
    if catalog["training_count"] != state["training_count"]:
        raise CapabilityImprovementError(
            "current KB catalog and microstate counts disagree"
        )
    if state["pact"]["kind"] == "learned":
        pact_path = root / state["pact"]["path"]
        from .kb_orchestrator import validate_kb_pact

        pack = validate_kb_pact(pact_path)
        if pack["pack_digest"] != state["pact"]["digest"]:
            raise CapabilityImprovementError("current KB pact digest changed")
    return manifest


def fork_kb_behavioral_feedback_lineage(
    *,
    source_experiment_root: Path,
    output_root: Path,
    run_root: Path,
    repository_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Fork the immutable C5 substrate without copying 1.5 GB of task inputs.

    The fork shares only the hash-registered task bundles.  C5's pact, five
    canonical card pairs, catalogs, reviews, and checkpoint are copied into an
    independent experiment root; later states therefore cannot collide with or
    mutate the superseded baseline-reuse lineage.
    """

    source = _required_directory(source_experiment_root, "source KB experiment")
    source_manifest = validate_kb_experiment(source)
    design = _load_validated(
        source / source_manifest["design"]["path"],
        schema=DESIGN_SCHEMA,
        digest_field="design_digest",
        label="source KB design",
    )
    checkpoint = _load_validated(
        source / "checkpoints/C5/checkpoint.json",
        schema=CHECKPOINT_SCHEMA,
        digest_field="checkpoint_digest",
        label="source C5 checkpoint",
    )
    state = _load_validated(
        source / "microstates/S005.json",
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="source S005 microstate",
    )
    if checkpoint["training_count"] != 5 or state["training_count"] != 5:
        raise CapabilityImprovementError("behavioral fork requires a complete C5 seed")
    registry = _load_validated(
        source / "runtime/task_registry.json",
        schema=TASK_REGISTRY_SCHEMA,
        digest_field="registry_digest",
        label="source task registry",
    )
    if registry["design_digest"] != design["design_digest"]:
        raise CapabilityImprovementError("source task registry belongs to another design")
    # The source registry was already built from a complete task-tree hash pass.
    # Re-reading 1.5 GB here adds no new contract; plans pin the registry's
    # per-task digests and Harbor consumes those same registered directories.
    for entry in registry["entries"]:
        _required_directory(Path(entry["path"]), "registered task")

    target = output_root.expanduser().resolve()
    reject_private_output_in_repository(target)
    if target.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite behavioral KB experiment: {target}"
        )
    repository = _required_directory(repository_root, "repository root")
    timestamp = normalize_timestamp(created_at)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.behavioral-stage-", dir=target.parent)
    )

    def copy_file(relative: str) -> None:
        source_path = source / relative
        destination = stage / relative
        if not source_path.is_file() or source_path.is_symlink():
            raise CapabilityImprovementError(
                f"behavioral seed artifact is missing or unsafe: {source_path}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha256_file(destination) != sha256_file(source_path):
                raise CapabilityImprovementError(
                    f"behavioral seed artifact collision: {relative}"
                )
            return
        shutil.copy2(source_path, destination)

    def copy_tree(relative: str) -> None:
        source_path = source / relative
        destination = stage / relative
        if destination.exists():
            return
        shutil.copytree(source_path, destination, copy_function=shutil.copy2)

    try:
        copy_tree("design")
        copy_file(source_manifest["identity_map"]["path"])
        copy_file("runtime/task_registry.json")
        for ordinal in range(0, 6):
            state_relative = f"microstates/S{ordinal:03d}.json"
            copy_file(state_relative)
            seeded_state = load_json_object(
                source / state_relative, label=f"seed S{ordinal:03d} microstate"
            )
            copy_file(seeded_state["catalog"]["path"])
            adjudication = seeded_state.get("adjudication")
            if isinstance(adjudication, Mapping):
                copy_file(adjudication["path"])
            pact = seeded_state.get("pact", {})
            if pact.get("kind") == "learned":
                copy_tree(pact["path"])

        seeded_catalog = _load_validated(
            source / state["catalog"]["path"],
            schema=CATALOG_SCHEMA,
            digest_field="catalog_digest",
            label="source C5 catalog",
        )
        for entry in seeded_catalog["entries"]:
            for field in ("oligo_card", "graph_card", "mechanism_summary"):
                copy_file(entry[field]["path"])
        copy_tree("checkpoints/C0")
        copy_tree("checkpoints/C5")
        for relative in (
            "knowledge/cards",
            "knowledge/catalogs",
            "microstates",
            "reviews",
            "pacts",
            "checkpoints",
            "runtime",
            "lineage",
        ):
            (stage / relative).mkdir(parents=True, exist_ok=True)

        manifest = copy.deepcopy(source_manifest)
        manifest["experiment_id"] = f"{design['experiment_id']}-behavioral-feedback-v2"
        manifest["created_at"] = timestamp
        manifest["repository"] = _repository_record(repository)
        manifest["private_roots"]["run_root"] = run_root.expanduser().resolve().as_posix()
        manifest["current_microstate"] = _artifact_digest_record(
            stage / "microstates/S005.json",
            relative_to=stage,
            digest=state["microstate_digest"],
        )
        manifest["final_lock"] = False
        manifest.pop("experiment_digest", None)
        manifest = with_digest(manifest, "experiment_digest")
        _validate_generated(
            manifest,
            EXPERIMENT_SCHEMA,
            "experiment_digest",
            "behavioral KB experiment manifest",
        )
        write_json_atomic(stage / "experiment.json", manifest, mode=0o444)

        lineage = with_digest(
            {
                "schema_version": BEHAVIORAL_LINEAGE_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "source_experiment": {
                    "path": source.as_posix(),
                    "experiment_digest": source_manifest["experiment_digest"],
                },
                "parent_checkpoint": "C5",
                "parent_checkpoint_digest": checkpoint["checkpoint_digest"],
                "parent_state_id": "S005",
                "parent_microstate_digest": state["microstate_digest"],
                "retrieval_policy": RETRIEVAL_POLICY_V3,
                "training_generation": (
                    "harbor_previous_checkpoint_pact_prior_only_kb"
                ),
                "knowledge_admission": (
                    "canonical_groundtruth_cards_after_whole_batch"
                ),
                "pact_update": "one_cumulative_update_after_whole_batch",
                "validation_feedback": "aggregate_evaluation_only_no_rule_learning",
                "test_evaluation": (
                    "validation_selected_checkpoint_only_after_all_validation"
                ),
                "task_registry": _artifact_digest_record(
                    stage / "runtime/task_registry.json",
                    relative_to=stage,
                    digest=registry["registry_digest"],
                ),
                "created_at": timestamp,
            },
            "lineage_digest",
        )
        _validate_generated(
            lineage,
            BEHAVIORAL_LINEAGE_SCHEMA,
            "lineage_digest",
            "behavioral KB lineage",
        )
        write_json_atomic(stage / "lineage/behavioral.json", lineage, mode=0o444)
        _make_design_read_only(stage / "design")
        os.replace(stage, target)
        validate_kb_experiment(target)
        return lineage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def build_kb_evaluation_matrix(design: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the exact 60 validation and 120 test cells before learning."""

    cells: dict[str, list[dict[str, str]]] = {"validation": [], "test": []}
    for checkpoint in design["learned_checkpoints"]:
        label = checkpoint["label"]
        for panel in ("validation", "test"):
            for protocol_id in design["split"][panel]:
                cells[panel].append(
                    {
                        "cell_id": f"{panel}-{label}-{protocol_id}",
                        "checkpoint": label,
                        "protocol_id": protocol_id,
                        "panel": panel,
                    }
                )
    if len(cells["validation"]) != design["expected_counts"]["new_validation_cells"]:
        raise CapabilityImprovementError("validation matrix cell count is inconsistent")
    if len(cells["test"]) != design["expected_counts"]["new_test_cells"]:
        raise CapabilityImprovementError("test matrix cell count is inconsistent")
    result = with_digest(
        {
            "schema_version": EVALUATION_MATRIX_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "validation_cells": cells["validation"],
            "test_cells": cells["test"],
            "test_access": "sealed_until_final_lock",
        },
        "matrix_digest",
    )
    _validate_generated(
        result,
        EVALUATION_MATRIX_SCHEMA,
        "matrix_digest",
        "KB evaluation matrix",
    )
    return result


def register_kb_tasks(*, experiment_root: Path, tasks_root: Path) -> dict[str, Any]:
    """Hash task-specific bytes once so online steps avoid repeated tree walks."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    tasks = _required_directory(tasks_root, "generated task root")
    output = root / "runtime" / "task_registry.json"
    if output.exists():
        registry = _load_validated(
            output,
            schema=TASK_REGISTRY_SCHEMA,
            digest_field="registry_digest",
            label="KB task registry",
        )
        if registry["tasks_root"] != tasks.as_posix():
            raise CapabilityImprovementError(
                "existing task registry belongs to another task root"
            )
        return registry
    entries = []
    for protocol_id in design["protocol_ids"]:
        task_root = tasks / protocol_id
        if not task_root.is_dir() or task_root.is_symlink():
            raise CapabilityImprovementError(
                f"generated task is missing or unsafe: {task_root}"
            )
        input_manifest = load_json_object(
            task_root / "input_manifest.json",
            label=f"{protocol_id} task input manifest",
        )
        source_manifest = load_json_object(
            root / "design/source_manifests" / f"{protocol_id}.json",
            label=f"{protocol_id} audit input manifest",
        )
        if _normalized_source_rows(input_manifest) != _normalized_source_rows(
            source_manifest
        ):
            raise CapabilityImprovementError(
                f"generated task source inventory differs for {protocol_id}"
            )
        files = _task_contract_files(task_root)
        rows = [
            {
                "path": path.relative_to(task_root).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in files
        ]
        entries.append(
            {
                "protocol_id": protocol_id,
                "path": task_root.as_posix(),
                "file_count": len(rows),
                "tree_digest": canonical_digest(rows),
                "agent_visible_fingerprint": _agent_visible_fingerprint(task_root),
            }
        )
    registry = with_digest(
        {
            "schema_version": TASK_REGISTRY_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "tasks_root": tasks.as_posix(),
            "entries": entries,
        },
        "registry_digest",
    )
    _validate_generated(
        registry,
        TASK_REGISTRY_SCHEMA,
        "registry_digest",
        "KB task registry",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, registry, mode=0o444)
    return registry


def _registered_tasks_root(root: Path) -> tuple[Path, dict[str, Any]]:
    registry = _load_validated(
        root / "runtime/task_registry.json",
        schema=TASK_REGISTRY_SCHEMA,
        digest_field="registry_digest",
        label="KB task registry",
    )
    parents = {
        Path(entry["path"]).expanduser().resolve().parent
        for entry in registry["entries"]
    }
    if len(parents) != 1:
        raise CapabilityImprovementError(
            "registered tasks do not share one Harbor dataset root"
        )
    tasks_root = _required_directory(parents.pop(), "registered task root")
    return tasks_root, registry


def plan_next_kb_training_run(
    *,
    experiment_root: Path,
    created_at: str,
    training_ordinal: int | None = None,
    batch_parent_state_id: str | None = None,
) -> dict[str, Any]:
    """Pin a protocol's preserved C0 trial without rerunning T2/T3.

    The optional ordinal is restricted to the next checkpoint-bounded batch and
    must share its immutable parent state.  This lets expensive adjudications
    run concurrently while admissions remain ordered.
    """

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="current KB microstate",
    )
    ordinal = (
        state["training_count"] + 1
        if training_ordinal is None
        else int(training_ordinal)
    )
    plan_state = state
    if training_ordinal is not None:
        parent_path = root / "microstates" / f"{batch_parent_state_id}.json"
        if batch_parent_state_id is None or not parent_path.is_file():
            raise CapabilityImprovementError(
                "batch training plan lacks its immutable parent state"
            )
        plan_state = _load_validated(
            parent_path,
            schema=MICROSTATE_SCHEMA,
            digest_field="microstate_digest",
            label="batch parent microstate",
        )
        if not plan_state["training_count"] <= state["training_count"]:
            raise CapabilityImprovementError(
                "batch parent is ahead of the current training state"
            )
        batch_end = next(
            item["after_training_count"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] > plan_state["training_count"]
        )
        if not plan_state["training_count"] < ordinal <= batch_end:
            raise CapabilityImprovementError(
                "batch training ordinal lies outside the next checkpoint batch"
            )
    if ordinal > len(design["split"]["training"]):
        raise CapabilityImprovementError("all KB training protocols are complete")
    protocol_id = design["split"]["training"][ordinal - 1]
    run_root = Path(str(manifest["private_roots"]["run_root"]))
    plan_root = (
        run_root / "plans" / "training" / f"S{ordinal:03d}" / "baseline-reuse"
    )
    if plan_root.exists():
        return _load_validated(
            plan_root / "plan.json",
            schema=BASELINE_TRAINING_PLAN_SCHEMA,
            digest_field="plan_digest",
            label="existing KB baseline-training plan",
        )
    c0 = _load_validated(
        root / manifest["c0_registry"]["path"],
        schema=C0_SCHEMA,
        digest_field="registry_digest",
        label="C0 compatibility registry",
    )
    entries = [
        item
        for item in c0["entries"]
        if item["protocol_id"] == protocol_id and item["panel"] == "training"
    ]
    if len(entries) != 1 or entries[0]["status"] != "compatible":
        raise CapabilityImprovementError(
            f"next training protocol lacks one compatible pinned C0 trial: {protocol_id}"
        )
    entry = entries[0]
    baseline_trial = entry.get("baseline_trial")
    if not isinstance(baseline_trial, Mapping):
        raise CapabilityImprovementError(
            "C0 registry predates hash-pinned training baseline support"
        )
    _validate_baseline_trial_pin(baseline_trial)
    plan_root.mkdir(parents=True)
    plan = with_digest(
        {
            "schema_version": BASELINE_TRAINING_PLAN_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "state_id": plan_state["state_id"],
            "training_ordinal": ordinal,
            "protocol_id": protocol_id,
            "pact_digest": plan_state["pact"]["digest"],
            "baseline_registry_digest": c0["registry_digest"],
            "baseline_condition": entry["baseline_condition"],
            "baseline_trial": dict(baseline_trial),
            "repair_policy": "preserve_original_prediction_no_repair",
            "created_at": normalize_timestamp(created_at),
        },
        "plan_digest",
    )
    _validate_generated(
        plan,
        BASELINE_TRAINING_PLAN_SCHEMA,
        "plan_digest",
        "KB baseline-training plan",
    )
    write_json_atomic(plan_root / "plan.json", plan, mode=0o444)
    return plan


def plan_next_kb_behavioral_training_batch(
    *,
    experiment_root: Path,
    base_config_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan one concurrent batch generated under the preceding checkpoint."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        schema=DESIGN_SCHEMA,
        digest_field="design_digest",
        label="KB design",
    )
    lineage = _load_validated(
        root / "lineage/behavioral.json",
        schema=BEHAVIORAL_LINEAGE_SCHEMA,
        digest_field="lineage_digest",
        label="behavioral KB lineage",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="batch-parent KB microstate",
    )
    parent_count = state["training_count"]
    if parent_count >= len(design["split"]["training"]):
        raise CapabilityImprovementError("all behavioral training batches are complete")
    next_spec = next(
        item
        for item in design["learned_checkpoints"]
        if item["after_training_count"] > parent_count
    )
    batch_end = next_spec["after_training_count"]
    previous_counts = {
        item["after_training_count"] for item in design["learned_checkpoints"]
    }
    if parent_count not in previous_counts:
        raise CapabilityImprovementError(
            "behavioral batch planning is allowed only at a frozen checkpoint"
        )
    parent_label = f"C{parent_count}"
    parent_checkpoint = _load_validated(
        root / "checkpoints" / parent_label / "checkpoint.json",
        schema=CHECKPOINT_SCHEMA,
        digest_field="checkpoint_digest",
        label=f"{parent_label} parent checkpoint",
    )
    ordinals = list(range(parent_count + 1, batch_end + 1))
    protocols = [design["split"]["training"][item - 1] for item in ordinals]
    run_root = Path(str(manifest["private_roots"]["run_root"]))
    plan_root = (
        run_root
        / "plans/training-batches"
        / f"{parent_label}-to-{next_spec['label']}"
    )
    if plan_root.exists():
        existing = _load_validated(
            plan_root / "plan.json",
            schema=TRAINING_BATCH_PLAN_SCHEMA,
            digest_field="plan_digest",
            label="existing behavioral training-batch plan",
        )
        _validate_knowledge_runtime_contract(existing)
        return existing
    plan_root.mkdir(parents=True)
    exposure, instruction_path = _prepare_checkpoint_exposure(
        root=root,
        manifest=manifest,
        design=design,
        checkpoint=parent_checkpoint,
        protocol_ids=protocols,
        run_root=run_root,
    )
    config = load_json_object(base_config_path, label="base Harbor config")
    _validate_kb_base_config(config, design)
    config = copy.deepcopy(config)
    _force_local_codex_subscription(config)
    job_suffix = f"{parent_label.lower()}-to-{next_spec['label'].lower()}"
    config["job_name"] = f"agent-guided-kb-behavioral-v2-{job_suffix}-training"
    config["jobs_dir"] = (run_root / "training").as_posix()
    concurrency = len(protocols)
    config["n_concurrent_trials"] = concurrency
    tasks_root, registry = _registered_tasks_root(root)
    registry_entries = {item["protocol_id"]: item for item in registry["entries"]}
    if any(protocol_id not in registry_entries for protocol_id in protocols):
        raise CapabilityImprovementError("training batch lacks registered tasks")
    config["datasets"][0]["path"] = tasks_root.as_posix()
    config["datasets"][0]["task_names"] = protocols
    config["retry"] = {"max_retries": 0}
    timeout = max(
        design["execution"]["slow_protocol_timeout_seconds"]
        if protocol_id in design["execution"]["slow_protocol_ids"]
        else design["execution"]["default_timeout_seconds"]
        for protocol_id in protocols
    )
    config["agent_timeout_multiplier"] = round(timeout / 3600, 6)
    mounts = [_read_only_mount(exposure, "/workspace/knowledge_base")]
    if state["pact"]["kind"] == "learned":
        mounts.append(
            _read_only_mount(root / state["pact"]["path"], "/workspace/capability_pack")
        )
    config["environment"] = {"mounts": mounts}
    config["extra_instruction_paths"] = [instruction_path.as_posix()]
    config_path = plan_root / "harbor.json"
    write_json_atomic(config_path, config, mode=0o444)
    plan = with_digest(
        {
            "schema_version": TRAINING_BATCH_PLAN_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "lineage_digest": lineage["lineage_digest"],
            "parent_state_id": state["state_id"],
            "parent_microstate_digest": state["microstate_digest"],
            "parent_checkpoint": parent_label,
            "next_checkpoint": next_spec["label"],
            "training_ordinals": ordinals,
            "protocol_ids": protocols,
            "task_tree_digests": [
                {
                    "protocol_id": protocol_id,
                    "tree_digest": registry_entries[protocol_id]["tree_digest"],
                }
                for protocol_id in protocols
            ],
            "pact_digest": state["pact"]["digest"],
            "catalog_digest": parent_checkpoint["catalog"]["digest"],
            "retrieval_policy": RETRIEVAL_POLICY_V3,
            "knowledge_runtime": _knowledge_runtime_contract(
                exposure=exposure,
                instruction_path=instruction_path,
                retrieval_policy=RETRIEVAL_POLICY_V3,
            ),
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "config": {
                "path": config_path.as_posix(),
                "sha256": sha256_file(config_path),
            },
            "created_at": normalize_timestamp(created_at),
        },
        "plan_digest",
    )
    _validate_generated(
        plan,
        TRAINING_BATCH_PLAN_SCHEMA,
        "plan_digest",
        "behavioral training-batch plan",
    )
    _validate_knowledge_runtime_contract(plan)
    write_json_atomic(plan_root / "plan.json", plan, mode=0o444)
    return plan


def plan_kb_c0_backfill(
    *, experiment_root: Path, base_config_path: Path, created_at: str
) -> dict[str, Any]:
    """Plan only the fixed-panel C0 cells absent from the compatible baseline."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    c0 = _load_validated(
        root / manifest["c0_registry"]["path"],
        schema=C0_SCHEMA,
        digest_field="registry_digest",
        label="C0 compatibility registry",
    )
    protocols = list(c0["summary"]["backfill_protocol_ids"])
    base_config = load_json_object(base_config_path, label="base Harbor config")
    _validate_kb_base_config(base_config, design)
    run_root = Path(str(manifest["private_roots"]["run_root"]))
    plan_root = run_root / "plans" / "c0-backfill"
    if plan_root.exists():
        return _load_validated(
            plan_root / "plan.json",
            schema=C0_BACKFILL_PLAN_SCHEMA,
            digest_field="plan_digest",
            label="existing C0 backfill plan",
        )
    plan_root.mkdir(parents=True)
    config_record = None
    timeout = max(
        (
            design["execution"]["slow_protocol_timeout_seconds"]
            if protocol_id in design["execution"]["slow_protocol_ids"]
            else design["execution"]["default_timeout_seconds"]
            for protocol_id in protocols
        ),
        default=design["execution"]["default_timeout_seconds"],
    )
    concurrency = min(len(protocols), design["execution"]["test_concurrency"])
    if protocols:
        register_kb_tasks(experiment_root=root, tasks_root=root / "tasks")
        config = copy.deepcopy(base_config)
        _force_local_codex_subscription(config)
        config["job_name"] = "agent-guided-kb-v1-c0-backfill"
        config["jobs_dir"] = (run_root / "c0-backfill").as_posix()
        config["n_concurrent_trials"] = concurrency
        config["datasets"][0]["path"] = (root / "tasks").as_posix()
        config["datasets"][0]["task_names"] = protocols
        config["retry"] = {"max_retries": 0}
        config["agent_timeout_multiplier"] = round(timeout / 3600, 6)
        config_path = plan_root / "harbor.json"
        write_json_atomic(config_path, config, mode=0o444)
        config_record = {
            "path": config_path.as_posix(),
            "sha256": sha256_file(config_path),
        }
    plan = with_digest(
        {
            "schema_version": C0_BACKFILL_PLAN_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "protocol_ids": protocols,
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "config": config_record,
            "created_at": normalize_timestamp(created_at),
        },
        "plan_digest",
    )
    _validate_generated(
        plan,
        C0_BACKFILL_PLAN_SCHEMA,
        "plan_digest",
        "C0 backfill plan",
    )
    write_json_atomic(plan_root / "plan.json", plan, mode=0o444)
    return plan


def plan_kb_checkpoint_evaluation(
    *,
    experiment_root: Path,
    checkpoint_label: str,
    panel: str,
    base_config_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan one concurrent 5- or 10-protocol checkpoint evaluation job."""

    if panel not in {"validation", "test"}:
        raise CapabilityImprovementError("evaluation panel must be validation or test")
    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    if panel == "test" and not manifest["final_lock"]:
        raise CapabilityImprovementError(
            "test evaluation remains sealed until the final KB lock"
        )
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    checkpoint_path = root / "checkpoints" / checkpoint_label / "checkpoint.json"
    checkpoint = _load_validated(
        checkpoint_path,
        schema=CHECKPOINT_SCHEMA,
        digest_field="checkpoint_digest",
        label=f"{checkpoint_label} KB checkpoint",
    )
    if checkpoint["design_digest"] != design["design_digest"]:
        raise CapabilityImprovementError("checkpoint belongs to another KB design")
    protocols = list(design["split"][panel])
    run_root = Path(str(manifest["private_roots"]["run_root"]))
    plan_root = run_root / "plans" / "evaluation" / checkpoint_label / panel
    if plan_root.exists():
        existing = _load_validated(
            plan_root / "plan.json",
            schema=EVALUATION_PLAN_SCHEMA,
            digest_field="plan_digest",
            label="existing KB evaluation plan",
        )
        _validate_knowledge_runtime_contract(existing)
        return existing
    plan_root.mkdir(parents=True)
    exposure, instruction_path = _prepare_checkpoint_exposure(
        root=root,
        manifest=manifest,
        design=design,
        checkpoint=checkpoint,
        protocol_ids=protocols,
        run_root=run_root,
    )
    config = load_json_object(base_config_path, label="base Harbor config")
    _validate_kb_base_config(config, design)
    config = copy.deepcopy(config)
    _force_local_codex_subscription(config)
    config["job_name"] = f"agent-guided-kb-v1-{checkpoint_label.lower()}-{panel}"
    config["jobs_dir"] = (run_root / "evaluation" / panel).as_posix()
    concurrency_key = f"{panel}_concurrency"
    concurrency = min(len(protocols), design["execution"][concurrency_key])
    config["n_concurrent_trials"] = concurrency
    tasks_root, _ = _registered_tasks_root(root)
    config["datasets"][0]["path"] = tasks_root.as_posix()
    config["datasets"][0]["task_names"] = protocols
    config["retry"] = _evaluation_retry_policy(panel)
    timeout = max(
        (
            design["execution"]["slow_protocol_timeout_seconds"]
            if protocol_id in design["execution"]["slow_protocol_ids"]
            else design["execution"]["default_timeout_seconds"]
            for protocol_id in protocols
        )
    )
    config["agent_timeout_multiplier"] = round(timeout / 3600, 6)
    mounts = [_read_only_mount(exposure, "/workspace/knowledge_base")]
    if checkpoint["pact"]["kind"] == "learned":
        mounts.append(
            _read_only_mount(
                root / checkpoint["pact"]["path"], "/workspace/capability_pack"
            )
        )
    config["environment"] = {"mounts": mounts}
    config["extra_instruction_paths"] = [instruction_path.as_posix()]
    # Harbor already collects /logs/artifacts recursively by convention. Adding
    # nested files here creates redundant artifact-collision warnings.
    config_path = plan_root / "harbor.json"
    write_json_atomic(config_path, config, mode=0o444)
    lineage_path = root / "lineage/behavioral.json"
    lineage_fields: dict[str, str] = {}
    if lineage_path.is_file():
        lineage = _load_validated(
            lineage_path,
            schema=BEHAVIORAL_LINEAGE_SCHEMA,
            digest_field="lineage_digest",
            label="behavioral KB lineage",
        )
        lineage_fields["lineage_digest"] = lineage["lineage_digest"]
    plan = with_digest(
        {
            "schema_version": EVALUATION_PLAN_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            **lineage_fields,
            "checkpoint": checkpoint_label,
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "panel": panel,
            "protocol_ids": protocols,
            "retrieval_policy": RETRIEVAL_POLICY,
            "knowledge_runtime": _knowledge_runtime_contract(
                exposure=exposure,
                instruction_path=instruction_path,
            ),
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "test_authorized": panel == "test" and manifest["final_lock"],
            "config": {
                "path": config_path.as_posix(),
                "sha256": sha256_file(config_path),
            },
            "created_at": normalize_timestamp(created_at),
        },
        "plan_digest",
    )
    _validate_generated(
        plan,
        EVALUATION_PLAN_SCHEMA,
        "plan_digest",
        "KB evaluation run plan",
    )
    _validate_knowledge_runtime_contract(plan)
    write_json_atomic(plan_root / "plan.json", plan, mode=0o444)
    return plan


def plan_kb_composite_evaluation(
    *,
    experiment_root: Path,
    composite_endpoint_path: Path,
    panel: str,
    base_config_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan one evaluation using a selected pact and a later frozen KB.

    The composite is an explicit endpoint rather than a synthetic checkpoint:
    its pact remains the validation-selected coherent package, while its
    catalog is the complete C58 ground-truth-only catalog.
    """

    if panel not in {"validation", "test"}:
        raise CapabilityImprovementError("evaluation panel must be validation or test")
    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    endpoint_path = composite_endpoint_path.expanduser().resolve()
    endpoint = _load_validated(
        endpoint_path,
        schema=COMPOSITE_ENDPOINT_SCHEMA,
        digest_field="endpoint_digest",
        label="KB composite endpoint",
    )
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    if endpoint["design_digest"] != design["design_digest"]:
        raise CapabilityImprovementError("composite endpoint belongs to another design")
    pact_label = endpoint["pact_checkpoint"]["label"]
    knowledge_label = endpoint["knowledge_checkpoint"]["label"]
    pact_checkpoint_path = root / "checkpoints" / pact_label / "checkpoint.json"
    knowledge_checkpoint_path = (
        root / "checkpoints" / knowledge_label / "checkpoint.json"
    )
    pact_checkpoint = _load_validated(
        pact_checkpoint_path,
        schema=CHECKPOINT_SCHEMA,
        digest_field="checkpoint_digest",
        label=f"{pact_label} pact checkpoint",
    )
    knowledge_checkpoint = _load_validated(
        knowledge_checkpoint_path,
        schema=CHECKPOINT_SCHEMA,
        digest_field="checkpoint_digest",
        label=f"{knowledge_label} knowledge checkpoint",
    )
    for checkpoint, reference, path, label in (
        (pact_checkpoint, endpoint["pact_checkpoint"], pact_checkpoint_path, "pact"),
        (
            knowledge_checkpoint,
            endpoint["knowledge_checkpoint"],
            knowledge_checkpoint_path,
            "knowledge",
        ),
    ):
        if (
            checkpoint["checkpoint_digest"] != reference["digest"]
            or sha256_file(path) != reference["sha256"]
            or (root / reference["path"]).resolve() != path.resolve()
        ):
            raise CapabilityImprovementError(
                f"composite {label} checkpoint reference changed"
            )
    if pact_checkpoint["pact"]["kind"] != "learned":
        raise CapabilityImprovementError("composite endpoint requires a learned pact")
    if pact_checkpoint["pact"] != endpoint["pact_checkpoint"]["pact"]:
        raise CapabilityImprovementError("composite pact reference changed")
    catalog_reference = endpoint["knowledge_checkpoint"]["catalog"]
    catalog_path = root / catalog_reference["path"]
    if (
        knowledge_checkpoint["catalog"]["path"] != catalog_reference["path"]
        or knowledge_checkpoint["catalog"]["digest"] != catalog_reference["digest"]
        or sha256_file(catalog_path) != catalog_reference["sha256"]
    ):
        raise CapabilityImprovementError("composite catalog reference changed")
    if panel == "test":
        if not manifest["final_lock"]:
            raise CapabilityImprovementError(
                "test evaluation remains sealed until the final KB lock"
            )
        final_lock = _load_validated(
            root / "final_lock.json",
            schema=FINAL_LOCK_SCHEMA,
            digest_field="lock_digest",
            label="KB final lock",
        )
        endpoint_selection = final_lock.get("endpoint_selection") or {}
        if (
            final_lock.get("primary_endpoint_mode") != "composite"
            or (endpoint_selection.get("composite_endpoint") or {}).get("digest")
            != endpoint["endpoint_digest"]
        ):
            raise CapabilityImprovementError(
                "final lock does not authorize this composite endpoint"
            )

    protocols = list(design["split"][panel])
    run_root = Path(str(manifest["private_roots"]["run_root"]))
    endpoint_id = endpoint["endpoint_id"]
    plan_root = run_root / "plans/evaluation/composite" / endpoint_id / panel
    if plan_root.exists():
        existing = _load_validated(
            plan_root / "plan.json",
            schema=EVALUATION_PLAN_SCHEMA,
            digest_field="plan_digest",
            label="existing composite KB evaluation plan",
        )
        if (existing.get("composite_endpoint") or {}).get("digest") != endpoint[
            "endpoint_digest"
        ]:
            raise CapabilityImprovementError(
                "existing composite plan belongs to another endpoint"
            )
        _validate_knowledge_runtime_contract(existing)
        return existing
    plan_root.mkdir(parents=True)

    runtime_checkpoint = copy.deepcopy(knowledge_checkpoint)
    runtime_checkpoint["label"] = endpoint_id
    runtime_checkpoint["pact"] = copy.deepcopy(pact_checkpoint["pact"])
    exposure, instruction_path = _prepare_checkpoint_exposure(
        root=root,
        manifest=manifest,
        design=design,
        checkpoint=runtime_checkpoint,
        protocol_ids=protocols,
        run_root=run_root,
    )
    config = load_json_object(base_config_path, label="base Harbor config")
    _validate_kb_base_config(config, design)
    config = copy.deepcopy(config)
    _force_local_codex_subscription(config)
    config["job_name"] = f"agent-guided-kb-v1-{endpoint_id.lower()}-{panel}"
    config["jobs_dir"] = (run_root / "evaluation" / panel).as_posix()
    concurrency = min(len(protocols), design["execution"][f"{panel}_concurrency"])
    config["n_concurrent_trials"] = concurrency
    tasks_root, _ = _registered_tasks_root(root)
    config["datasets"][0]["path"] = tasks_root.as_posix()
    config["datasets"][0]["task_names"] = protocols
    config["retry"] = _evaluation_retry_policy(panel)
    timeout = max(
        (
            design["execution"]["slow_protocol_timeout_seconds"]
            if protocol_id in design["execution"]["slow_protocol_ids"]
            else design["execution"]["default_timeout_seconds"]
            for protocol_id in protocols
        )
    )
    config["agent_timeout_multiplier"] = round(timeout / 3600, 6)
    config["environment"] = {
        "mounts": [
            _read_only_mount(exposure, "/workspace/knowledge_base"),
            _read_only_mount(
                root / pact_checkpoint["pact"]["path"],
                "/workspace/capability_pack",
            ),
        ]
    }
    config["extra_instruction_paths"] = [instruction_path.as_posix()]
    config_path = plan_root / "harbor.json"
    write_json_atomic(config_path, config, mode=0o444)
    composite_reference = {
        "endpoint_id": endpoint_id,
        "path": endpoint_path.as_posix(),
        "sha256": sha256_file(endpoint_path),
        "digest": endpoint["endpoint_digest"],
        "pact_checkpoint": pact_label,
        "knowledge_checkpoint": knowledge_label,
    }
    lineage_fields: dict[str, str] = {}
    lineage_path = root / "lineage/behavioral.json"
    if lineage_path.is_file():
        lineage = _load_validated(
            lineage_path,
            schema=BEHAVIORAL_LINEAGE_SCHEMA,
            digest_field="lineage_digest",
            label="behavioral KB lineage",
        )
        lineage_fields["lineage_digest"] = lineage["lineage_digest"]
    plan = with_digest(
        {
            "schema_version": EVALUATION_PLAN_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            **lineage_fields,
            "checkpoint": pact_label,
            "checkpoint_digest": pact_checkpoint["checkpoint_digest"],
            "composite_endpoint": composite_reference,
            "panel": panel,
            "protocol_ids": protocols,
            "retrieval_policy": RETRIEVAL_POLICY,
            "knowledge_runtime": _knowledge_runtime_contract(
                exposure=exposure,
                instruction_path=instruction_path,
            ),
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "test_authorized": panel == "test" and manifest["final_lock"],
            "config": {
                "path": config_path.as_posix(),
                "sha256": sha256_file(config_path),
            },
            "created_at": normalize_timestamp(created_at),
        },
        "plan_digest",
    )
    _validate_generated(
        plan,
        EVALUATION_PLAN_SCHEMA,
        "plan_digest",
        "composite KB evaluation run plan",
    )
    _validate_knowledge_runtime_contract(plan)
    write_json_atomic(plan_root / "plan.json", plan, mode=0o444)
    return plan


def _evaluation_retry_policy(panel: str) -> dict[str, Any]:
    """Retry only test-cell infrastructure exceptions, never scored invalid output."""

    if panel == "test":
        return {
            "max_retries": 2,
            "include_exceptions": ["RuntimeError"],
            "min_wait_sec": 5.0,
            "max_wait_sec": 30.0,
        }
    return {"max_retries": 0}


def lock_kb_final(
    *,
    experiment_root: Path,
    created_at: str,
    primary_endpoint_mode: str = "coupled_checkpoint",
    endpoint_selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Lock all learned checkpoints and the validation-selected primary endpoint."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    if manifest["final_lock"]:
        existing = _load_validated(
            root / "final_lock.json",
            schema=FINAL_LOCK_SCHEMA,
            digest_field="lock_digest",
            label="KB final lock",
        )
        if existing.get("primary_endpoint_mode", "coupled_checkpoint") != (
            primary_endpoint_mode
        ):
            raise CapabilityImprovementError(
                "existing final lock authorizes another endpoint mode"
            )
        return existing
    if primary_endpoint_mode not in {
        "coupled_checkpoint",
        "composite",
        "coupled_checkpoint_fallback",
    }:
        raise CapabilityImprovementError("invalid final endpoint mode")
    if primary_endpoint_mode == "coupled_checkpoint" and endpoint_selection is not None:
        raise CapabilityImprovementError(
            "coupled checkpoint lock cannot carry composite endpoint selection"
        )
    if primary_endpoint_mode != "coupled_checkpoint" and endpoint_selection is None:
        raise CapabilityImprovementError(
            "composite endpoint decision is required for this final lock"
        )
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="final KB microstate",
    )
    if state["training_count"] != len(design["split"]["training"]):
        raise CapabilityImprovementError("cannot lock KB before 58 training protocols")
    selection_path = root / "validation_selection.json"
    selection = _load_validated(
        selection_path,
        schema=VALIDATION_SELECTION_SCHEMA,
        digest_field="selection_digest",
        label="KB validation checkpoint selection",
    )
    if selection["design_digest"] != design["design_digest"]:
        raise CapabilityImprovementError(
            "validation checkpoint selection belongs to another KB design"
        )
    checkpoints = []
    for item in design["learned_checkpoints"]:
        path = root / "checkpoints" / item["label"] / "checkpoint.json"
        checkpoint = _load_validated(
            path,
            schema=CHECKPOINT_SCHEMA,
            digest_field="checkpoint_digest",
            label=f"{item['label']} KB checkpoint",
        )
        checkpoints.append(
            {
                "label": item["label"],
                "digest": checkpoint["checkpoint_digest"],
                "sha256": sha256_file(path),
            }
        )
    matrix = _load_validated(
        root / manifest["evaluation_matrix"]["path"],
        schema=EVALUATION_MATRIX_SCHEMA,
        digest_field="matrix_digest",
        label="KB evaluation matrix",
    )
    endpoint_fields: dict[str, Any] = {"primary_endpoint_mode": primary_endpoint_mode}
    if endpoint_selection is not None:
        endpoint_fields["endpoint_selection"] = copy.deepcopy(dict(endpoint_selection))
    lock = with_digest(
        {
            "schema_version": FINAL_LOCK_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "final_checkpoint": "C58",
            "primary_checkpoint": selection["selected_checkpoint"],
            **endpoint_fields,
            "validation_selection": {
                "path": "validation_selection.json",
                "sha256": sha256_file(selection_path),
                "digest": selection["selection_digest"],
            },
            "checkpoint_digests": checkpoints,
            "evaluation_matrix_digest": matrix["matrix_digest"],
            "training_complete": True,
            "test_cells_authorized": len(matrix["test_cells"]),
            "created_at": normalize_timestamp(created_at),
        },
        "lock_digest",
    )
    _validate_generated(lock, FINAL_LOCK_SCHEMA, "lock_digest", "KB final lock")
    write_json_atomic(root / "final_lock.json", lock, mode=0o444)
    updated = copy.deepcopy(manifest)
    updated["final_lock"] = True
    updated.pop("experiment_digest", None)
    updated = with_digest(updated, "experiment_digest")
    _validate_generated(
        updated,
        EXPERIMENT_SCHEMA,
        "experiment_digest",
        "final-locked KB experiment",
    )
    write_json_atomic(root / "experiment.json", updated, mode=0o444)
    return lock


def compile_kb_training_adjudication(
    *,
    experiment_root: Path,
    draft_path: Path,
    output_path: Path,
    expected_training_ordinal: int | None = None,
    batch_parent_state_id: str | None = None,
) -> dict[str, Any]:
    """Compile a Codex draft while pinning every referenced artifact ourselves."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    state = load_json_object(
        root / manifest["current_microstate"]["path"], label="KB microstate"
    )
    if state["training_count"] >= len(design["split"]["training"]):
        raise CapabilityImprovementError("all KB training protocols are complete")
    expected_ordinal = (
        state["training_count"] + 1
        if expected_training_ordinal is None
        else int(expected_training_ordinal)
    )
    if expected_training_ordinal is not None:
        if batch_parent_state_id != state["state_id"]:
            raise CapabilityImprovementError(
                "batch adjudication does not match the current parent state"
            )
        batch_end = next(
            item["after_training_count"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] > state["training_count"]
        )
        if not state["training_count"] < expected_ordinal <= batch_end:
            raise CapabilityImprovementError(
                "batch adjudication ordinal lies outside the next checkpoint batch"
            )
    expected_protocol = design["split"]["training"][expected_ordinal - 1]
    draft = load_json_object(draft_path, label="KB adjudication draft")
    if draft.get("protocol_id") != expected_protocol:
        raise CapabilityImprovementError(
            f"next adjudication must cover {expected_protocol}"
        )
    if draft.get("training_ordinal") != expected_ordinal:
        raise CapabilityImprovementError(
            f"next adjudication ordinal must be {expected_ordinal}"
        )
    findings = copy.deepcopy(draft.get("findings"))
    if not isinstance(findings, list):
        raise CapabilityImprovementError("adjudication findings must be an array")
    _validate_adjudication_findings(findings)
    pact_decision = copy.deepcopy(draft.get("pact_decision"))
    if not isinstance(pact_decision, dict):
        raise CapabilityImprovementError("adjudication pact_decision must be an object")
    expected_critic = pact_decision.get("action") in {
        "accept_change",
        "reject_change",
    }
    if pact_decision.get("critic_required") is not expected_critic:
        raise CapabilityImprovementError(
            "critic_required must be true exactly when a pact change is proposed"
        )
    accepts = pact_decision.get("action") == "accept_change"
    if accepts != isinstance(pact_decision.get("candidate_pact_digest"), str):
        raise CapabilityImprovementError(
            "candidate_pact_digest is required exactly for an accepted pact change"
        )
    eligible = [item for item in findings if item["learning_eligible"]]
    if expected_critic and not eligible:
        raise CapabilityImprovementError(
            "a pact change requires at least one eligible consolidated root event"
        )
    raw_artifacts = draft.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise CapabilityImprovementError("adjudication artifacts must be an object")
    reviewer = copy.deepcopy(draft.get("reviewer"))
    if (
        not isinstance(reviewer, dict)
        or {
            "name": reviewer.get("name"),
            "model": reviewer.get("model"),
            "reasoning_effort": reviewer.get("reasoning_effort"),
        }
        != {
            "name": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
        }
        or not isinstance(reviewer.get("version"), str)
        or not reviewer["version"]
    ):
        raise CapabilityImprovementError(
            "adjudication reviewer must record the actual local Codex version"
        )
    required_artifacts = (
        "result",
        "t2_prediction",
        "t3_prediction",
        "trajectory",
        "verifier_details",
        "error_analysis",
        "comparison",
        "adjudicator_output",
        "adjudicator_transcript",
    )
    artifacts = {
        key: _external_artifact_record(raw_artifacts.get(key), label=key)
        for key in required_artifacts
    }
    critic_output_value = raw_artifacts.get("critic_output")
    critic_value = raw_artifacts.get("critic_transcript")
    synthetic_report_value = raw_artifacts.get("candidate_pact_synthetic_report")
    if expected_critic:
        artifacts["critic_output"] = _external_artifact_record(
            critic_output_value, label="critic_output"
        )
        artifacts["critic_transcript"] = _external_artifact_record(
            critic_value, label="critic_transcript"
        )
    elif critic_value is not None or critic_output_value is not None:
        raise CapabilityImprovementError(
            "critic artifacts are forbidden when no pact change is proposed"
        )
    else:
        artifacts["critic_output"] = None
        artifacts["critic_transcript"] = None
    if accepts:
        artifacts["candidate_pact_synthetic_report"] = _external_artifact_record(
            synthetic_report_value, label="candidate_pact_synthetic_report"
        )
        _validate_candidate_pact_report(
            Path(str(synthetic_report_value)).expanduser().resolve()
        )
    elif synthetic_report_value is not None:
        raise CapabilityImprovementError(
            "non-accepted pact must not carry a synthetic report"
        )
    else:
        artifacts["candidate_pact_synthetic_report"] = None
    excluded = [item for item in findings if not item["learning_eligible"]]
    unresolved = [
        item
        for item in findings
        if item["benchmark_validity"] == "uncertain"
        or item["source_recoverability"] == "uncertain"
        or item["attribution"] == "ambiguous"
    ]
    document = with_digest(
        {
            "schema_version": ADJUDICATION_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "protocol_id": expected_protocol,
            "training_ordinal": expected_ordinal,
            "reviewer": reviewer,
            "provenance": design["policies"]["review_provenance"],
            "artifacts": artifacts,
            "findings": findings,
            "summary": {
                "raw_findings": len(findings),
                "eligible_root_events": len(eligible),
                "excluded_findings": len(excluded),
                "unresolved_findings": len(unresolved),
            },
            "pact_decision": pact_decision,
            "completed_at": normalize_timestamp(str(draft.get("completed_at", ""))),
        },
        "decision_digest",
    )
    validate_kb_training_adjudication(document)
    destination = output_path.expanduser().resolve()
    if destination.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite KB adjudication: {destination}"
        )
    write_json_atomic(destination, document, mode=0o444)
    return document


def validate_kb_training_adjudication(
    document: Mapping[str, Any],
) -> None:
    _validate_generated(
        document,
        ADJUDICATION_SCHEMA,
        "decision_digest",
        "KB training adjudication",
    )
    _validate_adjudication_findings(document["findings"])
    summary = document["summary"]
    eligible = sum(item["learning_eligible"] for item in document["findings"])
    if summary["raw_findings"] != len(document["findings"]):
        raise CapabilityImprovementError("adjudication raw finding count is stale")
    if summary["eligible_root_events"] != eligible:
        raise CapabilityImprovementError("adjudication eligible finding count is stale")
    propose = document["pact_decision"]["action"] in {
        "accept_change",
        "reject_change",
    }
    if document["pact_decision"]["critic_required"] is not propose:
        raise CapabilityImprovementError("adjudication critic policy is inconsistent")
    accepts = document["pact_decision"]["action"] == "accept_change"
    if accepts != isinstance(document["pact_decision"]["candidate_pact_digest"], str):
        raise CapabilityImprovementError(
            "adjudication candidate pact digest policy is inconsistent"
        )
    if accepts != (
        document["artifacts"]["candidate_pact_synthetic_report"] is not None
    ):
        raise CapabilityImprovementError(
            "adjudication synthetic-report policy is inconsistent"
        )
    if accepts:
        _validate_candidate_pact_report(
            Path(str(document["artifacts"]["candidate_pact_synthetic_report"]["path"]))
            .expanduser()
            .resolve()
        )
    if propose and (
        document["artifacts"]["critic_transcript"] is None
        or document["artifacts"]["critic_output"] is None
    ):
        raise CapabilityImprovementError("pact proposal lacks an independent critic")
    if not propose and (
        document["artifacts"]["critic_transcript"] is not None
        or document["artifacts"]["critic_output"] is not None
    ):
        raise CapabilityImprovementError("no-change adjudication contains a critic")
    _validate_observation_partition(document)
    for label, artifact in document["artifacts"].items():
        if artifact is None:
            continue
        path = Path(str(artifact["path"])).expanduser().resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != artifact["sha256"]
        ):
            raise CapabilityImprovementError(
                f"adjudication artifact changed or is unavailable: {label}"
            )


def _validate_candidate_pact_report(path: Path) -> None:
    synthetic_report = load_json_object(path, label="candidate pact synthetic report")
    if (
        synthetic_report.get("schema_version")
        != "libstruct.libgen_capability_synthetic_suite_report.v1"
        or synthetic_report.get("status") != "pass"
        or synthetic_report.get("issues") != []
        or synthetic_report.get("case_count")
        != len(synthetic_report.get("executed_case_ids", []))
        or not synthetic_report.get("executed_case_ids")
    ):
        raise CapabilityImprovementError(
            "accepted pact lacks a passing complete synthetic report"
        )


def admit_kb_training_protocol(
    *,
    experiment_root: Path,
    adjudication_path: Path,
    created_at: str,
    pact_root: Path | None = None,
) -> dict[str, Any]:
    """Admit one reviewed protocol by validating only its delta and parent state."""

    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    current = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="current KB microstate",
    )
    ordinal = current["training_count"] + 1
    if ordinal > len(design["split"]["training"]):
        raise CapabilityImprovementError("all KB training protocols are complete")
    protocol_id = design["split"]["training"][ordinal - 1]
    adjudication = _load_validated(
        adjudication_path,
        schema=ADJUDICATION_SCHEMA,
        digest_field="decision_digest",
        label="KB training adjudication",
    )
    validate_kb_training_adjudication(adjudication)
    if (
        adjudication["design_digest"] != design["design_digest"]
        or adjudication["protocol_id"] != protocol_id
        or adjudication["training_ordinal"] != ordinal
    ):
        raise CapabilityImprovementError(
            "adjudication does not match the next frozen training protocol"
        )
    _verify_protocol_contract(root, manifest, protocol_id)
    identity = _load_validated(
        root / manifest["identity_map"]["path"],
        schema=IDENTITY_SCHEMA,
        digest_field="identity_map_digest",
        label="KB identity map",
    )
    identity_entry = next(
        item for item in identity["entries"] if item["training_ordinal"] == ordinal
    )
    exemplar_id = identity_entry["exemplar_id"]
    if identity_entry["protocol_id"] != protocol_id:
        raise CapabilityImprovementError("private KB identity map order changed")
    catalog = _load_validated(
        root / current["catalog"]["path"],
        schema=CATALOG_SCHEMA,
        digest_field="catalog_digest",
        label="parent KB catalog",
    )
    _assert_artifact(root, current["catalog"], catalog["catalog_digest"])
    if catalog["training_count"] != ordinal - 1:
        raise CapabilityImprovementError("parent KB catalog count is inconsistent")

    timestamp = normalize_timestamp(created_at)
    state_id = f"S{ordinal:03d}"
    created_paths: list[Path] = []
    manifest_updated = False
    try:
        review_dir = root / "reviews" / f"{ordinal:03d}-{protocol_id}"
        card_dir = root / "knowledge" / "cards" / f"{ordinal:03d}-{exemplar_id}"
        if review_dir.exists() or card_dir.exists():
            raise CapabilityImprovementError(
                f"KB training ordinal {ordinal} already has admitted artifacts"
            )
        review_dir.mkdir(parents=True)
        created_paths.append(review_dir)
        stable_adjudication = review_dir / "adjudication.json"
        shutil.copy2(adjudication_path.resolve(), stable_adjudication)
        stable_adjudication.chmod(0o444)

        oligo_card, graph_card, mechanism = build_protocol_cards(
            protocol_id=protocol_id,
            training_ordinal=ordinal,
            exemplar_id=exemplar_id,
            groundtruth_root=Path(manifest["private_roots"]["groundtruth_root"]),
        )
        card_dir.mkdir(parents=True)
        created_paths.append(card_dir)
        card_files = {
            "oligo_card": ("oligos.json", oligo_card, "card_digest"),
            "graph_card": ("graph.json", graph_card, "card_digest"),
            "mechanism_summary": ("mechanism.json", mechanism, "summary_digest"),
        }
        entry: dict[str, Any] = {
            "exemplar_id": exemplar_id,
            "training_ordinal": ordinal,
        }
        for key, (filename, document, digest_field) in card_files.items():
            path = card_dir / filename
            write_json_atomic(path, document, mode=0o444)
            entry[key] = _artifact_digest_record(
                path,
                relative_to=root,
                digest=document[digest_field],
            )

        pact = copy.deepcopy(current["pact"])
        proposes = adjudication["pact_decision"]["action"] == "accept_change"
        if proposes:
            if pact_root is None:
                raise CapabilityImprovementError(
                    "pact proposal admission requires --pact-root"
                )
            source_pact = pact_root.expanduser().resolve()
            from .kb_orchestrator import validate_kb_pact

            pack = validate_kb_pact(source_pact)
            if (
                pack["pack_digest"]
                != adjudication["pact_decision"]["candidate_pact_digest"]
            ):
                raise CapabilityImprovementError(
                    "candidate pact digest differs from the accepted adjudication"
                )
            destination = root / "pacts" / state_id
            shutil.copytree(source_pact, destination, copy_function=shutil.copy2)
            _make_design_read_only(destination)
            created_paths.append(destination)
            pact = {
                "kind": "learned",
                "path": destination.relative_to(root).as_posix(),
                "digest": pack["pack_digest"],
            }
        elif pact_root is not None:
            raise CapabilityImprovementError(
                "no-change adjudication must not supply a pact root"
            )

        next_catalog = with_digest(
            {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "identity_map_digest": identity["identity_map_digest"],
                "training_count": ordinal,
                "entries": [*catalog["entries"], entry],
            },
            "catalog_digest",
        )
        _validate_generated(
            next_catalog, CATALOG_SCHEMA, "catalog_digest", "next KB catalog"
        )
        catalog_path = root / "knowledge" / "catalogs" / f"{state_id}.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(catalog_path, next_catalog, mode=0o444)
        created_paths.append(catalog_path)

        next_state = with_digest(
            {
                "schema_version": MICROSTATE_SCHEMA_VERSION,
                "design_digest": design["design_digest"],
                "state_id": state_id,
                "training_count": ordinal,
                "parent_state_id": current["state_id"],
                "protocol_id": protocol_id,
                "adjudication": _artifact_digest_record(
                    stable_adjudication,
                    relative_to=root,
                    digest=adjudication["decision_digest"],
                ),
                "pact": pact,
                "catalog": _artifact_digest_record(
                    catalog_path,
                    relative_to=root,
                    digest=next_catalog["catalog_digest"],
                ),
                "created_at": timestamp,
            },
            "microstate_digest",
        )
        _validate_generated(
            next_state, MICROSTATE_SCHEMA, "microstate_digest", "next KB microstate"
        )
        state_path = root / "microstates" / f"{state_id}.json"
        write_json_atomic(state_path, next_state, mode=0o444)
        created_paths.append(state_path)

        updated_manifest = copy.deepcopy(manifest)
        updated_manifest["current_microstate"] = _artifact_digest_record(
            state_path,
            relative_to=root,
            digest=next_state["microstate_digest"],
        )
        updated_manifest.pop("experiment_digest", None)
        updated_manifest = with_digest(updated_manifest, "experiment_digest")
        _validate_generated(
            updated_manifest,
            EXPERIMENT_SCHEMA,
            "experiment_digest",
            "updated KB experiment manifest",
        )
        write_json_atomic(root / "experiment.json", updated_manifest, mode=0o444)
        manifest_updated = True
        checkpoint_label = _checkpoint_label(design, ordinal)
        if checkpoint_label is not None:
            freeze_kb_checkpoint(
                experiment_root=root,
                label=checkpoint_label,
                created_at=timestamp,
            )
        return kb_status(root)
    except Exception:
        if manifest_updated:
            write_json_atomic(root / "experiment.json", manifest, mode=0o444)
        for path in reversed(created_paths):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        raise


def freeze_kb_checkpoint(
    *, experiment_root: Path, label: str, created_at: str
) -> dict[str, Any]:
    root = _required_directory(experiment_root, "KB experiment root")
    manifest = validate_kb_experiment(root)
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        schema=MICROSTATE_SCHEMA,
        digest_field="microstate_digest",
        label="checkpoint KB microstate",
    )
    expected = _checkpoint_label(design, state["training_count"])
    if label != expected:
        raise CapabilityImprovementError(
            f"training count {state['training_count']} freezes {expected}, not {label}"
        )
    target = root / "checkpoints" / label
    if target.exists():
        existing = _load_validated(
            target / "checkpoint.json",
            schema=CHECKPOINT_SCHEMA,
            digest_field="checkpoint_digest",
            label=f"existing {label} checkpoint",
        )
        return existing
    catalog = _load_validated(
        root / state["catalog"]["path"],
        schema=CATALOG_SCHEMA,
        digest_field="catalog_digest",
        label=f"{label} KB catalog",
    )
    _validate_catalog_artifacts(root, catalog)
    checkpoint = with_digest(
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "label": label,
            "training_count": state["training_count"],
            "microstate": manifest["current_microstate"],
            "catalog": state["catalog"],
            "pact": state["pact"],
            "validation_protocol_ids": list(design["split"]["validation"]),
            "test_protocol_ids": list(design["split"]["test"]),
            "created_at": normalize_timestamp(created_at),
        },
        "checkpoint_digest",
    )
    _validate_generated(
        checkpoint, CHECKPOINT_SCHEMA, "checkpoint_digest", f"{label} checkpoint"
    )
    target.mkdir(parents=True)
    write_json_atomic(target / "checkpoint.json", checkpoint, mode=0o444)
    shutil.copy2(
        root / manifest["current_microstate"]["path"], target / "microstate.json"
    )
    (target / "microstate.json").chmod(0o444)
    return checkpoint


def _validate_adjudication_findings(findings: Sequence[Mapping[str, Any]]) -> None:
    observed_ids = [item.get("finding_id") for item in findings]
    expected_ids = [f"F{index:03d}" for index in range(1, len(findings) + 1)]
    if observed_ids != expected_ids:
        raise CapabilityImprovementError(
            "adjudication finding IDs must be consecutive in source order"
        )
    observation_ids = [
        observation_id
        for item in findings
        for observation_id in item.get("observation_ids", [])
    ]
    if len(observation_ids) != len(set(observation_ids)):
        raise CapabilityImprovementError(
            "each verifier observation must belong to exactly one root finding"
        )
    for item in findings:
        expected_eligible = (
            item.get("benchmark_validity") == "valid"
            and item.get("attribution") == "agent"
            and item.get("source_recoverability") == "recoverable"
            and item.get("consolidated_root_event") is True
        )
        if item.get("learning_eligible") is not expected_eligible:
            raise CapabilityImprovementError(
                f"{item.get('finding_id')} has an invalid learning-eligibility label"
            )
        if expected_eligible and not item.get("evidence_locators"):
            raise CapabilityImprovementError(
                f"{item.get('finding_id')} is eligible but has no source locator"
            )
        if expected_eligible and not item.get("trajectory_locators"):
            raise CapabilityImprovementError(
                f"{item.get('finding_id')} is eligible but has no trajectory locator"
            )
        if not expected_eligible and item.get("recommended_action") not in (
            "none",
            "knowledge_card_only",
        ):
            raise CapabilityImprovementError(
                f"{item.get('finding_id')} cannot change the procedural pact"
            )


def _validate_observation_partition(document: Mapping[str, Any]) -> None:
    error_analysis_path = (
        Path(str(document["artifacts"]["error_analysis"]["path"]))
        .expanduser()
        .resolve()
    )
    error_analysis = load_json_object(
        error_analysis_path, label="adjudication error analysis"
    )
    expected = {
        item["error_id"]
        for item in error_analysis.get("observations", [])
        if item.get("substantive") is True
    }
    observed = {
        observation_id
        for finding in document["findings"]
        for observation_id in finding["observation_ids"]
    }
    if observed != expected:
        raise CapabilityImprovementError(
            "adjudication findings do not exactly partition substantive verifier "
            f"observations; missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _external_artifact_record(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityImprovementError(
            f"adjudication artifact path is missing: {label}"
        )
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise CapabilityImprovementError(
            f"adjudication artifact is missing or unsafe: {path}"
        )
    return {"path": path.as_posix(), "sha256": sha256_file(path)}


def _verify_protocol_contract(
    root: Path, manifest: Mapping[str, Any], protocol_id: str
) -> None:
    entry = next(
        (
            item
            for item in manifest["universe"]["entries"]
            if item["protocol_id"] == protocol_id
        ),
        None,
    )
    if entry is None:
        raise CapabilityImprovementError(
            f"protocol is absent from universe: {protocol_id}"
        )
    source_manifest = root / entry["source_manifest_path"]
    if sha256_file(source_manifest) != entry["source_manifest_sha256"]:
        raise CapabilityImprovementError(f"source manifest changed for {protocol_id}")
    groundtruth_root = Path(str(manifest["private_roots"]["groundtruth_root"]))
    for task, record in entry["groundtruth"].items():
        path = groundtruth_root / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise CapabilityImprovementError(
                f"{protocol_id} {task} ground truth changed after initialization"
            )
    documents = _load_groundtruth_bundle(groundtruth_root, protocol_id)
    if canonical_digest(documents) != entry["groundtruth_bundle_digest"]:
        raise CapabilityImprovementError(
            f"{protocol_id} transformed ground-truth contract changed"
        )


def _validate_catalog_artifacts(root: Path, catalog: Mapping[str, Any]) -> None:
    schemas = {
        "oligo_card": (OLIGO_CARD_SCHEMA, "card_digest"),
        "graph_card": (GRAPH_CARD_SCHEMA, "card_digest"),
        "mechanism_summary": (
            "exemplar_mechanism_summary.schema.json",
            "summary_digest",
        ),
    }
    ordinals = [item["training_ordinal"] for item in catalog["entries"]]
    if ordinals != list(range(1, catalog["training_count"] + 1)):
        raise CapabilityImprovementError("KB catalog ordinals are not consecutive")
    for entry in catalog["entries"]:
        for key, (schema, digest_field) in schemas.items():
            record = entry[key]
            document = _load_validated(
                root / record["path"],
                schema=schema,
                digest_field=digest_field,
                label=f"catalog {key}",
            )
            _assert_artifact(root, record, document[digest_field])


def _checkpoint_label(design: Mapping[str, Any], training_count: int) -> str | None:
    return next(
        (
            item["label"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] == training_count
        ),
        None,
    )


def _task_contract_files(task_root: Path) -> list[Path]:
    required = (
        task_root / "instruction.md",
        task_root / "rules.md",
        task_root / "input_manifest.json",
        task_root / "task.toml",
        task_root / "environment/fetch_input.py",
        task_root / "environment/Dockerfile",
        task_root / "tests/test.sh",
        task_root / "tests/grade.py",
        task_root / "tests/Dockerfile",
    )
    missing = [path for path in required if not path.is_file() or path.is_symlink()]
    if missing:
        raise CapabilityImprovementError(
            "generated task contract is incomplete: "
            + ", ".join(path.as_posix() for path in missing)
        )
    files = list(required)
    for relative in (
        "environment/protocol_sources",
        "tests/groundtruth",
    ):
        directory = task_root / relative
        if not directory.is_dir() or directory.is_symlink():
            raise CapabilityImprovementError(
                f"generated task contract directory is missing: {directory}"
            )
        files.extend(_regular_files(directory))
    return sorted(set(files))


def _validate_kb_base_config(
    config: Mapping[str, Any], design: Mapping[str, Any]
) -> None:
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise CapabilityImprovementError("KB base config requires one Codex agent")
    agent = agents[0]
    expected = design["agent"]
    observed = {
        "name": agent.get("name"),
        "model": agent.get("model_name"),
        "version": (agent.get("kwargs") or {}).get("version"),
        "reasoning_effort": (agent.get("kwargs") or {}).get("reasoning_effort"),
    }
    if observed != expected:
        raise CapabilityImprovementError(
            f"KB base config agent differs from the design: {observed}"
        )
    datasets = config.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1:
        raise CapabilityImprovementError("KB base config requires one dataset")
    environment = config.get("environment") or {}
    if not isinstance(environment, Mapping) or environment.get("mounts") not in (
        None,
        [],
    ):
        raise CapabilityImprovementError(
            "KB base config must not contain pre-existing mounts"
        )
    if config.get("extra_instruction_paths") not in (None, []):
        raise CapabilityImprovementError(
            "KB base config must not contain pre-existing extra instructions"
        )
    agent_environment = agent.get("env") or {}
    if any(
        key in agent_environment
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "CODEX_FORCE_AUTH_JSON",
            "CODEX_AUTH_JSON_PATH",
        )
    ):
        raise CapabilityImprovementError(
            "KB base config may not expose auth values to Harbor artifact redaction"
        )


def _force_local_codex_subscription(config: dict[str, Any]) -> None:
    agent = config["agents"][0]
    environment = dict(agent.get("env") or {})
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment.pop("CODEX_FORCE_AUTH_JSON", None)
    environment.pop("CODEX_AUTH_JSON_PATH", None)
    if environment:
        agent["env"] = environment
    else:
        agent.pop("env", None)


def _select_family_donors(
    *,
    design: Mapping[str, Any],
    target_protocol_id: str,
    catalog: Mapping[str, Any],
    identity: Mapping[str, Any],
    retrieval_policy: str = RETRIEVAL_POLICY,
) -> list[str]:
    family = next(
        (
            item["ordered_protocol_ids"]
            for item in design["families"]
            if target_protocol_id in item["ordered_protocol_ids"]
        ),
        None,
    )
    if family is None or not catalog["entries"]:
        return []
    available_ids = {item["exemplar_id"] for item in catalog["entries"]}
    candidates = []
    target_index = family.index(target_protocol_id)
    for item in identity["entries"]:
        if item["exemplar_id"] not in available_ids:
            continue
        protocol_id = item["protocol_id"]
        if protocol_id not in family:
            continue
        candidates.append(
            (
                abs(family.index(protocol_id) - target_index),
                -item["training_ordinal"],
                item["exemplar_id"],
            )
        )
    candidates.sort()
    if retrieval_policy == RETRIEVAL_POLICY_V2:
        candidates = candidates[: design["policies"]["retrieval_max_units"]]
    elif retrieval_policy != RETRIEVAL_POLICY_V3:
        raise CapabilityImprovementError(
            f"unsupported KB retrieval policy: {retrieval_policy}"
        )
    return [item[2] for item in candidates]


def _read_only_mount(source: Path, target: str) -> dict[str, Any]:
    return {
        "type": "bind",
        "source": source.resolve().as_posix(),
        "target": target,
        "read_only": True,
        "bind": {"create_host_path": False},
    }


def _knowledge_runtime_contract(
    *,
    exposure: Path,
    instruction_path: Path,
    retrieval_policy: str = RETRIEVAL_POLICY,
) -> dict[str, Any]:
    """Pin every mutable byte that controls ranked donor access for one plan."""

    if retrieval_policy not in SUPPORTED_RETRIEVAL_POLICIES:
        raise CapabilityImprovementError(
            f"unsupported KB retrieval policy: {retrieval_policy}"
        )
    payload = {
        "retrieval_policy": retrieval_policy,
        "query_tool_sha256": sha256_file(exposure / "query_kb.py"),
        "catalog_sha256": sha256_file(exposure / "catalog.json"),
        "ranking_index_sha256": sha256_file(exposure / "ranking_index.json"),
        "family_priorities_sha256": sha256_file(
            exposure / "family_priorities.json"
        ),
        "instruction_sha256": sha256_file(instruction_path),
    }
    return {**payload, "runtime_digest": canonical_digest(payload)}


def _validate_knowledge_runtime_contract(plan: Mapping[str, Any]) -> None:
    runtime = plan.get("knowledge_runtime")
    policy = plan.get("retrieval_policy")
    if runtime is None:
        if policy in SUPPORTED_RETRIEVAL_POLICIES:
            raise CapabilityImprovementError(
                "ranked-retrieval plan lacks a pinned knowledge runtime"
            )
        return
    if policy not in SUPPORTED_RETRIEVAL_POLICIES:
        raise CapabilityImprovementError(
            f"unsupported pinned KB retrieval policy: {policy}"
        )
    if not isinstance(runtime, Mapping) or runtime.get("retrieval_policy") != policy:
        raise CapabilityImprovementError(
            "knowledge runtime and plan retrieval policies disagree"
        )
    semantic = {key: value for key, value in runtime.items() if key != "runtime_digest"}
    if runtime.get("runtime_digest") != canonical_digest(semantic):
        raise CapabilityImprovementError("knowledge runtime digest is stale")

    config_ref = plan.get("config")
    if not isinstance(config_ref, Mapping):
        raise CapabilityImprovementError(
            "ranked-retrieval plan lacks its Harbor config"
        )
    config_path = Path(str(config_ref.get("path", ""))).expanduser().resolve()
    if (
        not config_path.is_file()
        or config_path.is_symlink()
        or sha256_file(config_path) != config_ref.get("sha256")
    ):
        raise CapabilityImprovementError("ranked-retrieval Harbor config changed")
    config = load_json_object(config_path, label="ranked-retrieval Harbor config")
    environment = config.get("environment", {})
    if not isinstance(environment, Mapping):
        raise CapabilityImprovementError("ranked-retrieval environment is invalid")
    mounts = environment.get("mounts", [])
    if not isinstance(mounts, list):
        raise CapabilityImprovementError("ranked-retrieval mounts are invalid")
    kb_mounts = [
        item
        for item in mounts
        if isinstance(item, Mapping)
        and item.get("target") == "/workspace/knowledge_base"
    ]
    if len(kb_mounts) != 1:
        raise CapabilityImprovementError(
            "ranked-retrieval plan needs exactly one knowledge-base mount"
        )
    exposure = _required_directory(
        Path(str(kb_mounts[0].get("source", ""))),
        "ranked-retrieval knowledge-base exposure",
    )
    instructions = config.get("extra_instruction_paths", [])
    if not isinstance(instructions, list) or len(instructions) != 1:
        raise CapabilityImprovementError(
            "ranked-retrieval plan needs exactly one runtime instruction"
        )
    instruction_path = Path(str(instructions[0])).expanduser().resolve()
    if not instruction_path.is_file() or instruction_path.is_symlink():
        raise CapabilityImprovementError(
            "ranked-retrieval instruction is missing or unsafe"
        )
    actual = {
        "query_tool_sha256": sha256_file(exposure / "query_kb.py"),
        "catalog_sha256": sha256_file(exposure / "catalog.json"),
        "ranking_index_sha256": sha256_file(exposure / "ranking_index.json"),
        "family_priorities_sha256": sha256_file(
            exposure / "family_priorities.json"
        ),
        "instruction_sha256": sha256_file(instruction_path),
    }
    for field, digest in actual.items():
        if runtime.get(field) != digest:
            raise CapabilityImprovementError(
                f"ranked-retrieval runtime artifact changed: {field}"
            )


def _kb_runtime_instruction(*, has_pact: bool, has_catalog: bool) -> str:
    lines = [
        "# Agent-guided KB runtime\n",
        "The target's primary sources remain the only authority for target claims.",
        "Do not copy donor bases, lengths, operations, or topology unless the target sources independently support them.",
        SOURCE_INSPECTION_EFFICIENCY,
    ]
    if has_pact:
        lines.extend(
            [
                "Read `/workspace/capability_pack/PLAYBOOK.md` and apply its protocol-neutral controls.",
                "Run the pack's deterministic checks before final validation.",
            ]
        )
    if has_catalog:
        lines.extend(
            [
                "A pseudonymous prior-training catalog and deterministic query tool are exposed under `/workspace/knowledge_base/`.",
                KB_QUERY_FEATURE_VOCABULARY,
                "After building a target-source-located feature inventory, write `/logs/artifacts/kb_query.json`. The query tool binds both stages to the immutable target input manifest.",
                KB_RETRIEVAL_WORKFLOW,
            ]
        )
    lines.extend(
        [
            "Construct source evidence and oligo inventories first, then physical state cards, then the event-participant table, and only then compile T2/T3.",
            "Run the local validator and use at most two repair attempts for deterministic schema or linkage failures.",
        ]
    )
    return "\n\n".join(lines) + "\n"


def _mechanism_summary_features(summary: Mapping[str, Any]) -> dict[str, list[str]]:
    barcode_map = {
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
    selection_map = {
        "affinity_selection": "affinity_selection",
        "size_selection": "size_selection",
        "capture": "capture",
        "sample_split": "sample_split",
        "modality_branching": "modality_branching",
        "alternative_branching": "alternative_branching",
        "discarded_product_branch": "discard_branching",
    }
    chemistry_map = {
        "reverse_transcription": "reverse_transcription",
        "template_switching": "template_switching",
        "ligation": "ligation",
        "tagmentation": "tagmentation",
        "pcr": "pcr",
        "restriction": "restriction",
        "conversion": "conversion",
    }
    return {
        "modalities": sorted(
            item["modality"]
            for item in summary["modality_counts"]
            if item["count"] > 0
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
            for key, value in barcode_map.items()
            if summary["barcoding_partitioning"][key]
        ),
        "selection_branching": sorted(
            value
            for key, value in selection_map.items()
            if summary["selection_branching"][key]
        ),
        "chemistries": sorted(
            value
            for key, value in chemistry_map.items()
            if summary["chemistry_flags"][key]
        ),
    }


def _build_exposure_catalog(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Remove all non-card private indexing references from the mounted catalog."""

    document = with_digest(
        {
            "schema_version": EXPOSURE_CATALOG_SCHEMA_VERSION,
            "source_catalog_digest": catalog["catalog_digest"],
            "training_count": catalog["training_count"],
            "entries": [
                {
                    "exemplar_id": entry["exemplar_id"],
                    "oligo_card": dict(entry["oligo_card"]),
                    "graph_card": dict(entry["graph_card"]),
                }
                for entry in catalog["entries"]
            ],
        },
        "catalog_digest",
    )
    _validate_generated(
        document,
        EXPOSURE_CATALOG_SCHEMA,
        "catalog_digest",
        "ground-truth-only KB exposure catalog",
    )
    return document


def _build_ranking_index(
    *,
    root: Path,
    source_catalog: Mapping[str, Any],
    exposure_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Build agent-visible similarity metadata from ground-truth projections only."""

    entries = []
    for entry in source_catalog["entries"]:
        summary_ref = entry["mechanism_summary"]
        summary = load_json_object(
            root / summary_ref["path"], label="private KB mechanism index"
        )
        if (
            sha256_file(root / summary_ref["path"]) != summary_ref["sha256"]
            or summary.get("summary_digest") != summary_ref["digest"]
        ):
            raise CapabilityImprovementError("private KB mechanism index changed")
        entries.append(
            {
                "exemplar_id": entry["exemplar_id"],
                "features": _mechanism_summary_features(summary),
            }
        )
    semantic = {
        "schema_version": "libstruct.libgen_kb_ranking_index.v1",
        "catalog_digest": exposure_catalog["catalog_digest"],
        "entries": entries,
    }
    return {**semantic, "index_digest": canonical_digest(semantic)}


def _prepare_training_knowledge_exposure(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    design: Mapping[str, Any],
    catalog: Mapping[str, Any],
    target_protocol_id: str,
    plan_root: Path,
) -> Path:
    exposure = plan_root / "knowledge_exposure"
    if exposure.exists():
        return exposure
    exposure.mkdir()
    try:
        exposure_catalog = _build_exposure_catalog(catalog)
        write_json_atomic(exposure / "catalog.json", exposure_catalog, mode=0o444)
        for entry in catalog["entries"]:
            for key in ("oligo_card", "graph_card"):
                reference = entry[key]
                source = root / reference["path"]
                destination = exposure / reference["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        write_json_atomic(
            exposure / "ranking_index.json",
            _build_ranking_index(
                root=root,
                source_catalog=catalog,
                exposure_catalog=exposure_catalog,
            ),
            mode=0o444,
        )
        identity = _load_validated(
            root / manifest["identity_map"]["path"],
            schema=IDENTITY_SCHEMA,
            digest_field="identity_map_digest",
            label="KB identity map",
        )
        priorities = {
            target_protocol_id: _select_family_donors(
                design=design,
                target_protocol_id=target_protocol_id,
                catalog=catalog,
                identity=identity,
            )
        }
        write_json_atomic(exposure / "family_priorities.json", priorities, mode=0o444)
        runtime_source = (
            Path(str(manifest["repository"]["path"]))
            / "improvement/kb_runtime/query_kb.py"
        )
        if not runtime_source.is_file() or runtime_source.is_symlink():
            raise CapabilityImprovementError(
                f"KB query runtime is missing: {runtime_source}"
            )
        shutil.copy2(runtime_source, exposure / "query_kb.py")
        (exposure / "query_kb.py").chmod(0o444)
        _make_design_read_only(exposure)
        return exposure
    except Exception:
        shutil.rmtree(exposure, ignore_errors=True)
        raise


def _prepare_checkpoint_exposure(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    design: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    protocol_ids: Sequence[str],
    run_root: Path,
) -> tuple[Path, Path]:
    exposure = run_root / "exposures" / checkpoint["label"]
    instruction_path = exposure / "extra_instruction.md"
    if exposure.exists():
        for required in (
            exposure / "catalog.json",
            exposure / "ranking_index.json",
            exposure / "family_priorities.json",
            exposure / "query_kb.py",
            instruction_path,
        ):
            if not required.is_file() or required.is_symlink():
                raise CapabilityImprovementError(
                    f"checkpoint exposure is incomplete: {required}"
                )
        return exposure, instruction_path
    catalog = _load_validated(
        root / checkpoint["catalog"]["path"],
        schema=CATALOG_SCHEMA,
        digest_field="catalog_digest",
        label=f"{checkpoint['label']} exposure catalog",
    )
    identity = _load_validated(
        root / manifest["identity_map"]["path"],
        schema=IDENTITY_SCHEMA,
        digest_field="identity_map_digest",
        label="KB identity map",
    )
    exposure.mkdir(parents=True)
    try:
        exposure_catalog = _build_exposure_catalog(catalog)
        write_json_atomic(exposure / "catalog.json", exposure_catalog, mode=0o444)
        for entry in catalog["entries"]:
            for key in ("oligo_card", "graph_card"):
                reference = entry[key]
                source = root / reference["path"]
                destination = exposure / reference["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        write_json_atomic(
            exposure / "ranking_index.json",
            _build_ranking_index(
                root=root,
                source_catalog=catalog,
                exposure_catalog=exposure_catalog,
            ),
            mode=0o444,
        )
        priorities = {
            protocol_id: _select_family_donors(
                design=design,
                target_protocol_id=protocol_id,
                catalog=catalog,
                identity=identity,
            )
            for protocol_id in design["protocol_ids"]
        }
        write_json_atomic(exposure / "family_priorities.json", priorities, mode=0o444)
        runtime_source = (
            Path(str(manifest["repository"]["path"]))
            / "improvement/kb_runtime/query_kb.py"
        )
        if not runtime_source.is_file() or runtime_source.is_symlink():
            raise CapabilityImprovementError(
                f"KB query runtime is missing: {runtime_source}"
            )
        shutil.copy2(runtime_source, exposure / "query_kb.py")
        (exposure / "query_kb.py").chmod(0o444)
        instruction_path.write_text(
            _checkpoint_runtime_instruction(
                has_pact=checkpoint["pact"]["kind"] == "learned"
            ),
            encoding="utf-8",
        )
        instruction_path.chmod(0o444)
        _make_design_read_only(exposure)
        return exposure, instruction_path
    except Exception:
        shutil.rmtree(exposure, ignore_errors=True)
        raise


def _checkpoint_runtime_instruction(*, has_pact: bool) -> str:
    pact = (
        "Read `/workspace/capability_pack/PLAYBOOK.md` and run its protocol-neutral checks."
        if has_pact
        else "No learned procedural pact is active at this checkpoint."
    )
    return f"""# Frozen knowledge-base checkpoint runtime

The target primary sources remain the only authority. {pact}

{SOURCE_INSPECTION_EFFICIENCY}

After building a target-source evidence inventory, write `/logs/artifacts/kb_query.json`:

```json
{{
  "schema_version": "libstruct.libgen_kb_query.v1",
  "features": {{
    "modalities": [],
    "operations": [],
    "barcoding_partitioning": [],
    "architectures": [],
    "selection_branching": [],
    "chemistries": []
  }},
  "evidence": [
    {{
      "feature_group": "operations",
      "feature_value": "reverse_transcription",
      "source_locator": "source file and page/table/figure"
    }}
  ]
}}
```

Every requested feature must have a target-source locator. Then run:

{KB_QUERY_FEATURE_VOCABULARY}

{KB_RETRIEVAL_WORKFLOW}

Build source evidence and oligo inventories first, then physical state cards, then the event-participant table, and finally compile linked T2/T3. Run local validation and use at most two deterministic repair attempts.
"""


def _project_oligo_component(
    component: Mapping[str, Any], redactions: set[str]
) -> dict[str, Any]:
    """Project one component without emitting schema-invalid null optionals."""

    component_support = component["support_status"]
    projected: dict[str, Any] = {
        "name": _redact_text(component["name"], redactions),
        "role": _redact_text(component["role"], redactions),
        "orientation": component["orientation"],
        "modifications": list(component["modifications"]),
        "support_status": component_support,
        "retrieval_eligible": component_support in RETRIEVAL_SUPPORT,
    }
    for key in ("sequence", "placeholder", "length"):
        if component.get(key) is not None:
            projected[key] = component[key]
    return projected


def build_protocol_cards(
    *,
    protocol_id: str,
    training_ordinal: int,
    exemplar_id: str,
    groundtruth_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project one approved training bundle into clean pseudonymous cards."""

    documents = _load_groundtruth_bundle(groundtruth_root, protocol_id)
    oligo_map = {
        item["oligo_id"]: f"oligo-{index:03d}"
        for index, item in enumerate(documents["T2"]["oligos"], start=1)
    }
    oligos: list[dict[str, Any]] = []
    redactions = {
        protocol_id,
        str(documents["T3"].get("protocol_name", "")),
        str(documents["T3"].get("protocol_id", "")),
    }
    for item in documents["T2"]["oligos"]:
        support = item["support_status"]
        projected: dict[str, Any] = {
            "oligo_id": oligo_map[item["oligo_id"]],
            "name": _redact_text(item["name"], redactions),
            "aliases": [_redact_text(value, redactions) for value in item["aliases"]],
            "role": _redact_text(item["role"], redactions),
            "kind": item["kind"],
            "orientation": item["orientation"],
            "components": [],
            "modifications": list(item["modifications"]),
            "support_status": support,
            "retrieval_eligible": support in RETRIEVAL_SUPPORT,
        }
        if isinstance(item.get("sequence"), str):
            projected["sequence"] = item["sequence"]
        for component in item["components"]:
            projected["components"].append(
                _project_oligo_component(component, redactions)
            )
        oligos.append(projected)
    oligo_card = with_digest(
        {
            "schema_version": OLIGO_CARD_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "training_ordinal": training_ordinal,
            "oligos": oligos,
        },
        "card_digest",
    )
    _validate_generated(oligo_card, OLIGO_CARD_SCHEMA, "card_digest", "KB oligo card")

    workflows = [
        _project_workflow(
            workflow,
            workflow_index=index,
            oligo_map=oligo_map,
            redactions=redactions,
        )
        for index, workflow in enumerate(documents["T3"]["workflows"], start=1)
    ]
    graph_card = with_digest(
        {
            "schema_version": GRAPH_CARD_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "training_ordinal": training_ordinal,
            "workflows": workflows,
        },
        "card_digest",
    )
    _validate_generated(graph_card, GRAPH_CARD_SCHEMA, "card_digest", "KB graph card")
    summary = _build_mechanism_summary(
        exemplar_id=exemplar_id,
        t2_groundtruth=documents["T2"],
        t3_groundtruth=documents["T3"],
    )
    _validate_generated(
        summary,
        "exemplar_mechanism_summary.schema.json",
        "summary_digest",
        "KB mechanism summary",
    )
    return oligo_card, graph_card, summary


def build_c0_compatibility_registry(
    *,
    design: Mapping[str, Any],
    baseline_root: Path,
    source_manifests: Mapping[str, Mapping[str, Any]],
    repository_root: Path,
    baseline_task_root: Path | None = None,
) -> dict[str, Any]:
    expected = dict(design["agent"])
    entries: list[dict[str, Any]] = []
    results = _baseline_results_by_protocol(baseline_root)
    panels = {
        protocol_id: panel
        for panel in ("training", "validation", "test")
        for protocol_id in design["split"][panel]
    }
    ordered_protocols = [
        *design["split"]["training"],
        *design["split"]["validation"],
        *design["split"]["test"],
    ]
    for protocol_id in ordered_protocols:
        candidates = results.get(protocol_id, [])
        compatible: list[dict[str, Any]] = []
        incompatible_reasons: list[str] = []
        for result_path, result in candidates:
            reasons = _baseline_incompatibility_reasons(
                result=result,
                trial_root=result_path.parent,
                expected_agent=expected,
                source_manifest=source_manifests[protocol_id],
                repository_root=repository_root,
                task_root_override=(
                    None
                    if baseline_task_root is None
                    else baseline_task_root.expanduser().resolve() / protocol_id
                ),
            )
            if reasons:
                incompatible_reasons.extend(reasons)
                continue
            compatible.append({"path": result_path, "result": result})
        if len(compatible) > 1:
            raise CapabilityImprovementError(
                f"multiple compatible raw C0 results for {protocol_id}"
            )
        if compatible:
            result_path = compatible[0]["path"]
            trial_root = result_path.parent
            prediction_files = _prediction_files(trial_root)
            task_root = _resolve_task_root(
                compatible[0]["result"],
                repository_root=repository_root,
                fallback=(
                    None
                    if baseline_task_root is None
                    else baseline_task_root.expanduser().resolve() / protocol_id
                ),
            )
            condition, baseline_trial = _build_baseline_trial_pin(
                trial_root=trial_root,
                result=compatible[0]["result"],
                protocol_id=protocol_id,
            )
            entries.append(
                {
                    "protocol_id": protocol_id,
                    "panel": panels[protocol_id],
                    "status": "compatible",
                    "reasons": [],
                    "trial_root": trial_root.resolve().as_posix(),
                    "result_sha256": sha256_file(result_path),
                    "prediction_sha256": canonical_digest(
                        {
                            key: sha256_file(value)
                            for key, value in prediction_files.items()
                        }
                    ),
                    "agent_visible_fingerprint": _agent_visible_fingerprint(task_root),
                    "baseline_condition": condition,
                    "baseline_trial": baseline_trial,
                }
            )
        elif candidates:
            entries.append(
                {
                    "protocol_id": protocol_id,
                    "panel": panels[protocol_id],
                    "status": "incompatible",
                    "reasons": sorted(set(incompatible_reasons))
                    or ["no candidate satisfied raw-C0 compatibility"],
                }
            )
        else:
            entries.append(
                {
                    "protocol_id": protocol_id,
                    "panel": panels[protocol_id],
                    "status": "missing",
                    "reasons": ["no raw baseline result found"],
                }
            )
    status_counts = Counter(item["status"] for item in entries)
    result = with_digest(
        {
            "schema_version": C0_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "baseline_root": baseline_root.resolve().as_posix(),
            "expected_agent": expected,
            "entries": entries,
            "summary": {
                "compatible": status_counts["compatible"],
                "missing": status_counts["missing"],
                "incompatible": status_counts["incompatible"],
                "backfill_protocol_ids": [
                    item["protocol_id"]
                    for item in entries
                    if item["status"] != "compatible"
                ],
            },
        },
        "registry_digest",
    )
    _validate_generated(
        result, C0_SCHEMA, "registry_digest", "C0 compatibility registry"
    )
    return result


def kb_status(experiment_root: Path) -> dict[str, Any]:
    manifest = validate_kb_experiment(experiment_root)
    root = experiment_root.resolve()
    design = load_json_object(root / manifest["design"]["path"], label="KB design")
    c0 = load_json_object(root / manifest["c0_registry"]["path"], label="C0 registry")
    state = load_json_object(
        root / manifest["current_microstate"]["path"], label="KB microstate"
    )
    return {
        "experiment_id": manifest["experiment_id"],
        "experiment_digest": manifest["experiment_digest"],
        "protocol_counts": design["expected_counts"],
        "current_microstate": state["state_id"],
        "training_count": state["training_count"],
        "next_protocol_id": (
            design["split"]["training"][state["training_count"]]
            if state["training_count"] < len(design["split"]["training"])
            else None
        ),
        "c0": c0["summary"],
        "final_lock": manifest["final_lock"],
    }


def _build_universe_contract(
    *,
    protocol_ids: Sequence[str],
    source_manifest_search_root: Path,
    groundtruth_root: Path,
    output_manifest_root: Path,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    truth_rows: list[dict[str, Any]] = []
    manifest_schema = (
        improvement_schema_root().parent / "audit/audit_input_manifest.schema.json"
    )
    for protocol_id in protocol_ids:
        manifest_path = _find_source_manifest(source_manifest_search_root, protocol_id)
        manifest = load_json_object(
            manifest_path, label=f"{protocol_id} audit input manifest"
        )
        validate_document(
            manifest, manifest_schema, label=f"{protocol_id} audit input manifest"
        )
        if manifest["protocol_id"] != protocol_id:
            raise CapabilityImprovementError(
                f"audit input manifest protocol mismatch for {protocol_id}"
            )
        destination = output_manifest_root / f"{protocol_id}.json"
        shutil.copy2(manifest_path, destination)
        documents = _load_groundtruth_bundle(groundtruth_root, protocol_id)
        groundtruth_files = {
            task: {
                "path": f"{protocol_id}/{filename}",
                "sha256": sha256_file(groundtruth_root / protocol_id / filename),
            }
            for task, filename in GROUNDTRUTH_FILES.items()
        }
        included_sources = [
            {
                "path": source["path"],
                "sha256": source["sha256"],
            }
            for source in manifest["sources"]
            if source["role"] == "primary_evidence"
            and source["approval_status"] == "included"
        ]
        if not included_sources:
            raise CapabilityImprovementError(
                f"{protocol_id} has no included primary evidence"
            )
        source_rows.append({"protocol_id": protocol_id, "sources": included_sources})
        truth_rows.append(
            {"protocol_id": protocol_id, "groundtruth": groundtruth_files}
        )
        entries.append(
            {
                "protocol_id": protocol_id,
                "source_manifest_path": f"design/source_manifests/{protocol_id}.json",
                "source_manifest_sha256": sha256_file(destination),
                "source_count": len(included_sources),
                "groundtruth": groundtruth_files,
                "groundtruth_bundle_digest": canonical_digest(documents),
            }
        )
    return {
        "protocol_count": len(entries),
        "source_manifest_digest": canonical_digest(source_rows),
        "groundtruth_digest": canonical_digest(truth_rows),
        "entries": entries,
    }


def _build_identity_map(design: Mapping[str, Any]) -> dict[str, Any]:
    nonce = secrets.token_hex(32)
    entries = []
    for ordinal, protocol_id in enumerate(design["split"]["training"], start=1):
        opaque = hmac.new(
            bytes.fromhex(nonce), protocol_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:32]
        entries.append(
            {
                "protocol_id": protocol_id,
                "training_ordinal": ordinal,
                "exemplar_id": f"exm-{opaque}",
            }
        )
    result = with_digest(
        {
            "schema_version": IDENTITY_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "mapping_nonce": nonce,
            "entries": entries,
        },
        "identity_map_digest",
    )
    _validate_generated(
        result, IDENTITY_SCHEMA, "identity_map_digest", "KB identity map"
    )
    return result


def _project_workflow(
    workflow: Mapping[str, Any],
    *,
    workflow_index: int,
    oligo_map: Mapping[str, str],
    redactions: set[str],
) -> dict[str, Any]:
    states = list(workflow["states"])
    state_map = {
        item["state_id"]: f"state-{index:03d}"
        for index, item in enumerate(states, start=1)
    }
    strand_map = {
        item["strand_id"]: f"strand-{index:03d}"
        for index, item in enumerate(
            (strand for state in states for strand in state["strands"]), start=1
        )
    }
    segment_map = {
        item["segment_id"]: f"segment-{index:03d}"
        for index, item in enumerate(
            (
                segment
                for state in states
                for strand in state["strands"]
                for segment in strand["segments"]
            ),
            start=1,
        )
    }
    pair_map = {
        item["paired_region_id"]: f"paired-region-{index:03d}"
        for index, item in enumerate(
            (pair for state in states for pair in state["paired_regions"]), start=1
        )
    }
    discontinuity_map = {
        item["discontinuity_id"]: f"discontinuity-{index:03d}"
        for index, item in enumerate(
            (value for state in states for value in state["discontinuities"]),
            start=1,
        )
    }
    transition_map = {
        item["transition_id"]: f"transition-{index:03d}"
        for index, item in enumerate(workflow["transitions"], start=1)
    }
    maps = {
        "state": state_map,
        "strand": strand_map,
        "segment": segment_map,
        "pair": pair_map,
        "discontinuity": discontinuity_map,
        "transition": transition_map,
        "oligo": oligo_map,
    }
    projected = _rewrite_graph_value(copy.deepcopy(dict(workflow)), maps, redactions)
    projected["workflow_id"] = f"workflow-{workflow_index:03d}"
    return projected


def _rewrite_graph_value(
    value: Any, maps: Mapping[str, Mapping[str, str]], redactions: set[str]
) -> Any:
    if isinstance(value, str):
        return _redact_text(value, redactions)
    if isinstance(value, list):
        return [_rewrite_graph_value(item, maps, redactions) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    singular = {
        "state_id": "state",
        "reference_strand_id": "strand",
        "strand_id": "strand",
        "segment_id": "segment",
        "after_segment_id": "segment",
        "before_segment_id": "segment",
        "paired_region_id": "pair",
        "discontinuity_id": "discontinuity",
        "transition_id": "transition",
        "oligo_id": "oligo",
    }
    plural = {
        "initial_state_ids": "state",
        "substrate_state_ids": "state",
        "product_state_ids": "state",
        "carried_forward_product_ids": "state",
        "discarded_product_ids": "state",
        "segment_ids": "segment",
        "oligo_ids": "oligo",
    }
    for key, item in value.items():
        if key in singular and isinstance(item, str):
            result[key] = _mapped(maps[singular[key]], item, key)
        elif key in plural and isinstance(item, list):
            result[key] = [_mapped(maps[plural[key]], entry, key) for entry in item]
        elif key in {"protocol_id", "protocol_name", "protocol_scope"}:
            continue
        else:
            result[key] = _rewrite_graph_value(item, maps, redactions)
    support = result.get("support_status")
    if isinstance(support, str):
        result["retrieval_eligible"] = support in RETRIEVAL_SUPPORT
    return result


def _baseline_results_by_protocol(
    baseline_root: Path,
) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    results: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in sorted(baseline_root.rglob("result.json")):
        if "failed-attempts" in path.parts:
            continue
        try:
            document = load_json_object(path, label="baseline result")
        except Exception:
            continue
        task_name = document.get("task_name")
        prefix = "sequencing/libgen-"
        if not isinstance(task_name, str) or not task_name.startswith(prefix):
            continue
        protocol_id = task_name.removeprefix(prefix)
        results.setdefault(protocol_id, []).append((path.resolve(), document))
    return results


def _baseline_runtime_provenance(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    """Classify a raw Codex trial or a frozen C0 trial with empty memory."""

    config = result.get("config", {})
    if not isinstance(config, Mapping):
        raise CapabilityImprovementError("baseline result config is invalid")
    environment = config.get("environment", {})
    if not isinstance(environment, Mapping):
        raise CapabilityImprovementError("baseline environment is invalid")
    mounts = environment.get("mounts") or []
    instructions = config.get("extra_instruction_paths") or []
    if not mounts and not instructions:
        return "raw_codex", []
    if not isinstance(mounts, list) or not isinstance(instructions, list):
        raise CapabilityImprovementError("baseline runtime exposure is malformed")
    by_target = {
        item.get("target"): item
        for item in mounts
        if isinstance(item, Mapping) and isinstance(item.get("target"), str)
    }
    required_targets = {"/workspace/capability_pack", "/workspace/capability_memory"}
    if set(by_target) != required_targets or len(mounts) != 2 or len(instructions) != 1:
        raise CapabilityImprovementError(
            "baseline has non-C0 memory, capability mounts, or instructions"
        )
    pack_root = _required_directory(
        Path(str(by_target["/workspace/capability_pack"].get("source", ""))),
        "baseline C0 capability pack",
    )
    memory_root = _required_directory(
        Path(str(by_target["/workspace/capability_memory"].get("source", ""))),
        "baseline C0 memory",
    )
    validate_capability_pack(pack_root)
    learned_rules = pack_root / "checklists/learned_rules.json"
    if learned_rules.exists():
        rules = load_json_object(learned_rules, label="baseline C0 learned rules")
        if rules.get("rules") not in (None, []):
            raise CapabilityImprovementError("baseline C0 pact is not empty")
    catalog_path = memory_root / "catalog.json"
    catalog = load_json_object(catalog_path, label="baseline C0 memory catalog")
    if catalog.get("exemplar_count") != 0 or catalog.get("exemplars") != []:
        raise CapabilityImprovementError("baseline C0 memory is not empty")
    if isinstance(catalog.get("catalog_digest"), str):
        validate_digest(catalog, "catalog_digest")
    memory_manifest = memory_root / "manifest.json"
    load_json_object(memory_manifest, label="baseline C0 memory manifest")
    instruction = Path(str(instructions[0])).expanduser().resolve()
    if not instruction.is_file() or instruction.is_symlink():
        raise CapabilityImprovementError("baseline C0 instruction is unavailable")
    runtime_paths = [
        ("capability_pack_manifest", pack_root / "manifest.json"),
        ("empty_memory_catalog", catalog_path),
        ("empty_memory_manifest", memory_manifest),
        ("extra_instruction", instruction),
    ]
    if learned_rules.is_file() and not learned_rules.is_symlink():
        runtime_paths.append(("empty_learned_rules", learned_rules))
    artifacts = [
        {
            "label": label,
            "path": path.resolve().as_posix(),
            "sha256": sha256_file(path),
        }
        for label, path in runtime_paths
    ]
    return "codex_c0_empty_memory_base_pack", artifacts


def _baseline_verifier_root(trial_root: Path, protocol_id: str) -> Path:
    candidates = (
        trial_root / "verifier",
        trial_root / "verifier/rescore" / f"libgen-{LIBGEN_BENCHMARK_VERSION}",
    )
    for candidate in candidates:
        details_path = candidate / "details.json"
        if not details_path.is_file() or details_path.is_symlink():
            continue
        try:
            details = load_json_object(details_path, label="baseline verifier details")
        except Exception:
            continue
        if (
            details.get("benchmark_version") == LIBGEN_BENCHMARK_VERSION
            and details.get("protocol_id") == protocol_id
        ):
            return candidate
    raise CapabilityImprovementError(
        f"baseline lacks LibGen {LIBGEN_BENCHMARK_VERSION} verifier artifacts"
    )


def _build_baseline_trial_pin(
    *, trial_root: Path, result: Mapping[str, Any], protocol_id: str
) -> tuple[str, dict[str, Any]]:
    trial = trial_root.expanduser().resolve()
    predictions = _prediction_files(trial)
    trajectory = next(
        (
            path
            for path in (
                trial / "agent/trajectory.json",
                trial / "artifacts/agent_trajectory.json",
            )
            if path.is_file() and not path.is_symlink()
        ),
        None,
    )
    if trajectory is None:
        raise CapabilityImprovementError("baseline trajectory is unavailable")
    verifier = _baseline_verifier_root(trial, protocol_id)
    paths = {
        "config": trial / "config.json",
        "result": trial / "result.json",
        "t2_prediction": predictions["t2"],
        "t3_prediction": predictions["t3"],
        "trajectory": trajectory,
        "reward": verifier / "reward.json",
        "verifier_details": verifier / "details.json",
        "error_analysis": verifier / "error_analysis.json",
    }
    missing = [
        label
        for label, path in paths.items()
        if not path.is_file() or path.is_symlink()
    ]
    if missing:
        raise CapabilityImprovementError(
            "baseline is missing pinned artifacts: " + ", ".join(missing)
        )
    condition, runtime_artifacts = _baseline_runtime_provenance(result)
    artifacts = {
        label: {"path": path.resolve().as_posix(), "sha256": sha256_file(path)}
        for label, path in paths.items()
    }
    semantic = {
        "trial_root": trial.as_posix(),
        "artifacts": artifacts,
        "runtime_artifacts": runtime_artifacts,
    }
    return condition, {**semantic, "trial_digest": canonical_digest(semantic)}


def _validate_baseline_trial_pin(pin: Mapping[str, Any]) -> None:
    semantic = {
        "trial_root": pin.get("trial_root"),
        "artifacts": pin.get("artifacts"),
        "runtime_artifacts": pin.get("runtime_artifacts"),
    }
    if pin.get("trial_digest") != canonical_digest(semantic):
        raise CapabilityImprovementError("baseline trial digest is stale")
    artifacts = pin.get("artifacts")
    runtime_artifacts = pin.get("runtime_artifacts")
    if not isinstance(artifacts, Mapping) or not isinstance(runtime_artifacts, list):
        raise CapabilityImprovementError("baseline trial pin is malformed")
    references = [*artifacts.values(), *runtime_artifacts]
    for reference in references:
        if not isinstance(reference, Mapping):
            raise CapabilityImprovementError("baseline artifact pin is malformed")
        path = Path(str(reference.get("path", ""))).expanduser().resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != reference.get("sha256")
        ):
            raise CapabilityImprovementError(
                f"baseline artifact changed or is unavailable: {path}"
            )


def _baseline_incompatibility_reasons(
    *,
    result: Mapping[str, Any],
    trial_root: Path,
    expected_agent: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    repository_root: Path,
    task_root_override: Path | None = None,
) -> list[str]:
    reasons: list[str] = []
    info = result.get("agent_info", {})
    config = result.get("config", {})
    agent_config = config.get("agent", {}) if isinstance(config, Mapping) else {}
    model_info = info.get("model_info", {}) if isinstance(info, Mapping) else {}
    if info.get("name") != expected_agent["name"]:
        reasons.append("agent name mismatch")
    if info.get("version") != expected_agent["version"]:
        reasons.append("agent version mismatch")
    if model_info.get("name") != expected_agent["model"]:
        reasons.append("model mismatch")
    if (
        agent_config.get("kwargs", {}).get("reasoning_effort")
        != expected_agent["reasoning_effort"]
    ):
        reasons.append("reasoning effort mismatch")
    try:
        _baseline_runtime_provenance(result)
    except (CapabilityImprovementError, OSError, ValueError) as error:
        reasons.append(str(error))
    if result.get("exception_info") is not None:
        reasons.append("baseline trial has an exception")
    try:
        prediction_files = _prediction_files(trial_root)
        for prediction in prediction_files.values():
            load_json_object(prediction, label="baseline prediction")
        _build_baseline_trial_pin(
            trial_root=trial_root,
            result=result,
            protocol_id=str(result.get("task_name", "")).removeprefix(
                "sequencing/libgen-"
            ),
        )
    except (CapabilityImprovementError, OSError, ValueError):
        reasons.append(
            "baseline prediction, trajectory, or v4.5 verifier artifacts are missing"
        )
    try:
        task_root = _resolve_task_root(
            result,
            repository_root=repository_root,
            fallback=task_root_override,
        )
        observed_manifest = load_json_object(
            task_root / "input_manifest.json", label="baseline task input manifest"
        )
        if _normalized_source_rows(observed_manifest) != _normalized_source_rows(
            source_manifest
        ):
            reasons.append("agent-visible source inventory mismatch")
        for required in ("instruction.md", "rules.md", "input_manifest.json"):
            if not (task_root / required).is_file():
                reasons.append(f"agent-visible task is missing {required}")
    except (CapabilityImprovementError, OSError, ValueError):
        reasons.append("baseline task payload cannot be resolved")
    return sorted(set(reasons))


def _normalized_source_rows(document: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = []
    for source in document.get("sources", []):
        if "role" in source and (
            source.get("role") != "primary_evidence"
            or source.get("approval_status") != "included"
        ):
            continue
        path = str(source["path"])
        if path.startswith("protocols/"):
            path = path.removeprefix("protocols/")
        rows.append({"path": path, "sha256": str(source["sha256"])})
    return sorted(rows, key=lambda item: item["path"])


def _resolve_task_root(
    result: Mapping[str, Any],
    *,
    repository_root: Path,
    fallback: Path | None = None,
) -> Path:
    task = result.get("config", {}).get("task", {})
    value = task.get("path") if isinstance(task, Mapping) else None
    if not isinstance(value, str) or not value:
        raise CapabilityImprovementError("baseline result has no task path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repository_root / path
    path = path.resolve()
    if not path.is_dir() or path.is_symlink():
        if fallback is None:
            raise CapabilityImprovementError(
                f"baseline task path is unavailable: {path}"
            )
        path = fallback.expanduser().resolve()
        if not path.is_dir() or path.is_symlink():
            raise CapabilityImprovementError(
                f"baseline task path and frozen fallback are unavailable: {path}"
            )
    return path


def _agent_visible_fingerprint(task_root: Path) -> str:
    paths = [
        task_root / "instruction.md",
        task_root / "rules.md",
        task_root / "input_manifest.json",
    ]
    environment = task_root / "environment"
    if environment.is_dir():
        paths.extend(_regular_files(environment))
    rows = [
        {
            "path": path.relative_to(task_root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(set(paths))
        if path.is_file() and not path.is_symlink()
    ]
    return canonical_digest(rows)


def _prediction_files(trial_root: Path) -> dict[str, Path]:
    candidates = {
        "t2": [
            trial_root / "artifacts/logs/artifacts/t2_prediction.json",
            trial_root / "artifacts/t2_prediction.json",
        ],
        "t3": [
            trial_root / "artifacts/logs/artifacts/t3_prediction.json",
            trial_root / "artifacts/t3_prediction.json",
        ],
    }
    result: dict[str, Path] = {}
    for key, values in candidates.items():
        selected = next((path for path in values if path.is_file()), None)
        if selected is None:
            raise CapabilityImprovementError(
                f"baseline trial lacks {key.upper()} prediction: {trial_root}"
            )
        result[key] = selected
    return result


def _repository_lock_files(repository: Path) -> list[Path]:
    """Return every runtime/verifier input covered by the experiment lock."""

    roots = [
        repository / "src/libstruct_bench/libgen",
        repository / "src/libstruct_bench/improvement",
        repository / "src/libstruct_bench/cli/libgen_capability_improvement.py",
        repository / "improvement/kb_runtime",
        repository / "schemas/benchmark",
        repository / "schemas/groundtruth",
        repository / "schemas/improvement",
        repository / "benchmarks/tasks2_3/rules.md",
    ]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            selected = [root]
        elif root.is_dir():
            selected = _regular_files(root)
        else:
            raise CapabilityImprovementError(
                f"repository verifier-lock input is missing: {root}"
            )
        paths.extend(selected)
    return sorted(set(paths))


def _repository_record(repository: Path) -> dict[str, Any]:
    commit = _run_git(repository, "rev-parse", "HEAD")
    dirty = bool(_run_git(repository, "status", "--porcelain"))
    rows = [
        {
            "path": path.relative_to(repository).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in _repository_lock_files(repository)
    ]
    return {
        "path": repository.as_posix(),
        "git_commit": commit,
        "dirty": dirty,
        "verifier_source_digest": canonical_digest(
            sorted(rows, key=lambda item: item["path"])
        ),
    }


def _load_groundtruth_bundle(
    groundtruth_root: Path, protocol_id: str
) -> dict[str, dict[str, Any]]:
    if not PROTOCOL_ID_RE.fullmatch(protocol_id):
        raise CapabilityImprovementError(f"invalid protocol ID: {protocol_id!r}")
    protocol_root = groundtruth_root.resolve() / protocol_id
    if not protocol_root.is_dir() or protocol_root.is_symlink():
        raise CapabilityImprovementError(
            f"ground-truth protocol directory is missing: {protocol_root}"
        )
    documents = {
        task: load_json_object(protocol_root / filename, label=f"{protocol_id} {task}")
        for task, filename in GROUNDTRUTH_FILES.items()
    }
    if any(
        "final_state_ids" in workflow or "modality" in workflow
        for workflow in documents["T3"].get("workflows", [])
    ):
        try:
            documents = migrate_connected_process_bundle(documents)
        except ConnectedProcessMigrationError as error:
            raise CapabilityImprovementError(
                f"{protocol_id} connected-process migration failed: {error}"
            ) from error
    validate_groundtruth_bundle(documents, protocol_id=protocol_id)
    return documents


def _find_source_manifest(root: Path, protocol_id: str) -> Path:
    matches = sorted(
        path.resolve()
        for path in root.rglob(f"{protocol_id}.json")
        if path.parent.name == "manifests" and path.is_file() and not path.is_symlink()
    )
    if not matches:
        raise CapabilityImprovementError(
            f"no audit input manifest found for {protocol_id}"
        )
    if len(matches) == 1:
        return matches[0]
    ranked: list[tuple[str, str, Path]] = []
    for path in matches:
        document = load_json_object(path, label=f"{protocol_id} audit input manifest")
        if document.get("protocol_id") != protocol_id:
            continue
        ranked.append(
            (
                normalize_timestamp(str(document.get("created_at", ""))),
                sha256_file(path),
                path,
            )
        )
    if not ranked:
        raise CapabilityImprovementError(
            f"no identity-matching audit input manifest found for {protocol_id}"
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2].as_posix()))
    latest_timestamp = ranked[-1][0]
    latest = [item for item in ranked if item[0] == latest_timestamp]
    if len({item[1] for item in latest}) != 1:
        raise CapabilityImprovementError(
            f"latest audit input manifests conflict for {protocol_id} at "
            f"{latest_timestamp}"
        )
    return latest[-1][2]


def _load_validated(
    path: Path, *, schema: str, digest_field: str, label: str
) -> dict[str, Any]:
    document = load_json_object(path, label=label)
    _validate_generated(document, schema, digest_field, label)
    return document


def _validate_generated(
    document: Mapping[str, Any], schema: str, digest_field: str, label: str
) -> None:
    validate_document(dict(document), improvement_schema_root() / schema, label=label)
    validate_digest(document, digest_field)


def _artifact_digest_record(
    path: Path, *, relative_to: Path, digest: str
) -> dict[str, str]:
    resolved = path.resolve()
    return {
        "path": resolved.relative_to(relative_to.resolve()).as_posix(),
        "sha256": sha256_file(resolved),
        "digest": digest,
    }


def _assert_artifact(root: Path, record: Mapping[str, Any], digest: str) -> None:
    path = root / str(record["path"])
    if sha256_file(path) != record["sha256"] or record["digest"] != digest:
        raise CapabilityImprovementError(
            f"KB experiment artifact changed: {record['path']}"
        )


def _mapped(mapping: Mapping[str, str], value: str, label: str) -> str:
    try:
        return mapping[value]
    except KeyError as error:
        raise CapabilityImprovementError(
            f"KB graph projection has unknown {label}: {value}"
        ) from error


def _redact_text(value: str, redactions: set[str]) -> str:
    result = value
    for redaction in sorted(
        (item for item in redactions if item), key=len, reverse=True
    ):
        result = re.sub(re.escape(redaction), "[DONOR]", result, flags=re.IGNORECASE)
    return result


def _regular_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if any(part in IGNORED_TREE_PARTS for part in path.parts):
            continue
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlink is forbidden: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files)


def _required_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise CapabilityImprovementError(f"{label} is missing or unsafe: {resolved}")
    return resolved


def _make_design_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            executable = bool(path.stat().st_mode & 0o111)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def _run_git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
