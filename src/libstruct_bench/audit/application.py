from __future__ import annotations

import copy
import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .review import (
    ReviewError,
    all_review_decision_items,
    compiled_root_operation_ids,
    is_compiled_review_decision,
    proposal_compiled_root_issue_ids,
    validate_review_decision,
)
from .groundtruth import (
    GroundtruthValidationError,
    TASK_ARTIFACTS,
    documents_by_task,
    validate_cross_task_links,
    validate_task_document,
)


class ApplicationError(ValueError):
    """Raised when an approved correction cannot be applied deterministically."""


@dataclass(frozen=True)
class ApplicationResult:
    output_dir: Path
    log_path: Path
    candidate_paths: dict[str, Path]
    applied_issue_ids: tuple[str, ...]


def apply_review_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    baseline_paths: Mapping[str, Path],
    output_dir: Path,
    proposal_schema_path: Path,
    decision_schema_path: Path,
    application_schema_path: Path,
    regression_schema_path: Path | None = None,
    artifact_schema_paths: Mapping[str, Path] | None = None,
    compiled_candidate_paths: Mapping[str, Path] | None = None,
    allow_in_progress: bool = False,
) -> ApplicationResult:
    """Apply only human-approved ground-truth patches to immutable baselines."""

    try:
        proposal, decision = validate_review_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            proposal_schema_path=proposal_schema_path,
            decision_schema_path=decision_schema_path,
            require_final=not allow_in_progress,
        )
    except ReviewError as error:
        raise ApplicationError(str(error)) from error
    application_schema_path = _file(application_schema_path, "application log schema")
    if regression_schema_path is None:
        regression_schema_path = (
            Path(__file__).resolve().parents[3]
            / "schemas"
            / "audit"
            / "accepted_correction_regression.schema.json"
        )
    regression_schema_path = _file(
        regression_schema_path, "regression fixture schema"
    )
    proposal_path = _file(proposal_path, "audit proposal")
    decision_path = _file(decision_path, "review decision")
    expected_hashes = {
        item["source_id"]: item["sha256"]
        for item in proposal["baseline_artifacts"]
    }
    if set(baseline_paths) != set(expected_hashes):
        raise ApplicationError(
            "baseline path map must exactly match proposal baselines; "
            f"missing={sorted(set(expected_hashes) - set(baseline_paths))}, "
            f"extra={sorted(set(baseline_paths) - set(expected_hashes))}"
        )
    baselines: dict[str, dict[str, Any]] = {}
    resolved_paths: dict[str, Path] = {}
    for source_id, path in baseline_paths.items():
        resolved = _file(path, f"baseline {source_id}")
        actual = sha256_file(resolved)
        if actual != expected_hashes[source_id]:
            raise ApplicationError(
                f"stale baseline {source_id}: expected {expected_hashes[source_id]}, got {actual}"
            )
        baselines[source_id] = load_json_object(resolved, label=f"baseline {source_id}")
        resolved_paths[source_id] = resolved

    if is_compiled_review_decision(decision):
        if allow_in_progress:
            raise ApplicationError(
                "compiled-root application requires a final scientific approval"
            )
        return _apply_compiled_review_decision(
            proposal=proposal,
            decision=decision,
            proposal_path=proposal_path,
            decision_path=decision_path,
            baselines=baselines,
            resolved_paths=resolved_paths,
            expected_hashes=expected_hashes,
            compiled_candidate_paths=compiled_candidate_paths or {},
            output_dir=output_dir,
            application_schema_path=application_schema_path,
            regression_schema_path=regression_schema_path,
            artifact_schema_paths=artifact_schema_paths or {},
        )
    if compiled_candidate_paths:
        raise ApplicationError(
            "--compiled-candidate is only valid for a compiled-root review"
        )

    decisions = {item["issue_id"]: item for item in decision["issue_decisions"]}
    selected: list[
        tuple[str, str, list[dict[str, Any]], str, str | None]
    ] = []
    skipped: list[str] = []
    new_artifact_ids: set[str] = set()
    new_artifact_names: set[str] = set()
    selected_artifact_tasks: dict[str, str] = {}
    for issue in proposal["issues"]:
        issue_id = issue["issue_id"]
        review = decisions.get(issue_id)
        if review is None:
            skipped.append(issue_id)
            continue
        disposition = review["disposition"]
        if disposition not in {"accept", "modify"}:
            skipped.append(issue_id)
            continue
        target_kind = issue["target"]["kind"]
        if target_kind not in {"groundtruth_record", "new_groundtruth_record"}:
            skipped.append(issue_id)
            continue
        if disposition == "accept":
            if issue["recommendation"] != "propose_change":
                skipped.append(issue_id)
                continue
            patch = issue["proposed_patch"]
        else:
            patch = review["replacement_patch"]
        source_id = issue["target"].get("artifact_source_id")
        filename: str | None = None
        if target_kind == "groundtruth_record" and source_id not in baselines:
            raise ApplicationError(
                f"issue {issue_id} targets unknown baseline {source_id!r}"
            )
        if target_kind == "new_groundtruth_record":
            if source_id in baselines or source_id in new_artifact_ids:
                raise ApplicationError(
                    f"issue {issue_id} duplicates ground-truth artifact {source_id!r}"
                )
            filename = issue["target"]["artifact_filename"]
            if filename in new_artifact_names:
                raise ApplicationError(
                    f"issue {issue_id} duplicates new artifact filename {filename!r}"
                )
            _validate_new_artifact_patch(issue_id, patch)
            new_artifact_ids.add(source_id)
            new_artifact_names.add(filename)
            baselines[source_id] = {}
        if issue["task"] in TASK_ARTIFACTS:
            previous_task = selected_artifact_tasks.setdefault(source_id, issue["task"])
            if previous_task != issue["task"]:
                raise ApplicationError(
                    f"artifact {source_id!r} is targeted as both "
                    f"{previous_task} and {issue['task']}"
                )
        selected.append((issue_id, source_id, patch, target_kind, filename))
    _reject_overlapping_patches(
        [(issue_id, source_id, patch) for issue_id, source_id, patch, _, _ in selected]
    )

    candidates = copy.deepcopy(baselines)
    baseline_states = {
        source_id: ("absent" if source_id in new_artifact_ids else "present")
        for source_id in candidates
    }
    new_filenames = {
        source_id: filename
        for _, source_id, _, _, filename in selected
        if filename is not None
    }
    applied: list[str] = []
    regression_records: list[dict[str, Any]] = []
    for issue_id, source_id, patch, _, _ in selected:
        before = copy.deepcopy(candidates[source_id])
        after = apply_json_patch(before, patch)
        candidates[source_id] = after
        applied.append(issue_id)
        regression_candidate = apply_json_patch(baselines[source_id], patch)
        regression_record = {
            "protocol_id": proposal["protocol_id"],
            "audit_id": proposal["audit_id"],
            "decision_id": decision["decision_id"],
            "issue_id": issue_id,
            "artifact_source_id": source_id,
            "baseline_state": baseline_states[source_id],
            "patch": patch,
            "candidate_document_sha256": hashlib.sha256(
                canonical_json_bytes(regression_candidate)
            ).hexdigest(),
        }
        if baseline_states[source_id] == "present":
            regression_record["baseline_sha256"] = expected_hashes[source_id]
        regression_records.append(regression_record)
        try:
            validate_document(
                regression_records[-1],
                regression_schema_path,
                label=f"regression fixture {issue_id}",
            )
        except AuditArtifactError as error:
            raise ApplicationError(str(error)) from error

    schema_map = dict(artifact_schema_paths or {})
    unknown_schema_ids = sorted(set(schema_map) - set(candidates))
    if unknown_schema_ids:
        raise ApplicationError(
            "artifact schema supplied for unknown source: "
            + ", ".join(unknown_schema_ids)
        )
    canonical_schema_dir = (
        Path(__file__).resolve().parents[3] / "schemas" / "groundtruth"
    )
    for source_id, candidate in candidates.items():
        task = selected_artifact_tasks.get(source_id)
        if task is None:
            matching_tasks = [
                candidate_task
                for candidate_task, details in TASK_ARTIFACTS.items()
                if details["root_key"] in candidate
            ]
            if len(matching_tasks) != 1:
                continue
            task = matching_tasks[0]
        schema_map.setdefault(
            source_id, canonical_schema_dir / TASK_ARTIFACTS[task]["schema"]
        )
    for source_id, schema_path in schema_map.items():
        try:
            validate_document(
                candidates[source_id],
                _file(schema_path, f"artifact schema for {source_id}"),
                label=f"candidate {source_id}",
            )
        except AuditArtifactError as error:
            raise ApplicationError(str(error)) from error
    if decision["review_state"] == "final":
        try:
            validate_cross_task_links(documents_by_task(candidates))
        except GroundtruthValidationError as error:
            raise ApplicationError(str(error)) from error

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir)
    if output_dir.exists():
        raise ApplicationError(f"application output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        candidate_dir = temporary_dir / "candidates"
        regression_dir = temporary_dir / "regressions"
        candidate_dir.mkdir()
        regression_dir.mkdir()
        candidate_relative: dict[str, str] = {}
        candidate_hashes: dict[str, str] = {}
        used_names: set[str] = set()
        for source_id in sorted(candidates):
            basename = (
                new_filenames[source_id]
                if baseline_states[source_id] == "absent"
                else resolved_paths[source_id].name
            )
            if basename in used_names:
                basename = f"{_slug(source_id)}-{basename}"
            used_names.add(basename)
            path = candidate_dir / basename
            write_json_atomic(path, candidates[source_id])
            candidate_relative[source_id] = f"candidates/{basename}"
            candidate_hashes[source_id] = sha256_file(path)
        regression_paths: list[str] = []
        for record in regression_records:
            relative = f"regressions/{_slug(record['issue_id'])}.json"
            write_json_atomic(temporary_dir / relative, record)
            regression_paths.append(relative)

        proposal_sha = sha256_file(proposal_path)
        decision_sha = sha256_file(decision_path)
        identity = hashlib.sha256(f"{proposal_sha}:{decision_sha}".encode()).hexdigest()
        log = {
            "application_id": f"{proposal['protocol_id']}:application:{identity[:16]}",
            "protocol_id": proposal["protocol_id"],
            "audit_id": proposal["audit_id"],
            "decision_id": decision["decision_id"],
            "review_state": decision["review_state"],
            "proposal_sha256": proposal_sha,
            "decision_sha256": decision_sha,
            "created_at": decision["review_completed_at"],
            "artifacts": [
                _application_artifact(
                    source_id=source_id,
                    baseline_state=baseline_states[source_id],
                    resolved_paths=resolved_paths,
                    expected_hashes=expected_hashes,
                    candidate_path=candidate_relative[source_id],
                    candidate_sha256=candidate_hashes[source_id],
                )
                for source_id in sorted(candidates)
            ],
            "applied_issue_ids": applied,
            "skipped_issue_ids": skipped,
            "regression_fixtures": regression_paths,
        }
        try:
            validate_document(log, application_schema_path, label="application log")
        except AuditArtifactError as error:
            raise ApplicationError(str(error)) from error
        write_json_atomic(temporary_dir / "application-log.json", log)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return ApplicationResult(
        output_dir=output_dir,
        log_path=output_dir / "application-log.json",
        candidate_paths={
            source_id: output_dir / candidate_relative[source_id]
            for source_id in candidate_relative
        },
        applied_issue_ids=tuple(applied),
    )


def _compiled_baseline_sources(
    baselines: Mapping[str, dict[str, Any]],
    resolved_paths: Mapping[str, Path],
) -> dict[str, str]:
    """Identify each compiled baseline by its canonical task root."""

    result: dict[str, str] = {}
    task_by_filename = {
        details["filename"]: task for task, details in TASK_ARTIFACTS.items()
    }
    for source_id, document in baselines.items():
        matches = [
            task
            for task, details in TASK_ARTIFACTS.items()
            if details["root_key"] in document
        ]
        if len(matches) != 1:
            raise ApplicationError(
                f"compiled baseline {source_id!r} does not identify exactly one task"
            )
        task = matches[0]
        filename_task = task_by_filename.get(resolved_paths[source_id].name)
        if filename_task is not None and filename_task != task:
            raise ApplicationError(
                f"compiled baseline {source_id!r} filename disagrees with its task root"
            )
        if task in result:
            raise ApplicationError(f"compiled review has multiple {task} baselines")
        result[task] = source_id
    return result


def _apply_compiled_review_decision(
    *,
    proposal: dict[str, Any],
    decision: dict[str, Any],
    proposal_path: Path,
    decision_path: Path,
    baselines: Mapping[str, dict[str, Any]],
    resolved_paths: Mapping[str, Path],
    expected_hashes: Mapping[str, str],
    compiled_candidate_paths: Mapping[str, Path],
    output_dir: Path,
    application_schema_path: Path,
    regression_schema_path: Path,
    artifact_schema_paths: Mapping[str, Path],
) -> ApplicationResult:
    """Apply hash-approved complete task documents as deterministic root patches."""

    approved_hashes = decision["candidate_sha256"]
    if set(compiled_candidate_paths) != set(approved_hashes):
        raise ApplicationError(
            "compiled candidate map must exactly match approved task hashes; "
            f"missing={sorted(set(approved_hashes) - set(compiled_candidate_paths))}, "
            f"extra={sorted(set(compiled_candidate_paths) - set(approved_hashes))}"
        )

    issues = {item["issue_id"]: item for item in proposal["issues"]}
    try:
        root_operations = compiled_root_operation_ids(proposal, decision)
    except ReviewError as error:
        raise ApplicationError(str(error)) from error
    root_ids = tuple(root_operations[task] for task in sorted(root_operations))
    baseline_source_by_task = _compiled_baseline_sources(
        baselines, resolved_paths
    )
    source_by_task: dict[str, str] = {}
    baseline_states: dict[str, str] = {}
    candidate_documents: dict[str, dict[str, Any]] = {}
    candidate_sources: dict[str, Path] = {}
    filenames: dict[str, str] = {}
    regression_records: list[dict[str, Any]] = []

    for task in sorted(root_operations):
        issue_id = root_operations[task]
        issue = issues.get(issue_id)
        if issue is not None:
            target = issue["target"]
            source_id = target["artifact_source_id"]
            state = (
                "present" if target["kind"] == "groundtruth_record" else "absent"
            )
            operation = [
                item for item in issue["proposed_patch"] if item["op"] != "test"
            ]
            expected_op = "replace" if state == "present" else "add"
            if (
                len(operation) != 1
                or operation[0]["op"] != expected_op
                or operation[0]["path"] != ""
            ):
                raise ApplicationError(
                    f"compiled root {issue_id} must be one root {expected_op} patch"
                )
            filename = target.get(
                "artifact_filename", TASK_ARTIFACTS[task]["filename"]
            )
        else:
            source_id = baseline_source_by_task.get(
                task, f"compiled:{task.lower()}:new"
            )
            state = "present" if task in baseline_source_by_task else "absent"
            filename = TASK_ARTIFACTS[task]["filename"]
        if source_id in source_by_task.values():
            raise ApplicationError(
                f"compiled roots reuse artifact source {source_id!r}"
            )
        if state == "present" and source_id not in baselines:
            raise ApplicationError(
                f"compiled root {issue_id} targets unknown baseline {source_id!r}"
            )
        if state == "absent" and source_id in baselines:
            raise ApplicationError(
                f"compiled root {issue_id} recreates baseline {source_id!r}"
            )
        if state == "present" and baseline_source_by_task.get(task) != source_id:
            raise ApplicationError(
                f"compiled {task} root targets the wrong baseline {source_id!r}"
            )
        source_by_task[task] = source_id
        baseline_states[source_id] = state
        filenames[task] = filename
        if filenames[task] != TASK_ARTIFACTS[task]["filename"]:
            raise ApplicationError(
                f"compiled {task} root uses the wrong artifact filename"
            )

    present_sources = {
        source_by_task[task]
        for task in root_operations
        if baseline_states[source_by_task[task]] == "present"
    }
    if present_sources != set(baselines):
        raise ApplicationError(
            "compiled roots must exactly cover proposal baselines; "
            f"missing={sorted(set(baselines) - present_sources)}, "
            f"extra={sorted(present_sources - set(baselines))}"
        )
    if set(root_operations) != set(approved_hashes):
        raise ApplicationError(
            "compiled roots do not match approved candidate tasks"
        )

    canonical_schema_dir = (
        Path(__file__).resolve().parents[3] / "schemas" / "groundtruth"
    )
    unknown_schema_ids = sorted(
        set(artifact_schema_paths) - set(source_by_task.values())
    )
    if unknown_schema_ids:
        raise ApplicationError(
            "artifact schema supplied for unknown compiled source: "
            + ", ".join(unknown_schema_ids)
        )
    for task in sorted(root_operations):
        source_path = _file(
            compiled_candidate_paths[task], f"approved compiled {task} candidate"
        )
        actual_hash = sha256_file(source_path)
        if actual_hash != approved_hashes[task]:
            raise ApplicationError(
                f"stale compiled {task} candidate: expected "
                f"{approved_hashes[task]}, got {actual_hash}"
            )
        document = load_json_object(source_path, label=f"compiled {task} candidate")
        try:
            validate_task_document(
                task,
                document,
                protocol_id=proposal["protocol_id"],
                schema_dir=canonical_schema_dir,
            )
            source_id = source_by_task[task]
            if source_id in artifact_schema_paths:
                validate_document(
                    document,
                    _file(
                        artifact_schema_paths[source_id],
                        f"artifact schema for {source_id}",
                    ),
                    label=f"compiled candidate {source_id}",
                )
        except (AuditArtifactError, GroundtruthValidationError) as error:
            raise ApplicationError(str(error)) from error
        candidate_documents[task] = document
        candidate_sources[task] = source_path

    try:
        validate_cross_task_links(candidate_documents)
    except GroundtruthValidationError as error:
        raise ApplicationError(str(error)) from error

    for task in sorted(root_operations):
        issue_id = root_operations[task]
        source_id = source_by_task[task]
        patch = [
            {
                "op": "replace" if baseline_states[source_id] == "present" else "add",
                "path": "",
                "value": candidate_documents[task],
            }
        ]
        regression_record = {
            "protocol_id": proposal["protocol_id"],
            "audit_id": proposal["audit_id"],
            "decision_id": decision["decision_id"],
            "issue_id": issue_id,
            "artifact_source_id": source_id,
            "baseline_state": baseline_states[source_id],
            "patch": patch,
            "candidate_document_sha256": hashlib.sha256(
                canonical_json_bytes(candidate_documents[task])
            ).hexdigest(),
        }
        if baseline_states[source_id] == "present":
            regression_record["baseline_sha256"] = expected_hashes[source_id]
        try:
            validate_document(
                regression_record,
                regression_schema_path,
                label=f"compiled-root regression fixture {issue_id}",
            )
        except AuditArtifactError as error:
            raise ApplicationError(str(error)) from error
        regression_records.append(regression_record)

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir)
    if output_dir.exists():
        raise ApplicationError(f"application output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    candidate_relative: dict[str, str] = {}
    try:
        candidate_dir = temporary_dir / "candidates"
        regression_dir = temporary_dir / "regressions"
        candidate_dir.mkdir()
        regression_dir.mkdir()
        artifacts: list[dict[str, Any]] = []
        for task in sorted(root_operations):
            source_id = source_by_task[task]
            relative = f"candidates/{filenames[task]}"
            destination = temporary_dir / relative
            shutil.copyfile(candidate_sources[task], destination)
            copied_hash = sha256_file(destination)
            if copied_hash != approved_hashes[task]:
                raise ApplicationError(
                    f"compiled {task} candidate changed while being copied"
                )
            candidate_relative[source_id] = relative
            artifacts.append(
                _application_artifact(
                    source_id=source_id,
                    baseline_state=baseline_states[source_id],
                    resolved_paths=resolved_paths,
                    expected_hashes=expected_hashes,
                    candidate_path=relative,
                    candidate_sha256=copied_hash,
                )
            )
        regression_paths: list[str] = []
        for record in regression_records:
            relative = f"regressions/{_slug(record['issue_id'])}.json"
            write_json_atomic(temporary_dir / relative, record)
            regression_paths.append(relative)

        proposal_sha = sha256_file(proposal_path)
        decision_sha = sha256_file(decision_path)
        identity = hashlib.sha256(f"{proposal_sha}:{decision_sha}".encode()).hexdigest()
        incorporated = [
            item["issue_id"]
            for item in all_review_decision_items(proposal, decision)
        ]
        skipped = [
            item["issue_id"]
            for item in proposal["issues"]
            if item["issue_id"] not in proposal_compiled_root_issue_ids(proposal)
        ]
        log = {
            "application_id": f"{proposal['protocol_id']}:application:{identity[:16]}",
            "application_mode": "compiled_roots",
            "protocol_id": proposal["protocol_id"],
            "audit_id": proposal["audit_id"],
            "decision_id": decision["decision_id"],
            "review_state": decision["review_state"],
            "proposal_sha256": proposal_sha,
            "decision_sha256": decision_sha,
            "created_at": decision["review_completed_at"],
            "artifacts": artifacts,
            "applied_issue_ids": list(root_ids),
            "incorporated_decision_ids": incorporated,
            "skipped_issue_ids": skipped,
            "regression_fixtures": regression_paths,
        }
        try:
            validate_document(log, application_schema_path, label="application log")
        except AuditArtifactError as error:
            raise ApplicationError(str(error)) from error
        write_json_atomic(temporary_dir / "application-log.json", log)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

    return ApplicationResult(
        output_dir=output_dir,
        log_path=output_dir / "application-log.json",
        candidate_paths={
            source_id: output_dir / relative
            for source_id, relative in candidate_relative.items()
        },
        applied_issue_ids=tuple(root_ids),
    )


def apply_json_patch(document: Any, operations: list[dict[str, Any]]) -> Any:
    """Apply the supported deterministic RFC 6902 subset to a deep copy."""

    value = copy.deepcopy(document)
    for operation in operations:
        op = operation["op"]
        path = operation["path"]
        if op == "test":
            actual = _pointer_get(value, path)
            if actual != operation["value"]:
                raise ApplicationError(
                    f"JSON Patch test failed at {path}: expected {operation['value']!r}, got {actual!r}"
                )
            continue
        if path == "":
            if op in {"add", "replace"}:
                value = copy.deepcopy(operation["value"])
            elif op == "remove":
                raise ApplicationError("removing the document root is not supported")
            continue
        parent, token = _pointer_parent(value, path)
        if isinstance(parent, dict):
            if op == "add":
                parent[token] = copy.deepcopy(operation["value"])
            elif op == "replace":
                if token not in parent:
                    raise ApplicationError(f"replace path does not exist: {path}")
                parent[token] = copy.deepcopy(operation["value"])
            elif op == "remove":
                if token not in parent:
                    raise ApplicationError(f"remove path does not exist: {path}")
                del parent[token]
        elif isinstance(parent, list):
            if op == "add" and token == "-":
                parent.append(copy.deepcopy(operation["value"]))
                continue
            index = _array_index(token, path, allow_end=op == "add", length=len(parent))
            if op == "add":
                parent.insert(index, copy.deepcopy(operation["value"]))
            elif op == "replace":
                parent[index] = copy.deepcopy(operation["value"])
            elif op == "remove":
                del parent[index]
        else:
            raise ApplicationError(f"JSON Pointer parent is not a container: {path}")
    return value


def _pointer_get(document: Any, pointer: str) -> Any:
    value = document
    for token in _pointer_tokens(pointer):
        if isinstance(value, dict):
            if token not in value:
                raise ApplicationError(f"JSON Pointer does not exist: {pointer}")
            value = value[token]
        elif isinstance(value, list):
            value = value[_array_index(token, pointer, allow_end=False, length=len(value))]
        else:
            raise ApplicationError(f"JSON Pointer traverses a scalar: {pointer}")
    return value


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        raise ApplicationError("root pointer has no parent")
    value = document
    for token in tokens[:-1]:
        if isinstance(value, dict):
            if token not in value:
                raise ApplicationError(f"JSON Pointer does not exist: {pointer}")
            value = value[token]
        elif isinstance(value, list):
            value = value[_array_index(token, pointer, allow_end=False, length=len(value))]
        else:
            raise ApplicationError(f"JSON Pointer traverses a scalar: {pointer}")
    return value, tokens[-1]


def _pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ApplicationError(f"invalid JSON Pointer: {pointer}")
    tokens = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ApplicationError(f"invalid JSON Pointer escape: {pointer}")
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def _array_index(token: str, path: str, *, allow_end: bool, length: int) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]*", token):
        raise ApplicationError(f"invalid array index at {path}")
    index = int(token)
    upper = length if allow_end else length - 1
    if index < 0 or index > upper:
        raise ApplicationError(f"array index out of range at {path}")
    return index


