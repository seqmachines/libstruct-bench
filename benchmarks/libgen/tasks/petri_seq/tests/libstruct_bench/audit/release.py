from __future__ import annotations

import hashlib
import math
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .review import ReviewError, validate_review_decision
from .oligo_catalog import OligoCatalogError, build_oligo_outputs
from .groundtruth import GroundtruthValidationError, validate_cross_task_links


SCHEMA_FILES = {
    "manifest": "audit_input_manifest.schema.json",
    "audit": "protocol_audit.schema.json",
    "decision": "review_decision.schema.json",
    "application": "application_log.schema.json",
    "regression_fixture": "accepted_correction_regression.schema.json",
    "regression_results": "regression_results.schema.json",
    "checkpoint": "checkpoint_report.schema.json",
    "release_manifest": "groundtruth_release_manifest.schema.json",
    "t1": "final_library_groundtruth.schema.json",
    "t2": "oligo_groundtruth.schema.json",
    "t3": "library_generation_workflow.schema.json",
    "oligo_catalog": "oligo_catalog.schema.json",
    "oligo_build": "oligo_output_build.schema.json",
}


class ReleaseError(ValueError):
    """Raised when a candidate fails an evidence, review, or release gate."""


def build_release_manifest(
    *,
    spec_path: Path,
    artifact_root: Path,
    output_path: Path,
    spec_schema_path: Path,
    release_schema_path: Path,
) -> dict[str, Any]:
    """Verify every referenced artifact and produce a frozen release manifest."""

    spec_path = _file(spec_path, "release specification")
    artifact_root = _directory(artifact_root, "release artifact root")
    spec = load_json_object(spec_path, label="release specification")
    _validate(spec, _file(spec_schema_path, "release spec schema"), "release specification")
    if spec["release_status"] == "frozen":
        if spec["reviewed_protocol_count"] != spec["expected_protocol_count"]:
            raise ReleaseError("a frozen release must review every expected protocol")
        if len(spec["protocols"]) != spec["expected_protocol_count"]:
            raise ReleaseError("a frozen release must contain every expected protocol")
    if len({item["protocol_id"] for item in spec["protocols"]}) != len(spec["protocols"]):
        raise ReleaseError("release specification contains duplicate protocol IDs")
    for dataset in spec["source_datasets"]:
        if not re.fullmatch(r"[a-f0-9]{40,64}", dataset["revision"]):
            raise ReleaseError(
                f"dataset revision must be an immutable commit hash: {dataset['repository']}"
            )

    policies = [_hash_path(artifact_root, path) for path in spec["policy_paths"]]
    schemas: list[dict[str, str]] = []
    schema_by_name: dict[str, Path] = {}
    for relative in spec["schema_paths"]:
        path = _resolve(artifact_root, relative)
        filename = PurePosixPath(relative).name
        if filename in schema_by_name:
            raise ReleaseError(f"duplicate schema filename: {filename}")
        schema_by_name[filename] = path
        schemas.append({"path": relative, "sha256": sha256_file(path)})
    missing_schemas = sorted(set(SCHEMA_FILES.values()) - set(schema_by_name))
    if missing_schemas:
        raise ReleaseError(
            "release is missing required schemas: " + ", ".join(missing_schemas)
        )

    checkpoint_documents: list[tuple[str, dict[str, Any]]] = []
    checkpoints: list[dict[str, Any]] = []
    for relative in spec["checkpoint_paths"]:
        path = _resolve(artifact_root, relative)
        document = load_json_object(path, label="checkpoint report")
        _validate(
            document,
            schema_by_name[SCHEMA_FILES["checkpoint"]],
            "checkpoint report",
        )
        checkpoint_documents.append((relative, document))
        checkpoints.append(
            {
                "checkpoint_id": document["checkpoint_id"],
                "reviewed_protocol_count": document["reviewed_protocol_count"],
                "report_path": relative,
                "report_sha256": sha256_file(path),
            }
        )
    _validate_checkpoint_cadence(
        [document["reviewed_protocol_count"] for _, document in checkpoint_documents],
        spec["reviewed_protocol_count"],
    )
    _validate_checkpoint_chain(checkpoint_documents, artifact_root)

    protocol_records: list[dict[str, Any]] = []
    high_impact_protocols: set[str] = set()
    protocol_independent_ids: dict[str, list[str]] = {}
    pinned_datasets = {
        (item["provider"], item["repository"], item["revision"])
        for item in spec["source_datasets"]
    }
    for protocol in sorted(spec["protocols"], key=lambda item: item["protocol_id"]):
        record, high_impact, independent_ids = _verify_protocol(
            protocol=protocol,
            root=artifact_root,
            schemas=schema_by_name,
            pinned_datasets=pinned_datasets,
        )
        protocol_records.append(record)
        if high_impact:
            high_impact_protocols.add(protocol["protocol_id"])
        protocol_independent_ids[protocol["protocol_id"]] = independent_ids

    protocol_ids = [item["protocol_id"] for item in protocol_records]
    expected_selection = _independent_selection(
        protocol_ids=protocol_ids,
        high_impact=high_impact_protocols,
        seed=spec["independent_audit"]["seed"],
        fraction=spec["independent_audit"]["sample_fraction"],
    )
    supplied_selection = set(spec["independent_audit"]["selected_protocol_ids"])
    if supplied_selection != expected_selection:
        raise ReleaseError(
            "independent-audit selection is not reproducible; "
            f"expected={sorted(expected_selection)}, supplied={sorted(supplied_selection)}"
        )
    for protocol_id in expected_selection:
        if not protocol_independent_ids.get(protocol_id):
            raise ReleaseError(
                f"protocol {protocol_id} requires an independent Codex audit"
            )

    final_checkpoint = max(
        checkpoint_documents, key=lambda item: item[1]["reviewed_protocol_count"]
    )[1]
    _verify_final_checkpoint_coverage(
        checkpoint=final_checkpoint,
        specification=spec,
        root=artifact_root,
        regression_schema=schema_by_name[SCHEMA_FILES["regression_results"]],
    )
    if final_checkpoint["metrics"]["new_regression_count"] not in {0, None}:
        raise ReleaseError("release has newly introduced regressions")
    if spec["release_status"] == "frozen" and final_checkpoint["metrics"]["new_regression_count"] is None:
        raise ReleaseError("a frozen release requires recorded regression results")

    oligo_outputs = _verify_oligo_outputs(
        specification=spec["oligo_outputs"],
        root=artifact_root,
        schemas=schema_by_name,
        protocol_records=protocol_records,
    )

    manifest = {
        "release_id": spec["release_id"],
        "release_status": spec["release_status"],
        "created_at": spec["created_at"],
        "generated_by": spec["generated_by"],
        "source_datasets": spec["source_datasets"],
        "policies": policies,
        "schemas": schemas,
        "checkpoints": sorted(
            checkpoints, key=lambda item: item["reviewed_protocol_count"]
        ),
        "independent_audit": spec["independent_audit"],
        "protocols": protocol_records,
        "oligo_outputs": oligo_outputs,
        "metrics": final_checkpoint["metrics"],
    }
    _validate(manifest, _file(release_schema_path, "release schema"), "release manifest")
    output_path = output_path.expanduser().resolve()
    _reject_output(output_path)
    if output_path.exists():
        raise ReleaseError(f"release manifest already exists: {output_path}")
    write_json_atomic(output_path, manifest)
    return manifest


