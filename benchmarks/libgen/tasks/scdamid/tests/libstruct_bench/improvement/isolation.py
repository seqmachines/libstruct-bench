from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from libstruct_bench.audit.artifacts import (
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    copy_capability_pack,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    validate_capability_pack,
    with_digest,
)
from .agent import independent_review_instruction, proposal_instruction
from .governance import (
    assert_transfer_panel_isolation,
    validate_transfer_access_policy,
)
from .exemplar_memory import exemplar_memory_record, validate_exemplar_memory


WORKSPACE_SCHEMA_VERSION = "libstruct.libgen_worker_workspace.v1"
WORKSPACE_MODES = {
    "improvement_worker",
    "revision_worker",
    "independent_critic",
    "human_review_console",
    "agent_protocol_proposer",
    "agent_protocol_revision",
    "agent_protocol_critic",
}

_UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS = frozenset(
    {
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
    }
)
_REGEX_LOOKAROUND_TOKENS = ("(?=", "(?!", "(?<=", "(?<!")


def prepare_isolated_worker_workspace(
    *,
    experiment_manifest: Mapping[str, Any],
    packet_path: Path,
    parent_pack_root: Path,
    access_policy_path: Path,
    output_root: Path,
    mode: str,
    proposal_path: Path | None = None,
    candidate_root: Path | None = None,
    decision_path: Path | None = None,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
    validation_feedback_path: Path | None = None,
) -> dict[str, Any]:
    """Copy only packet-allowlisted inputs into a host-path-free workspace."""

    if mode not in WORKSPACE_MODES:
        raise CapabilityImprovementError(f"unknown improvement workspace mode: {mode}")
    if mode == "improvement_worker" and validation_feedback_path is None:
        raise CapabilityImprovementError(
            "an improvement worker requires the canonical prior-checkpoint "
            "validation aggregate"
        )
    packet = load_and_validate(
        packet_path,
        schema_filename="batch_packet.schema.json",
        digest_field="packet_digest",
        label="capability batch packet",
    )
    policy = validate_transfer_access_policy(access_policy_path)
    if packet["experiment_digest"] != experiment_manifest["experiment_digest"]:
        raise CapabilityImprovementError("worker packet belongs to another experiment")
    expected_policy = experiment_manifest["frozen_retrospective_transfer_panel"][
        "access_policy"
    ]["digest"]
    if policy["policy_digest"] != expected_policy:
        raise CapabilityImprovementError("worker access policy is stale")
    if packet["transfer_access_policy_digest"] != policy["policy_digest"]:
        raise CapabilityImprovementError(
            "worker packet references another access policy"
        )
    if packet["eligibility_status"] != "eligible_for_improvement":
        raise CapabilityImprovementError(
            "worker packet is not eligible for improvement"
        )
    assert_transfer_panel_isolation(
        protocol_ids=packet["protocol_ids"],
        artifacts=packet["artifacts"],
        policy=policy,
    )
    parent_memory_root, parent_memory, source_checkpoint = (
        _resolve_parent_exemplar_memory(
            parent_pack_root,
            experiment_digest=experiment_manifest["experiment_digest"],
        )
    )
    validation_feedback: dict[str, str] | None = None
    validated_feedback: dict[str, Any] | None = None
    if validation_feedback_path is not None:
        if mode != "improvement_worker":
            raise CapabilityImprovementError(
                "validation macro feedback may be staged only for an improvement worker"
            )
        experiment_root = access_policy_path.expanduser().resolve().parents[1]
        from .validation import (
            VALIDATION_BATCH_GATE,
            build_validation_feedback_projection,
            validate_validation_access_policy,
            validate_validation_aggregate,
        )

        validation_reference = experiment_manifest["validation_panel"]["access_policy"]
        validation_policy_path = experiment_root / validation_reference["path"]
        validation_policy = validate_validation_access_policy(validation_policy_path)
        if (
            validation_policy["policy_digest"] != validation_reference["digest"]
            or sha256_file(validation_policy_path) != validation_reference["sha256"]
        ):
            raise CapabilityImprovementError(
                "validation access-policy reference is stale"
            )
        expected_label = VALIDATION_BATCH_GATE.get(packet["batch_id"])
        canonical_feedback_path = (
            experiment_root / "validation" / "aggregates" / f"{expected_label}.json"
        )
        if validation_feedback_path.expanduser().resolve() != canonical_feedback_path:
            raise CapabilityImprovementError(
                "validation feedback is outside its canonical aggregate path"
            )
        parent = validate_capability_pack(parent_pack_root)
        validated_feedback = validate_validation_aggregate(
            canonical_feedback_path,
            experiment_digest=experiment_manifest["experiment_digest"],
            validation_access_policy=validation_policy,
            expected_checkpoint_label=expected_label,
            expected_pack_digest=parent["pack_digest"],
            expected_checkpoint_root=(
                experiment_root / "checkpoints" / str(expected_label)
            ),
        )
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise CapabilityImprovementError(
            f"worker workspace already exists: {output_root}"
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.building-", dir=output_root.parent
        )
    )
    try:
        inputs = temporary / "inputs"
        copy_capability_pack(parent_pack_root, inputs / "capability_pack", freeze=True)
        shutil.copytree(parent_memory_root, inputs / "exemplar_memory")
        copied_memory = validate_exemplar_memory(
            inputs / "exemplar_memory",
            expected_count=parent_memory["exemplar_count"],
        )
        if copied_memory["memory_digest"] != parent_memory["memory_digest"]:
            raise CapabilityImprovementError(
                "staged exemplar memory differs from its parent checkpoint"
            )
        staged_memory_record = _workspace_exemplar_memory_record(
            inputs / "exemplar_memory",
            source_checkpoint=source_checkpoint,
        )
        safe_artifacts = []
        staged_files: list[dict[str, str]] = []
        if validated_feedback is not None:
            destination = inputs / "validation_feedback.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            projection = build_validation_feedback_projection(validated_feedback)
            write_json_atomic(destination, projection, mode=0o444)
            feedback_sha256 = sha256_file(destination)
            staged_files.append(
                {
                    "path": "inputs/validation_feedback.json",
                    "sha256": feedback_sha256,
                    "role": "validation_macro_aggregate",
                }
            )
            validation_feedback = {
                "checkpoint_label": validated_feedback["checkpoint_label"],
                "aggregate_digest": validated_feedback["aggregate_digest"],
                "aggregate_sha256": sha256_file(validation_feedback_path),
                "projection_sha256": feedback_sha256,
            }
        for index, artifact in enumerate(packet["artifacts"], start=1):
            if artifact["visibility"] != "agent_after_reveal":
                continue
            raw_source = Path(artifact["path"]).expanduser()
            if raw_source.is_symlink() or not raw_source.is_file():
                raise CapabilityImprovementError(
                    f"worker artifact is not a regular file: {artifact['path']}"
                )
            source = raw_source.resolve()
            suffix = "".join(source.suffixes[-2:]) or ".bin"
            relative = (
                Path("inputs")
                / "evidence"
                / artifact["protocol_id"]
                / (f"{index:04d}-{artifact['sha256'][:16]}{suffix}")
            )
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if sha256_file(destination) != artifact["sha256"]:
                raise CapabilityImprovementError("staged worker artifact changed hash")
            destination.chmod(0o444)
            view = copy.deepcopy(artifact)
            view["path"] = relative.as_posix()
            safe_artifacts.append(view)
            staged_files.append(
                {
                    "path": relative.as_posix(),
                    "sha256": artifact["sha256"],
                    "role": artifact["role"],
                }
            )
        packet_view = {
            key: copy.deepcopy(value)
            for key, value in packet.items()
            if key not in {"artifacts", "packet_digest"}
        }
        packet_view["artifacts"] = safe_artifacts
        packet_view["source_packet_digest"] = packet["packet_digest"]
        packet_view_path = inputs / "packet_view.json"
        write_json_atomic(packet_view_path, packet_view, mode=0o444)
        staged_files.append(
            {
                "path": "inputs/packet_view.json",
                "sha256": sha256_file(packet_view_path),
                "role": "batch_packet_view",
            }
        )
        review_materials_staged = False
        revision_lineage_staged = False
        if (
            proposal_path is not None
            or candidate_root is not None
            or decision_path is not None
            or prior_proposal_path is not None
            or revision_decision_path is not None
        ):
            if mode == "improvement_worker":
                raise CapabilityImprovementError(
                    "proposal materials cannot be staged for an improvement worker"
                )
            if proposal_path is None or candidate_root is None:
                raise CapabilityImprovementError(
                    "review staging requires both proposal and candidate root"
                )
            if (prior_proposal_path is None) != (revision_decision_path is None):
                raise CapabilityImprovementError(
                    "revised review staging requires both prior proposal and "
                    "revision decision"
                )
            if mode == "revision_worker" and prior_proposal_path is not None:
                raise CapabilityImprovementError(
                    "a revision worker cannot stage a second revision round"
                )
            from .workflow import validate_capability_proposal

            validate_capability_proposal(
                experiment_manifest=experiment_manifest,
                access_policy_path=access_policy_path,
                proposal_path=proposal_path,
                candidate_root=candidate_root,
                parent_pack_root=parent_pack_root,
                packet_path=packet_path,
                prior_proposal_path=prior_proposal_path,
                revision_decision_path=revision_decision_path,
            )
            if mode == "revision_worker":
                if decision_path is None:
                    raise CapabilityImprovementError(
                        "revision worker requires the recorded revision decision"
                    )
                from .workflow import validate_capability_decision

                _, revision_decision = validate_capability_decision(
                    proposal_path=proposal_path,
                    decision_path=decision_path,
                    require_final=False,
                )
                if revision_decision["review_state"] != "revision_requested":
                    raise CapabilityImprovementError(
                        "revision worker requires a revision_requested decision"
                    )
            elif decision_path is not None:
                raise CapabilityImprovementError(
                    "a decision may be staged only for a revision worker"
                )
            review_root = inputs / "review"
            staged_proposal = review_root / "proposal.json"
            staged_proposal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(proposal_path, staged_proposal)
            staged_proposal.chmod(0o444)
            staged_files.append(
                {
                    "path": staged_proposal.relative_to(temporary).as_posix(),
                    "sha256": sha256_file(staged_proposal),
                    "role": "capability_proposal",
                }
            )
            if decision_path is not None:
                staged_decision = review_root / "decision.json"
                shutil.copy2(decision_path, staged_decision)
                staged_decision.chmod(0o444)
                staged_files.append(
                    {
                        "path": staged_decision.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(staged_decision),
                        "role": "capability_revision_decision",
                    }
                )
            if prior_proposal_path is not None and revision_decision_path is not None:
                staged_prior = review_root / "prior-proposal.json"
                staged_revision_decision = review_root / "revision-decision.json"
                shutil.copy2(prior_proposal_path, staged_prior)
                shutil.copy2(revision_decision_path, staged_revision_decision)
                for staged_path, role in (
                    (staged_prior, "capability_prior_proposal"),
                    (
                        staged_revision_decision,
                        "capability_revision_request_decision",
                    ),
                ):
                    staged_path.chmod(0o444)
                    staged_files.append(
                        {
                            "path": staged_path.relative_to(temporary).as_posix(),
                            "sha256": sha256_file(staged_path),
                            "role": role,
                        }
                    )
                revision_lineage_staged = True
            resolved_candidates = candidate_root.expanduser().resolve()
            for source in sorted(resolved_candidates.rglob("*")):
                if source.is_symlink():
                    raise CapabilityImprovementError(
                        f"candidate symlink is forbidden: {source}"
                    )
                if not source.is_file():
                    continue
                relative = source.relative_to(resolved_candidates)
                destination = review_root / "candidates" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                destination.chmod(0o444)
                staged_files.append(
                    {
                        "path": destination.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(destination),
                        "role": "capability_candidate",
                    }
                )
            review_materials_staged = True
        for item in sorted((inputs / "capability_pack").rglob("*")):
            if item.is_file():
                staged_files.append(
                    {
                        "path": item.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(item),
                        "role": "current_capability_pack",
                    }
                )
        for item in sorted((inputs / "exemplar_memory").rglob("*")):
            if item.is_file():
                staged_files.append(
                    {
                        "path": item.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(item),
                        "role": "current_exemplar_memory",
                    }
                )
        (temporary / "candidates").mkdir()
        (temporary / "outputs").mkdir()
        agent_contract = _stage_agent_contract(
            temporary=temporary,
            inputs=inputs,
            mode=mode,
            review_materials_staged=review_materials_staged,
            revision_lineage_staged=revision_lineage_staged,
            validation_feedback_staged=validation_feedback is not None,
            staged_files=staged_files,
        )
        freeze_tree(inputs)
        payload: dict[str, Any] = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "mode": mode,
            "experiment_digest": experiment_manifest["experiment_digest"],
            "packet_digest": packet["packet_digest"],
            "access_policy_digest": policy["policy_digest"],
            "staged_protocol_ids": list(packet["protocol_ids"]),
            "staged_files": sorted(staged_files, key=lambda item: item["path"]),
            "exemplar_memory": staged_memory_record,
            "review_materials_staged": review_materials_staged,
            "validation_feedback": validation_feedback,
            "agent_contract": agent_contract,
            "host_paths_exposed": False,
            "network_policy": "provider_api_only_no_web",
        }
        manifest = with_digest(payload, "workspace_digest")
        validate_document(
            manifest,
            improvement_schema_root() / "worker_workspace_manifest.schema.json",
            label="capability worker workspace",
        )
        write_json_atomic(temporary / "workspace_manifest.json", manifest, mode=0o444)
        temporary.replace(output_root)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_isolated_worker_workspace(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = load_and_validate(
        root / "workspace_manifest.json",
        schema_filename="worker_workspace_manifest.schema.json",
        digest_field="workspace_digest",
        label="capability worker workspace",
    )
    validate_capability_pack(root / "inputs" / "capability_pack")
    for item in manifest["staged_files"]:
        artifact = root / item["path"]
        if artifact.is_symlink() or not artifact.is_file():
            raise CapabilityImprovementError(
                f"staged workspace artifact is missing: {item['path']}"
            )
        if sha256_file(artifact) != item["sha256"]:
            raise CapabilityImprovementError(
                f"staged workspace artifact changed: {item['path']}"
            )
    memory_record = manifest["exemplar_memory"]
    memory_root = root / "inputs" / "exemplar_memory"
    validated_memory = validate_exemplar_memory(
        memory_root,
        expected_count=memory_record["exemplar_count"],
    )
    expected_memory = _workspace_exemplar_memory_record(
        memory_root,
        source_checkpoint=memory_record["source_checkpoint"],
    )
    if memory_record != expected_memory:
        raise CapabilityImprovementError("workspace exemplar-memory binding is stale")
    if validated_memory["memory_digest"] != memory_record["memory_digest"]:
        raise CapabilityImprovementError("workspace exemplar-memory digest is stale")
    memory_files = {
        item["path"]
        for item in manifest["staged_files"]
        if item["role"] == "current_exemplar_memory"
    }
    actual_memory_files = {
        item.relative_to(root).as_posix()
        for item in memory_root.rglob("*")
        if item.is_file()
    }
    if memory_files != actual_memory_files:
        raise CapabilityImprovementError(
            "workspace exemplar-memory staged-file inventory is not closed"
        )
    if any(
        "identity_map" in item.lower() or "/private/" in f"/{item.lower()}/"
        for item in memory_files
    ):
        raise CapabilityImprovementError(
            "private exemplar identity map entered an agent workspace"
        )
    feedback = manifest["validation_feedback"]
    feedback_records = [
        item
        for item in manifest["staged_files"]
        if item["role"] == "validation_macro_aggregate"
    ]
    if feedback is None:
        if feedback_records:
            raise CapabilityImprovementError(
                "workspace has unrecorded validation feedback"
            )
    else:
        if feedback_records != [
            {
                "path": "inputs/validation_feedback.json",
                "sha256": feedback["projection_sha256"],
                "role": "validation_macro_aggregate",
            }
        ]:
            raise CapabilityImprovementError(
                "workspace validation feedback record is inconsistent"
            )
        from .validation import validate_validation_feedback_projection

        projection = validate_validation_feedback_projection(
            root / "inputs" / "validation_feedback.json",
        )
        if (
            projection["checkpoint_label"] != feedback["checkpoint_label"]
            or projection["aggregate_digest"] != feedback["aggregate_digest"]
        ):
            raise CapabilityImprovementError(
                "workspace validation feedback digest is stale"
            )
    output_schema_path = manifest["agent_contract"]["output_schema_path"]
    if output_schema_path is not None:
        validate_codex_output_schema(
            load_json_object(
                root / output_schema_path,
                label="staged Codex output schema",
            )
        )
    return manifest


def _resolve_parent_exemplar_memory(
    parent_pack_root: Path,
    *,
    experiment_digest: str,
) -> tuple[Path, dict[str, Any], dict[str, str]]:
    """Resolve memory only from a frozen checkpoint or a validated staged copy."""

    unresolved = parent_pack_root.expanduser()
    if unresolved.is_symlink():
        raise CapabilityImprovementError(
            f"parent capability pack may not be a symlink: {unresolved}"
        )
    pack_root = unresolved.resolve()
    validate_capability_pack(pack_root)
    if pack_root.name == "pack" and (pack_root.parent / "checkpoint.json").is_file():
        from .workflow import validate_checkpoint_runtime

        checkpoint_root = pack_root.parent
        checkpoint, runtime, pack = validate_checkpoint_runtime(checkpoint_root)
        if pack_root != (checkpoint_root / "pack").resolve():
            raise CapabilityImprovementError(
                "parent pack is outside its frozen checkpoint"
            )
        if checkpoint["experiment_digest"] != experiment_digest:
            raise CapabilityImprovementError(
                "parent exemplar memory belongs to another experiment"
            )
        memory_root = checkpoint_root / "memory"
        memory = exemplar_memory_record(memory_root)
        if memory != checkpoint["exemplar_memory"] or (
            pack["pack_digest"] != checkpoint["pack_digest"]
        ):
            raise CapabilityImprovementError(
                "parent checkpoint pack and exemplar memory are not coherently bound"
            )
        source_checkpoint = {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "checkpoint_sha256": sha256_file(checkpoint_root / "checkpoint.json"),
            "runtime_digest": runtime["runtime_digest"],
            "runtime_sha256": sha256_file(checkpoint_root / "runtime.json"),
        }
        return memory_root, memory, source_checkpoint

    if pack_root.name == "capability_pack":
        workspace_root = pack_root.parent.parent
        workspace_manifest_path = workspace_root / "workspace_manifest.json"
        if workspace_manifest_path.is_file():
            source_manifest = validate_isolated_worker_workspace(workspace_root)
            if source_manifest["experiment_digest"] != experiment_digest:
                raise CapabilityImprovementError(
                    "staged parent exemplar memory belongs to another experiment"
                )
            memory_root = pack_root.parent / "exemplar_memory"
            memory = exemplar_memory_record(memory_root)
            recorded = source_manifest["exemplar_memory"]
            expected = _workspace_exemplar_memory_record(
                memory_root,
                source_checkpoint=recorded["source_checkpoint"],
            )
            if recorded != expected:
                raise CapabilityImprovementError(
                    "staged parent exemplar-memory binding is stale"
                )
            return memory_root, memory, dict(recorded["source_checkpoint"])

    raise CapabilityImprovementError(
        "isolated workers require a parent pack from a validated frozen "
        "checkpoint (or a validated staged derivative) with sibling exemplar memory"
    )


def _workspace_exemplar_memory_record(
    memory_root: Path,
    *,
    source_checkpoint: Mapping[str, str],
) -> dict[str, Any]:
    record = exemplar_memory_record(memory_root)
    return {
        **record,
        "path": "inputs/exemplar_memory",
        "manifest_path": "inputs/exemplar_memory/manifest.json",
        "catalog_path": "inputs/exemplar_memory/catalog.json",
        "source_checkpoint": dict(source_checkpoint),
        "private_identity_map_staged": False,
    }


def compile_codex_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Project a canonical draft schema into Codex Structured Outputs' subset.

    Canonical draft validation remains unchanged and is applied after generation.
    This projection exists only to constrain the untrusted model response.  In
    particular, OpenAI accepts ``anyOf`` but not ``oneOf`` and requires every
    object property to be listed in ``required``.  Optional draft fields must
    therefore already permit ``null`` before they can be made required here.
    """

    projected = _project_structured_output_node(copy.deepcopy(dict(schema)))
    projected.pop("$schema", None)
    projected.pop("$id", None)
    validate_codex_output_schema(projected)
    return projected


def validate_codex_output_schema(schema: Mapping[str, Any]) -> None:
    """Reject a staged schema that Codex Structured Outputs cannot accept."""

    if schema.get("type") != "object" or "anyOf" in schema:
        raise CapabilityImprovementError(
            "Codex output schema root must be an object without anyOf"
        )
    _validate_codex_output_schema_node(dict(schema), path=("<root>",))


def _validate_codex_output_schema_node(value: Any, *, path: tuple[str, ...]) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_codex_output_schema_node(item, path=(*path, str(index)))
        return
    if not isinstance(value, dict):
        return

    forbidden = sorted(
        (_UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS | {"oneOf", "uniqueItems"})
        & value.keys()
    )
    location = "/".join(path)
    if forbidden:
        raise CapabilityImprovementError(
            f"Codex output schema at {location} uses unsupported keywords: "
            + ", ".join(forbidden)
        )
    if "const" in value:
        expected_type = _json_schema_type(value["const"])
        actual_type = value.get("type")
        if actual_type != expected_type and not (
            isinstance(actual_type, list) and expected_type in actual_type
        ):
            raise CapabilityImprovementError(
                f"Codex output schema const at {location} requires type={expected_type}"
            )
    pattern = value.get("pattern")
    if isinstance(pattern, str) and any(
        token in pattern for token in _REGEX_LOOKAROUND_TOKENS
    ):
        raise CapabilityImprovementError(
            f"Codex output schema regex at {location} uses unsupported lookaround"
        )

    properties = value.get("properties")
    if isinstance(properties, dict):
        if value.get("additionalProperties") is not False:
            raise CapabilityImprovementError(
                f"Codex output-schema object at {location} requires "
                "additionalProperties=false"
            )
        required = value.get("required")
        if (
            not isinstance(required, list)
            or len(required) != len(properties)
            or set(required) != set(properties)
        ):
            raise CapabilityImprovementError(
                f"Codex output-schema object at {location} must require every property"
            )
        missing_type = [
            name
            for name, property_schema in properties.items()
            if not isinstance(property_schema, dict)
            or not ({"type", "$ref", "anyOf"} & property_schema.keys())
        ]
        if missing_type:
            raise CapabilityImprovementError(
                f"Codex output-schema properties at {location} require type, "
                "$ref, or anyOf: " + ", ".join(missing_type)
            )

    for key, item in value.items():
        _validate_codex_output_schema_node(item, path=(*path, key))


def _project_structured_output_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_project_structured_output_node(item) for item in value]
    if not isinstance(value, dict):
        return value

    unsupported = sorted(_UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS & value.keys())
    if unsupported:
        raise CapabilityImprovementError(
            "Codex output schema uses unsupported keywords: " + ", ".join(unsupported)
        )

    projected = {
        key: _project_structured_output_node(item)
        for key, item in value.items()
        if key != "uniqueItems"
    }
    pattern = projected.get("pattern")
    if isinstance(pattern, str) and any(
        token in pattern for token in _REGEX_LOOKAROUND_TOKENS
    ):
        # Structured Outputs rejects lookaround. The unchanged canonical draft
        # schema still enforces the complete path constraint after generation.
        projected.pop("pattern")
    if "oneOf" in projected:
        if "anyOf" in projected:
            raise CapabilityImprovementError(
                "Codex output schema cannot combine oneOf and anyOf in one node"
            )
        projected["anyOf"] = projected.pop("oneOf")
    if "const" in projected and "type" not in projected:
        projected["type"] = _json_schema_type(projected["const"])

    properties = projected.get("properties")
    if isinstance(properties, dict):
        if projected.get("additionalProperties") is not False:
            raise CapabilityImprovementError(
                "Codex output-schema objects require additionalProperties=false"
            )
        required = projected.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise CapabilityImprovementError(
                "Codex output-schema required must be a string array"
            )
        missing = [name for name in properties if name not in required]
        non_nullable = [
            name for name in missing if not _schema_node_allows_null(properties[name])
        ]
        if non_nullable:
            raise CapabilityImprovementError(
                "optional Codex output-schema fields must permit null: "
                + ", ".join(non_nullable)
            )
        missing_type = [
            name
            for name, property_schema in properties.items()
            if not isinstance(property_schema, dict)
            or not ({"type", "$ref", "anyOf"} & property_schema.keys())
        ]
        if missing_type:
            raise CapabilityImprovementError(
                "Codex output-schema properties require type, $ref, or anyOf: "
                + ", ".join(missing_type)
            )
        projected["required"] = list(properties)
    return projected


def _json_schema_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise CapabilityImprovementError(
        f"cannot infer JSON Schema type for const value: {type(value).__name__}"
    )


def _schema_node_allows_null(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    node_type = value.get("type")
    if node_type == "null" or (isinstance(node_type, list) and "null" in node_type):
        return True
    for keyword in ("anyOf", "oneOf"):
        alternatives = value.get(keyword)
        if isinstance(alternatives, list) and any(
            _schema_node_allows_null(item) for item in alternatives
        ):
            return True
    return False


def _stage_agent_contract(
    *,
    temporary: Path,
    inputs: Path,
    mode: str,
    review_materials_staged: bool,
    revision_lineage_staged: bool,
    validation_feedback_staged: bool,
    staged_files: list[dict[str, str]],
) -> dict[str, str | None]:
    if (
        mode
        in {
            "independent_critic",
            "revision_worker",
            "human_review_console",
        }
        and not review_materials_staged
    ):
        raise CapabilityImprovementError(
            f"{mode} requires staged proposal and candidate files"
        )
    if mode == "human_review_console":
        return {
            "prompt_path": None,
            "output_schema_path": None,
            "draft_output_path": None,
            "event_log_path": None,
        }
    if mode in {"improvement_worker", "revision_worker"}:
        prompt_text = (
            proposal_instruction()
            + """

Workspace contract:
- current pack: inputs/capability_pack/
- cumulative prediction-shaped exemplar memory: inputs/exemplar_memory/
- query memory only through runtime/tools/query_exemplars.py, using features
  supported by the current packet sources and work record; use the exact donor
  limit declared by the staged parent checkpoint runtime and record usage under
  outputs/exemplar_usage.json
- never read exemplar_memory/catalog.json or exemplar_memory/exemplars/**
  directly; those are query-tool implementation data, not agent context
- exemplar memory is representational guidance, never evidence for a sequence,
  operation, state, modification, or branch in the current target
- evidence packet: inputs/packet_view.json
- evidence files: inputs/evidence/
- write changed full files only under candidates/
- final structured draft: outputs/proposal_draft.json
"""
        )
        if mode == "improvement_worker" and validation_feedback_staged:
            prompt_text += """
- sanitized validation feedback: inputs/validation_feedback.json
- use only its five-protocol macro means and counts to prioritize general
  controls; it contains no protocol examples and cannot support an evidence_ref
- never infer validation-specific answers, copy scores into candidate files, or
  place validation material in cumulative memory or synthetic fixtures
"""
        if mode == "revision_worker":
            prompt_text += """
- prior proposal, candidates, and recorded decision: inputs/review/
- implement only the bounded revision instructions recorded in decision.json
- this is the single permitted revision round; do not introduce unrelated changes
- emit one complete revised proposal: carry accepted change units and candidate
  bytes forward unchanged, revise every modify unit, and omit rejected or
  unresolved units
- candidates/ is deterministically seeded with accepted and modify files; keep
  accepted files byte-identical, edit only modify files, and add only files
  required by the bounded revision instructions
"""
        schema_name = "capability_proposal_draft.schema.json"
        draft_path = "outputs/proposal_draft.json"
        event_path = (
            "outputs/revision.events.jsonl"
            if mode == "revision_worker"
            else "outputs/proposal.events.jsonl"
        )
    else:
        prompt_text = (
            independent_review_instruction()
            + """

Workspace contract:
- current pack: inputs/capability_pack/
- cumulative prediction-shaped exemplar memory: inputs/exemplar_memory/
- inspect donor content only through its deterministic query interface; exemplar
  memory is representational guidance and is not target evidence
- never read exemplar_memory/catalog.json or exemplar_memory/exemplars/** directly
- evidence packet and files: inputs/packet_view.json and inputs/evidence/
- proposal and candidate files: inputs/review/
- final structured draft: outputs/decision_draft.json
"""
        )
        if revision_lineage_staged:
            prompt_text += """
- this is the fresh review of the one permitted revised proposal
- prior proposal and revision request: inputs/review/prior-proposal.json and
  inputs/review/revision-decision.json
- verify that the bounded revision instructions were satisfied and unrelated
  accepted candidate bytes were preserved
- no second modify decision is permitted; use accept, reject, or unresolved
"""
        schema_name = "capability_decision_draft.schema.json"
        draft_path = "outputs/decision_draft.json"
        event_path = "outputs/critic.events.jsonl"
    prompt_path = inputs / "worker_prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    prompt_path.chmod(0o444)
    schema_source = improvement_schema_root() / schema_name
    schema_path = inputs / schema_name
    output_schema = compile_codex_output_schema(
        load_json_object(schema_source, label="canonical capability draft schema")
    )
    write_json_atomic(schema_path, output_schema, mode=0o444)
    for path, role in (
        (prompt_path, "agent_prompt"),
        (schema_path, "agent_output_schema"),
    ):
        staged_files.append(
            {
                "path": path.relative_to(temporary).as_posix(),
                "sha256": sha256_file(path),
                "role": role,
            }
        )
    return {
        "prompt_path": prompt_path.relative_to(temporary).as_posix(),
        "output_schema_path": schema_path.relative_to(temporary).as_posix(),
        "draft_output_path": draft_path,
        "event_log_path": event_path,
    }