def _reject_overlapping_patches(
    selected: list[tuple[str, str, list[dict[str, Any]]]]
) -> None:
    mutations: list[tuple[str, tuple[str, ...], str]] = []
    for issue_id, source_id, operations in selected:
        for operation in operations:
            if operation["op"] == "test":
                continue
            tokens = tuple(_pointer_tokens(operation["path"]))
            for other_source, other_tokens, other_issue in mutations:
                if source_id != other_source or issue_id == other_issue:
                    continue
                if _is_prefix(tokens, other_tokens) or _is_prefix(other_tokens, tokens):
                    raise ApplicationError(
                        f"overlapping patches from {other_issue} and {issue_id}"
                    )
            mutations.append((source_id, tokens, issue_id))


def _validate_new_artifact_patch(
    issue_id: str, operations: list[dict[str, Any]]
) -> None:
    mutations = [item for item in operations if item["op"] != "test"]
    if (
        len(mutations) != 1
        or mutations[0]["op"] not in {"add", "replace"}
        or mutations[0]["path"] != ""
    ):
        raise ApplicationError(
            f"new artifact issue {issue_id} must contain one root add/replace patch"
        )


def _application_artifact(
    *,
    source_id: str,
    baseline_state: str,
    resolved_paths: Mapping[str, Path],
    expected_hashes: Mapping[str, str],
    candidate_path: str,
    candidate_sha256: str,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "source_id": source_id,
        "baseline_state": baseline_state,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_sha256,
    }
    if baseline_state == "present":
        artifact["baseline_path"] = resolved_paths[source_id].as_posix()
        artifact["baseline_sha256"] = expected_hashes[source_id]
    return artifact


def _is_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and right[: len(left)] == left


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "artifact"


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ApplicationError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise ApplicationError("private application output must not be written inside libstruct-bench")