def _verify_final_checkpoint_coverage(
    *,
    checkpoint: dict[str, Any],
    specification: dict[str, Any],
    root: Path,
    regression_schema: Path,
) -> None:
    expected = {
        "proposal_artifacts": sorted(
            sha256_file(_resolve(root, item["path"]))
            for protocol in specification["protocols"]
            for item in protocol["audits"]
        ),
        "decision_artifacts": sorted(
            sha256_file(_resolve(root, item["path"]))
            for protocol in specification["protocols"]
            for item in protocol["decisions"]
        ),
        "application_artifacts": sorted(
            sha256_file(_resolve(root, item["path"]))
            for protocol in specification["protocols"]
            for item in protocol["applications"]
        ),
    }
    for key, expected_hashes in expected.items():
        actual_hashes = sorted(item["sha256"] for item in checkpoint[key])
        if actual_hashes != expected_hashes:
            raise ReleaseError(
                f"final checkpoint {key} do not cover the release specification"
            )

    artifact = checkpoint["regression_results_artifact"]
    if artifact is None:
        if expected["application_artifacts"]:
            raise ReleaseError(
                "final checkpoint is missing regression results for applications"
            )
        return
    path = _resolve_recorded_path(root, artifact["path"])
    if sha256_file(path) != artifact["sha256"]:
        raise ReleaseError("final checkpoint regression result hash is stale")
    document = load_json_object(path, label="final regression results")
    _validate(document, regression_schema, "final regression results")
    if sorted(document["new_regressions"]) != sorted(
        checkpoint.get("new_regressions") or []
    ):
        raise ReleaseError(
            "final checkpoint regression summary does not match its result artifact"
        )
    if document["failed_count"] != len(document["new_regressions"]):
        raise ReleaseError("final regression result counts are inconsistent")


def _verify_oligo_outputs(
    *,
    specification: dict[str, str],
    root: Path,
    schemas: dict[str, Path],
    protocol_records: list[dict[str, Any]],
) -> dict[str, str]:
    catalog_path = _resolve(root, specification["catalog_path"])
    tsv_path = _resolve(root, specification["tsv_path"])
    metadata_path = _resolve(root, specification["build_metadata_path"])
    catalog = load_json_object(catalog_path, label="canonical oligo catalog")
    metadata = load_json_object(metadata_path, label="oligo output build metadata")
    _validate(
        catalog,
        schemas[SCHEMA_FILES["oligo_catalog"]],
        "canonical oligo catalog",
    )
    _validate(
        metadata,
        schemas[SCHEMA_FILES["oligo_build"]],
        "oligo output build metadata",
    )
    catalog_sha = sha256_file(catalog_path)
    tsv_sha = sha256_file(tsv_path)
    if metadata["catalog"]["sha256"] != catalog_sha:
        raise ReleaseError("oligo catalog hash does not match its build metadata")
    if metadata["tsv"]["sha256"] != tsv_sha:
        raise ReleaseError("oligo TSV hash does not match its build metadata")

    t2_paths: list[Path] = []
    decisions: dict[str, list[str]] = {}
    expected_inputs: dict[str, str] = {}
    for record in protocol_records:
        t2 = next(
            (item for item in record["artifacts"] if item["task"] == "T2"),
            None,
        )
        if t2 is None:
            continue
        protocol_id = record["protocol_id"]
        t2_paths.append(_resolve(root, t2["path"]))
        decisions[protocol_id] = sorted(record["decision_ids"])
        expected_inputs[protocol_id] = t2["sha256"]
    if not t2_paths:
        raise ReleaseError("release has no T2 artifacts for the audited oligo outputs")
    metadata_inputs: dict[str, dict[str, Any]] = {}
    for item in metadata["inputs"]:
        protocol_id = item["protocol_id"]
        if protocol_id in metadata_inputs:
            raise ReleaseError(
                f"oligo build metadata duplicates protocol {protocol_id}"
            )
        metadata_inputs[protocol_id] = item
    if set(metadata_inputs) != set(expected_inputs):
        raise ReleaseError(
            "oligo build inputs do not match released T2 artifacts; "
            f"expected={sorted(expected_inputs)}, got={sorted(metadata_inputs)}"
        )
    for protocol_id, expected_sha in expected_inputs.items():
        item = metadata_inputs[protocol_id]
        if item["sha256"] != expected_sha:
            raise ReleaseError(
                f"oligo build T2 hash is stale for {protocol_id}"
            )
        if sorted(item["decision_ids"]) != decisions[protocol_id]:
            raise ReleaseError(
                f"oligo build decisions are stale for {protocol_id}"
            )

    try:
        with tempfile.TemporaryDirectory(prefix="libstruct-oligo-release-") as temporary:
            rebuilt = build_oligo_outputs(
                t2_paths=t2_paths,
                decision_ids_by_protocol=decisions,
                output_dir=Path(temporary) / "rebuilt",
                t2_schema_path=schemas[SCHEMA_FILES["t2"]],
                catalog_schema_path=schemas[SCHEMA_FILES["oligo_catalog"]],
                metadata_schema_path=schemas[SCHEMA_FILES["oligo_build"]],
                created_at=metadata["created_at"],
            )
            if rebuilt.catalog_path.read_bytes() != catalog_path.read_bytes():
                raise ReleaseError(
                    "oligo catalog is not the deterministic aggregate of released T2 artifacts"
                )
            if rebuilt.tsv_path.read_bytes() != tsv_path.read_bytes():
                raise ReleaseError(
                    "oligo TSV is not the deterministic projection of released T2 artifacts"
                )
    except OligoCatalogError as error:
        raise ReleaseError(f"cannot reproduce audited oligo outputs: {error}") from error
    return {
        "catalog_path": specification["catalog_path"],
        "catalog_sha256": catalog_sha,
        "tsv_path": specification["tsv_path"],
        "tsv_sha256": tsv_sha,
        "build_metadata_path": specification["build_metadata_path"],
        "build_metadata_sha256": sha256_file(metadata_path),
    }


def _verify_protocol(
    *,
    protocol: dict[str, Any],
    root: Path,
    schemas: dict[str, Path],
    pinned_datasets: set[tuple[str, str, str]],
) -> tuple[dict[str, Any], bool, list[str]]:
    protocol_id = protocol["protocol_id"]
    manifest_path = _resolve(root, protocol["input_manifest_path"])
    manifest = load_json_object(manifest_path, label="audit input manifest")
    _validate(
        manifest,
        schemas[SCHEMA_FILES["manifest"]],
        "audit input manifest",
    )
    if manifest["protocol_id"] != protocol_id:
        raise ReleaseError(f"input manifest protocol mismatch for {protocol_id}")
    for source in manifest["sources"]:
        reference = source.get("dataset_reference")
        if source["approval_status"] == "included":
            identity = (
                reference["provider"],
                reference["repository"],
                reference["revision"],
            )
            if identity not in pinned_datasets:
                raise ReleaseError(
                    f"manifest source dataset is not pinned by release for {protocol_id}: "
                    f"{source['source_id']}"
                )
    artifact_chain = {
        source["source_id"]: source["sha256"]
        for source in manifest["sources"]
        if source["approval_status"] == "included"
        and source["role"] == "current_benchmark_record"
    }
    manifest_sha256 = sha256_file(manifest_path)
    audits: dict[str, tuple[Path, dict[str, Any]]] = {}
    independent_ids: list[str] = []
    for item in protocol["audits"]:
        path = _resolve(root, item["path"])
        document = load_json_object(path, label="audit proposal")
        _validate(document, schemas[SCHEMA_FILES["audit"]], "audit proposal")
        if document["protocol_id"] != protocol_id or document["audit_id"] != item["id"]:
            raise ReleaseError(f"audit identity mismatch for {protocol_id}")
        if item["id"] in audits:
            raise ReleaseError(f"duplicate audit ID for {protocol_id}: {item['id']}")
        audits[item["id"]] = (path, document)
        if document["input_manifest_sha256"] != manifest_sha256:
            raise ReleaseError(f"audit input manifest is stale for {protocol_id}")
        if document["run"]["review_mode"] == "independent":
            if document["run"]["agent"] != "codex":
                raise ReleaseError(
                    f"independent audit must be performed by Codex for {protocol_id}"
                )
            independent_ids.append(item["id"])
        elif document["run"]["agent"] != "claude-code":
            raise ReleaseError(
                f"primary comparison audit must be performed by Claude Code for {protocol_id}"
            )

    decisions: dict[str, tuple[Path, dict[str, Any]]] = {}
    for item in protocol["decisions"]:
        path = _resolve(root, item["path"])
        document = load_json_object(path, label="review decision")
        audit_id = document.get("audit_id")
        if item["id"] != document.get("decision_id") or audit_id not in audits:
            raise ReleaseError(f"decision identity mismatch for {protocol_id}")
        if audit_id in decisions:
            raise ReleaseError(f"multiple decisions for audit {audit_id}")
        try:
            proposal, validated = validate_review_decision(
                proposal_path=audits[audit_id][0],
                decision_path=path,
                proposal_schema_path=schemas[SCHEMA_FILES["audit"]],
                decision_schema_path=schemas[SCHEMA_FILES["decision"]],
            )
        except ReviewError as error:
            raise ReleaseError(str(error)) from error
        decisions[audit_id] = (path, validated)
    if set(decisions) != set(audits):
        raise ReleaseError(f"every audit for {protocol_id} requires one human decision")

    high_impact = False
    unresolved: set[str] = set()
    required_applications: dict[str, set[str]] = {}
    required_application_patches: dict[str, dict[str, list[dict[str, Any]]]] = {}
    required_application_sources: dict[str, dict[str, tuple[str, str]]] = {}
    for audit_id, (_, proposal) in audits.items():
        decision = decisions[audit_id][1]
        issues = {issue["issue_id"]: issue for issue in proposal["issues"]}
        required_for_decision: set[str] = set()
        patches_for_decision: dict[str, list[dict[str, Any]]] = {}
        sources_for_decision: dict[str, tuple[str, str]] = {}
        for item in decision["issue_decisions"]:
            issue = issues[item["issue_id"]]
            severity = item.get("severity", issue["severity"])
            disposition = item["disposition"]
            if disposition in {"accept", "modify"} and severity in {"blocker", "high"}:
                high_impact = True
            if (
                disposition == "modify"
                or (
                    disposition == "accept"
                    and issue["recommendation"] == "propose_change"
                )
            ) and issue["target"]["kind"] in {
                "groundtruth_record",
                "new_groundtruth_record",
            }:
                required_for_decision.add(issue["issue_id"])
                patches_for_decision[issue["issue_id"]] = (
                    item["replacement_patch"]
                    if disposition == "modify"
                    else issue["proposed_patch"]
                )
                sources_for_decision[issue["issue_id"]] = (
                    issue["target"]["artifact_source_id"],
                    issue["target"]["kind"],
                )
            if disposition == "unresolved":
                unresolved.add(issue["issue_id"])
                if severity in {"blocker", "high"} and protocol["task_dispositions"].get(issue["task"]) == "included":
                    raise ReleaseError(
                        f"{protocol_id} includes {issue['task']} with unresolved {severity} issue {issue['issue_id']}"
                    )
            if disposition == "exclude":
                scope = item["exclusion_scope"]
                if scope == "protocol" and any(
                    value == "included" for value in protocol["task_dispositions"].values()
                ):
                    raise ReleaseError(f"protocol-level exclusion conflicts with included task for {protocol_id}")
                if scope == "task" and protocol["task_dispositions"].get(issue["task"]) == "included":
                    raise ReleaseError(f"task exclusion conflicts with included {issue['task']} for {protocol_id}")
        required_applications[decision["decision_id"]] = required_for_decision
        required_application_patches[decision["decision_id"]] = patches_for_decision
        required_application_sources[decision["decision_id"]] = sources_for_decision
    if unresolved != set(protocol["unresolved_issue_ids"]):
        raise ReleaseError(f"unresolved issue list is stale for {protocol_id}")

    application_ids: list[str] = []
    applications_by_decision: set[str] = set()
    for item in protocol["applications"]:
        path = _resolve(root, item["path"])
        document = load_json_object(path, label="application log")
        _validate(document, schemas[SCHEMA_FILES["application"]], "application log")
        if document["protocol_id"] != protocol_id or document["application_id"] != item["id"]:
            raise ReleaseError(f"application identity mismatch for {protocol_id}")
        audit_id = document["audit_id"]
        if audit_id not in audits:
            raise ReleaseError(
                f"application references unknown audit for {protocol_id}: {audit_id}"
            )
        decision = decisions[audit_id][1]
        decision_path = decisions[audit_id][0]
        proposal_path = audits[audit_id][0]
        decision_id = document["decision_id"]
        if decision_id != decision["decision_id"]:
            raise ReleaseError(
                f"application decision mismatch for {protocol_id}: {decision_id}"
            )
        if decision_id in applications_by_decision:
            raise ReleaseError(
                f"multiple application logs for decision {decision_id}"
            )
        if document["proposal_sha256"] != sha256_file(proposal_path):
            raise ReleaseError(f"application proposal hash is stale for {protocol_id}")
        if document["decision_sha256"] != sha256_file(decision_path):
            raise ReleaseError(f"application decision hash is stale for {protocol_id}")
        expected_issue_ids = required_applications[decision_id]
        if set(document["applied_issue_ids"]) != expected_issue_ids:
            raise ReleaseError(
                f"application issue set is stale for {decision_id}; "
                f"expected={sorted(expected_issue_ids)}, "
                f"got={sorted(document['applied_issue_ids'])}"
            )
        application_artifacts = {
            artifact["source_id"]: artifact for artifact in document["artifacts"]
        }
        if len(application_artifacts) != len(document["artifacts"]):
            raise ReleaseError(
                f"application contains duplicate artifact source IDs: {decision_id}"
            )
        expected_application_sources = {
            item["source_id"] for item in audits[audit_id][1]["baseline_artifacts"]
        }
        expected_application_sources.update(
            source_id
            for source_id, target_kind in required_application_sources[
                decision_id
            ].values()
            if target_kind == "new_groundtruth_record"
        )
        if set(application_artifacts) != expected_application_sources:
            raise ReleaseError(
                f"application artifact set is stale for {decision_id}; "
                f"expected={sorted(expected_application_sources)}, "
                f"got={sorted(application_artifacts)}"
            )
        for artifact in document["artifacts"]:
            source_id = artifact["source_id"]
            baseline_state = artifact["baseline_state"]
            expected_parent = artifact_chain.get(source_id)
            if baseline_state == "present":
                if expected_parent is None:
                    raise ReleaseError(
                        f"application baseline has no provenance for {decision_id}: {source_id}"
                    )
                if artifact["baseline_sha256"] != expected_parent:
                    raise ReleaseError(
                        f"application baseline chain is stale for {decision_id}: {source_id}"
                    )
            else:
                if expected_parent is not None:
                    raise ReleaseError(
                        f"application tries to recreate an existing artifact for {decision_id}: {source_id}"
                    )
                accepted_new_sources = {
                    value[0]
                    for value in required_application_sources[decision_id].values()
                    if value[1] == "new_groundtruth_record"
                }
                if source_id not in accepted_new_sources:
                    raise ReleaseError(
                        f"application creates an unapproved artifact for {decision_id}: {source_id}"
                    )
            candidate_path = _resolve_relative(
                path.parent, artifact["candidate_path"], root
            )
            if sha256_file(candidate_path) != artifact["candidate_sha256"]:
                raise ReleaseError(
                    f"application candidate hash is stale for {decision_id}: "
                    f"{artifact['source_id']}"
                )
            artifact_chain[source_id] = artifact["candidate_sha256"]
        fixture_issue_ids: set[str] = set()
        for relative_fixture in document["regression_fixtures"]:
            fixture_path = _resolve_relative(path.parent, relative_fixture, root)
            fixture = load_json_object(
                fixture_path, label="accepted correction regression fixture"
            )
            _validate(
                fixture,
                schemas[SCHEMA_FILES["regression_fixture"]],
                "accepted correction regression fixture",
            )
            issue_id = fixture["issue_id"]
            if (
                fixture["protocol_id"] != protocol_id
                or fixture["audit_id"] != audit_id
                or fixture["decision_id"] != decision_id
                or issue_id not in expected_issue_ids
            ):
                raise ReleaseError(
                    f"regression fixture identity mismatch for {decision_id}"
                )
            if issue_id in fixture_issue_ids:
                raise ReleaseError(
                    f"duplicate regression fixture for issue {issue_id}"
                )
            source_id = fixture["artifact_source_id"]
            artifact = application_artifacts.get(source_id)
            if artifact is None:
                raise ReleaseError(
                    f"regression fixture targets unknown application artifact: {source_id}"
                )
            if fixture["baseline_state"] != artifact["baseline_state"]:
                raise ReleaseError(
                    f"regression fixture baseline state is stale for issue {issue_id}"
                )
            if (
                fixture["baseline_state"] == "present"
                and fixture["baseline_sha256"] != artifact["baseline_sha256"]
            ):
                raise ReleaseError(
                    f"regression fixture baseline is stale for issue {issue_id}"
                )
            expected_source_id = required_application_sources[decision_id][
                issue_id
            ][0]
            if source_id != expected_source_id:
                raise ReleaseError(
                    f"regression fixture source is stale for issue {issue_id}"
                )
            if fixture["patch"] != required_application_patches[decision_id][issue_id]:
                raise ReleaseError(
                    f"regression fixture patch is stale for issue {issue_id}"
                )
            fixture_issue_ids.add(issue_id)
        if fixture_issue_ids != expected_issue_ids:
            raise ReleaseError(
                f"regression fixtures do not cover applied issues for {decision_id}"
            )
        applications_by_decision.add(decision_id)
        application_ids.append(item["id"])
    missing_applications = sorted(
        decision_id
        for decision_id, issue_ids in required_applications.items()
        if issue_ids and decision_id not in applications_by_decision
    )
    if missing_applications:
        raise ReleaseError(
            "accepted ground-truth changes lack deterministic application logs: "
            + ", ".join(missing_applications)
        )

    artifacts: list[dict[str, str]] = []
    artifact_tasks: set[str] = set()
    artifact_documents: dict[str, dict[str, Any]] = {}
    for item in protocol["artifacts"]:
        path = _resolve(root, item["path"])
        schema_key = {
            "T1": "t1",
            "T2": "t2",
            "T3": "t3",
            "oligo_catalog": "oligo_catalog",
        }[item["task"]]
        schema = schemas.get(SCHEMA_FILES[schema_key])
        if schema is None:
            raise ReleaseError(
                f"artifact schema not pinned: {SCHEMA_FILES[schema_key]}"
            )
        document = load_json_object(path, label="ground-truth artifact")
        _validate(document, schema, f"{protocol_id} {item['task']} artifact")
        if item["task"] != "oligo_catalog" and document.get("protocol_id") != protocol_id:
            raise ReleaseError(f"artifact protocol mismatch for {protocol_id}")
        if item["task"] in artifact_tasks:
            raise ReleaseError(
                f"duplicate {item['task']} artifact for {protocol_id}"
            )
        artifact_tasks.add(item["task"])
        artifact_documents[item["task"]] = document
        _verify_groundtruth_semantics(
            protocol_id=protocol_id,
            task=item["task"],
            document=document,
        )
        artifact_sha256 = sha256_file(path)
        source_id = item["artifact_source_id"]
        expected_artifact_sha256 = artifact_chain.get(source_id)
        if expected_artifact_sha256 is None:
            raise ReleaseError(
                f"ground-truth artifact has no baseline/application provenance for "
                f"{protocol_id}: {source_id}"
            )
        if artifact_sha256 != expected_artifact_sha256:
            raise ReleaseError(
                f"ground-truth artifact does not match the latest deterministic "
                f"candidate for {protocol_id}: {source_id}"
            )
        artifacts.append(
            {
                "task": item["task"],
                "path": item["path"],
                "sha256": artifact_sha256,
                "artifact_source_id": source_id,
            }
        )
    for task, disposition in protocol["task_dispositions"].items():
        if disposition == "included" and task not in artifact_tasks:
            raise ReleaseError(f"included task {task} has no artifact for {protocol_id}")
    try:
        validate_cross_task_links(
            {
                task: document
                for task, document in artifact_documents.items()
                if task in {"T1", "T2", "T3"}
            }
        )
    except GroundtruthValidationError as error:
        raise ReleaseError(str(error)) from error

    record = {
        "protocol_id": protocol_id,
        "task_dispositions": protocol["task_dispositions"],
        "input_manifest_sha256": manifest_sha256,
        "audit_ids": list(audits),
        "independent_audit_ids": sorted(independent_ids),
        "decision_ids": [decisions[audit_id][1]["decision_id"] for audit_id in audits],
        "application_ids": application_ids,
        "artifacts": artifacts,
        "unresolved_issue_ids": sorted(unresolved),
        "limitations": protocol["limitations"],
    }
    return record, high_impact, sorted(independent_ids)


def _verify_groundtruth_semantics(
    *,
    protocol_id: str,
    task: str,
    document: dict[str, Any],
) -> None:
    if task == "T1":
        for library_index, library in enumerate(document["libraries"]):
            _unique_ids(
                library["segments"],
                "segment_id",
                f"{protocol_id} T1 library at index {library_index}",
            )
        return
    if task == "T2":
        _unique_ids(document["oligos"], "oligo_id", f"{protocol_id} T2")
        return
    if task == "T3":
        _unique_ids(document["workflows"], "workflow_id", f"{protocol_id} T3")
        for workflow in document["workflows"]:
            _unique_ids(workflow["states"], "state_id", f"{protocol_id} T3 workflow {workflow['workflow_id']} states")
            _unique_ids(workflow["transitions"], "transition_id", f"{protocol_id} T3 workflow {workflow['workflow_id']} transitions")


def _unique_ids(values: list[dict[str, Any]], key: str, label: str) -> None:
    identifiers = [item[key] for item in values]
    if len(identifiers) != len(set(identifiers)):
        raise ReleaseError(f"{label} contains duplicate {key} values")


def _independent_selection(
    *, protocol_ids: list[str], high_impact: set[str], seed: str, fraction: float
) -> set[str]:
    remaining = sorted(set(protocol_ids) - high_impact)
    sample_size = math.ceil(len(remaining) * fraction)
    ranked = sorted(
        remaining,
        key=lambda protocol_id: hashlib.sha256(
            f"{seed}:{protocol_id}".encode("utf-8")
        ).hexdigest(),
    )
    return set(high_impact) | set(ranked[:sample_size])


def _validate_checkpoint_cadence(counts: list[int], reviewed: int) -> None:
    if len(counts) != len(set(counts)):
        raise ReleaseError("checkpoint reports contain duplicate reviewed counts")
    expected = {0, reviewed}
    expected.update(range(5, reviewed + 1, 5))
    if set(counts) != expected:
        raise ReleaseError(
            f"checkpoint cadence must be 0, every 5, and final; expected={sorted(expected)}, got={sorted(counts)}"
        )


def _validate_checkpoint_chain(
    checkpoints: list[tuple[str, dict[str, Any]]], root: Path
) -> None:
    ordered = sorted(
        checkpoints, key=lambda item: item[1]["reviewed_protocol_count"]
    )
    for index, (_, document) in enumerate(ordered):
        previous = document.get("previous_checkpoint")
        deltas = document.get("deltas")
        if index == 0:
            if previous is not None or deltas is not None:
                raise ReleaseError("checkpoint 0 must not reference a previous checkpoint")
            continue
        previous_path = _resolve(root, ordered[index - 1][0])
        if previous is None or previous["sha256"] != sha256_file(previous_path):
            raise ReleaseError(
                f"checkpoint {document['checkpoint_id']} does not hash the prior checkpoint"
            )
        if deltas is None:
            raise ReleaseError(
                f"checkpoint {document['checkpoint_id']} is missing delta metrics"
            )


def _hash_path(root: Path, relative: str) -> dict[str, str]:
    path = _resolve(root, relative)
    return {"path": relative, "sha256": sha256_file(path)}


def _resolve(root: Path, relative: str) -> Path:
    portable = PurePosixPath(relative)
    if portable.is_absolute() or not portable.parts or any(part in {".", ".."} for part in portable.parts):
        raise ReleaseError(f"unsafe release artifact path: {relative}")
    path = root.joinpath(*portable.parts).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReleaseError(f"release artifact is missing or escapes root: {relative}")
    return path


def _resolve_relative(base: Path, relative: str, root: Path) -> Path:
    portable = PurePosixPath(relative)
    if portable.is_absolute() or not portable.parts or any(
        part in {".", ".."} for part in portable.parts
    ):
        raise ReleaseError(f"unsafe relative artifact path: {relative}")
    path = base.joinpath(*portable.parts).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReleaseError(
            f"relative artifact is missing or escapes release root: {relative}"
        )
    return path


def _resolve_recorded_path(root: Path, value: str) -> Path:
    recorded = Path(value).expanduser()
    path = recorded.resolve() if recorded.is_absolute() else (root / recorded).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReleaseError(
            f"recorded checkpoint artifact is missing or escapes root: {value}"
        )
    return path


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise ReleaseError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReleaseError(f"{label} does not exist: {path}")
    return resolved


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ReleaseError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise ReleaseError("private release output must not be written inside libstruct-bench")
