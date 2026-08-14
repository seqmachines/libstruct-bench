from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    sha256_bytes,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .groundtruth import (
    GroundtruthValidationError,
    TASK_ARTIFACTS,
    documents_by_task,
    validate_cross_task_links,
)


HARNESS_VERSION = "libstruct-bench-audit/0.6.0"
PHASE_SCHEMA_FILES = {
    "comparison": "protocol_audit.schema.json",
}
MODEL_ALIASES = {"default", "sonnet", "opus", "haiku", "fable"}
READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
REPAIR_TOOLS = ("Read",)
MAX_COMPARISON_REPAIR_ATTEMPTS = 2
GROUNDTRUTH_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "groundtruth"
DEFAULT_REPAIR_PROMPT = (
    Path(__file__).resolve().parents[3]
    / ".claude"
    / "prompts"
    / "audit-comparison-repair.md"
)
CURRENT_TASK_BY_SOURCE_KIND = {
    "current_t1": "T1",
    "current_t2": "T2",
    "current_t3": "T3",
}


class ClaudeAuditError(ValueError):
    """Raised when a bounded Claude audit run cannot produce a valid artifact."""


class _RepairableValidationError(ClaudeAuditError):
    """A deterministic artifact failure eligible for bounded model repair."""

    def __init__(self, message: str, *, validation_kind: str) -> None:
        super().__init__(message)
        self.validation_kind = validation_kind


@dataclass(frozen=True)
class ClaudeRunResult:
    output_dir: Path
    artifact_path: Path
    transcript_path: Path
    metadata_path: Path
    phase: str
    run_id: str


@dataclass
class _RepairAttempt:
    attempt: int
    input_artifact: dict[str, Any]
    validator_errors: list[str]
    started_at: str
    completed_at: str
    duration_seconds: float
    status: str
    completed: subprocess.CompletedProcess[bytes] | None
    candidate_artifact: dict[str, Any] | None
    changed_paths: list[str]
    result_error: str | None
    error_type: str | None
    command: list[str]
    system_prompt: str
    agent_schema: dict[str, Any]
    repair_prompt_sha256: str


def run_claude_audit(
    *,
    packet_dir: Path,
    output_dir: Path,
    output_schema_path: Path,
    packet_schema_path: Path,
    prompt_path: Path,
    skill_path: Path,
    policy_paths: Iterable[Path],
    model: str,
    run_id: str,
    effort: str = "high",
    review_mode: str = "primary",
    max_budget_usd: float = 20.0,
    timeout_seconds: int = 3600,
    claude_executable: str = "claude",
    groundtruth_schema_paths: Mapping[str, Path] | None = None,
    repair_prompt_path: Path | None = None,
    max_repair_attempts: int = MAX_COMPARISON_REPAIR_ATTEMPTS,
) -> ClaudeRunResult:
    """Run one conversion-first Claude comparison audit."""

    packet_dir = _directory(packet_dir, "audit packet")
    packet_path = _file(packet_dir / "packet.json", "packet metadata")
    output_schema_path = _file(output_schema_path, "output schema")
    packet_schema_path = _file(packet_schema_path, "packet schema")
    prompt_path = _file(prompt_path, "phase prompt")
    repair_prompt_path = _file(
        repair_prompt_path or DEFAULT_REPAIR_PROMPT,
        "comparison repair prompt",
    )
    skill_path = _file(skill_path, "audit skill")
    policies = [_file(path, "audit policy") for path in policy_paths]
    if not policies:
        raise ClaudeAuditError("at least one policy file is required")
    if model.strip().lower() in MODEL_ALIASES or not any(char.isdigit() for char in model):
        raise ClaudeAuditError("--model must be a full model ID, not a moving alias")
    if not run_id or any(char.isspace() for char in run_id):
        raise ClaudeAuditError("run ID must be a non-empty identifier without whitespace")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ClaudeAuditError("unsupported Claude effort")
    if review_mode != "primary":
        raise ClaudeAuditError(
            "Claude is the primary audit agent; independent audits must use Codex"
        )
    if max_budget_usd <= 0 or timeout_seconds <= 0:
        raise ClaudeAuditError("budget and timeout must be positive")
    if not 0 <= max_repair_attempts <= MAX_COMPARISON_REPAIR_ATTEMPTS:
        raise ClaudeAuditError(
            "comparison repair attempts must be between 0 and "
            f"{MAX_COMPARISON_REPAIR_ATTEMPTS}"
        )

    packet = load_json_object(packet_path, label="packet metadata")
    _validate(packet, packet_schema_path, "packet metadata")
    phase = packet["phase"]
    if phase not in PHASE_SCHEMA_FILES:
        raise ClaudeAuditError(f"unsupported audit phase: {phase}")
    if output_schema_path.name != PHASE_SCHEMA_FILES[phase]:
        raise ClaudeAuditError(
            f"{phase} phase requires {PHASE_SCHEMA_FILES[phase]}"
        )
    groundtruth_schemas = _resolve_groundtruth_schemas(
        phase, groundtruth_schema_paths
    )
    _verify_packet_files(packet_dir, packet)
    output_schema = load_json_object(output_schema_path, label="output schema")

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir, packet_dir)
    if output_dir.exists():
        raise ClaudeAuditError(f"run output already exists: {output_dir}")
    rejected_output_dir = _rejected_output_dir(output_dir)
    if rejected_output_dir.exists():
        raise ClaudeAuditError(
            f"rejected run output already exists: {rejected_output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    prompt_bytes = prompt_path.read_bytes()
    repair_prompt_bytes = repair_prompt_path.read_bytes()
    skill_bytes = skill_path.read_bytes()
    policy_bytes = b"\n".join(path.read_bytes() for path in policies)
    system_prompt = _system_prompt(
        phase=phase,
        prompt=prompt_bytes.decode("utf-8"),
        skill=skill_bytes.decode("utf-8"),
        policies=[path.read_text(encoding="utf-8") for path in policies],
        groundtruth_schemas=groundtruth_schemas,
    )
    primary_source_ids = sorted(
        item["source_id"]
        for item in packet["files"]
        if item["role"] == "primary_evidence"
    )
    agent_schema = _agent_output_schema(
        output_schema,
        phase,
        primary_source_ids=primary_source_ids,
    )
    claude_version = _claude_version(claude_executable)
    started_at = _now()
    started_monotonic = time.monotonic()
    command = [
        claude_executable,
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        "--no-chrome",
        "--strict-mcp-config",
        "--permission-mode",
        "plan",
        "--tools",
        ",".join(READ_ONLY_TOOLS),
        "--model",
        model,
        "--effort",
        effort,
        "--max-budget-usd",
        str(max_budget_usd),
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(agent_schema, separators=(",", ":")),
        "--system-prompt",
        system_prompt,
        _user_prompt(packet_dir, phase),
    ]
    progress = _progress_reporter(packet["protocol_id"], phase)
    progress(f"starting run {run_id}")
    try:
        completed = _run_streaming(
            command,
            cwd=packet_dir,
            timeout_seconds=timeout_seconds,
            progress=progress,
        )
    except FileNotFoundError as error:
        raise ClaudeAuditError(f"Claude executable not found: {claude_executable}") from error
    except subprocess.TimeoutExpired as error:
        raise ClaudeAuditError(
            f"Claude audit exceeded {timeout_seconds} seconds"
        ) from error
    completed_at = _now()
    run = {
        "run_id": run_id,
        "agent": "claude-code",
        "provider": "anthropic",
        "model": model,
        "tool_version": claude_version,
        "harness_version": HARNESS_VERSION,
        "review_mode": review_mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "prompt_sha256": sha256_bytes(prompt_bytes),
        "skill_sha256": sha256_bytes(skill_bytes),
        "policy_sha256": sha256_bytes(policy_bytes),
        "schema_sha256": sha256_file(output_schema_path),
        "skills": ["audit-protocol"],
        "tools": list(READ_ONLY_TOOLS),
        "permission_mode": "plan",
        "checkpoint_id": packet_manifest_checkpoint(packet_dir),
    }
    artifact: dict[str, Any] | None = None
    initial_rejected_artifact: dict[str, Any] | None = None
    repair_attempts: list[_RepairAttempt] = []
    try:
        if completed.returncode != 0:
            stdout_error = _claude_result_error(completed.stdout)
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            details = []
            if stdout_error:
                details.append(stdout_error)
            if stderr:
                details.append(f"stderr: {stderr[-1000:]}")
            raise ClaudeAuditError(
                f"Claude exited with {completed.returncode}: "
                + ("; ".join(details) or "no error details were emitted")
            )

        progress("Claude finished; validating the structured artifact")
        artifact = _extract_artifact(completed.stdout, phase)
        artifact = _finalize_artifact(
            artifact=artifact,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            run=run,
        )
        try:
            _validate_comparison_artifact(
                artifact=artifact,
                output_schema_path=output_schema_path,
                packet=packet,
                packet_dir=packet_dir,
                phase=phase,
                groundtruth_schemas=groundtruth_schemas,
            )
        except _RepairableValidationError as validation_error:
            initial_rejected_artifact = copy.deepcopy(artifact)
            if max_repair_attempts == 0:
                raise
            progress(
                "deterministic validation failed; starting bounded repair "
                f"(up to {max_repair_attempts} attempts)"
            )
            artifact, repair_attempts, repair_error = _repair_comparison_artifact(
                artifact=artifact,
                initial_artifact=initial_rejected_artifact,
                validation_error=validation_error,
                max_attempts=max_repair_attempts,
                output_dir=output_dir,
                output_schema_path=output_schema_path,
                packet=packet,
                packet_dir=packet_dir,
                phase=phase,
                run=run,
                groundtruth_schemas=groundtruth_schemas,
                agent_schema=agent_schema,
                repair_prompt=repair_prompt_bytes.decode("utf-8"),
                repair_prompt_sha256=sha256_bytes(repair_prompt_bytes),
                model=model,
                effort=effort,
                max_budget_usd=max_budget_usd,
                timeout_seconds=timeout_seconds,
                claude_executable=claude_executable,
                progress=progress,
            )
            if repair_error is not None:
                raise repair_error
            progress(
                f"repair attempt {len(repair_attempts)} produced a valid artifact"
            )

        run["completed_at"] = _now()
        artifact = _finalize_artifact(
            artifact=artifact,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            run=run,
        )
        _validate_comparison_artifact(
            artifact=artifact,
            output_schema_path=output_schema_path,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            groundtruth_schemas=groundtruth_schemas,
        )
    except Exception as error:
        run["completed_at"] = _now()
        duration = max(0.0, time.monotonic() - started_monotonic)
        try:
            rejected_dir = _preserve_rejected_run(
                output_dir=output_dir,
                completed=completed,
                artifact=initial_rejected_artifact or artifact,
                error=error,
                run=run,
                phase=phase,
                packet_path=packet_path,
                duration=duration,
                max_budget_usd=max_budget_usd,
                timeout_seconds=timeout_seconds,
                effort=effort,
                groundtruth_schemas=groundtruth_schemas,
                command=command,
                system_prompt=system_prompt,
                agent_schema=agent_schema,
                repair_attempts=repair_attempts,
                max_repair_attempts=max_repair_attempts,
            )
        except Exception as preservation_error:
            raise ClaudeAuditError(
                f"{error}; additionally failed to preserve the rejected run: "
                f"{preservation_error}"
            ) from error
        progress(f"rejected run preserved at {rejected_dir}")
        if isinstance(error, ClaudeAuditError):
            raise ClaudeAuditError(
                f"{error}; rejected run preserved at {rejected_dir}"
            ) from error
        raise

    duration = max(0.0, time.monotonic() - started_monotonic)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        transcript_path = temporary_dir / "transcript.jsonl"
        transcript_path.write_bytes(completed.stdout)
        stderr_path = temporary_dir / "stderr.txt"
        stderr_path.write_bytes(completed.stderr)
        artifact_name = "audit.json"
        artifact_path = temporary_dir / artifact_name
        write_json_atomic(artifact_path, artifact)
        repair_summaries = _write_repair_attempts(temporary_dir, repair_attempts)
        metadata = {
            "run": run,
            "phase": phase,
            "packet_sha256": sha256_file(packet_path),
            "artifact_path": artifact_name,
            "artifact_sha256": sha256_file(artifact_path),
            "transcript_path": "transcript.jsonl",
            "transcript_sha256": sha256_file(transcript_path),
            "stderr_path": "stderr.txt",
            "stderr_sha256": sha256_file(stderr_path),
            "duration_seconds": round(duration, 6),
            "max_budget_usd": max_budget_usd,
            "timeout_seconds": timeout_seconds,
            "effort": effort,
            "validation_repair": {
                "status": "succeeded" if repair_attempts else "not_needed",
                "max_attempts": max_repair_attempts,
                "attempt_count": len(repair_attempts),
                "attempts": repair_summaries,
            },
            "groundtruth_schemas": [
                {
                    "task": task,
                    "filename": path.name,
                    "sha256": sha256_file(path),
                }
                for task, path in sorted(groundtruth_schemas.items())
            ],
            "command": _redacted_command(command, system_prompt, agent_schema),
        }
        metadata_path = temporary_dir / "run-metadata.json"
        write_json_atomic(metadata_path, metadata)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    progress(f"validated and saved {output_dir / artifact_name}")
    return ClaudeRunResult(
        output_dir=output_dir,
        artifact_path=output_dir / artifact_name,
        transcript_path=output_dir / "transcript.jsonl",
        metadata_path=output_dir / "run-metadata.json",
        phase=phase,
        run_id=run_id,
    )


def _rejected_output_dir(output_dir: Path) -> Path:
    return output_dir.with_name(f"{output_dir.name}.rejected")


def _preserve_rejected_run(
    *,
    output_dir: Path,
    completed: subprocess.CompletedProcess[bytes],
    artifact: dict[str, Any] | None,
    error: Exception,
    run: dict[str, Any],
    phase: str,
    packet_path: Path,
    duration: float,
    max_budget_usd: float,
    timeout_seconds: int,
    effort: str,
    groundtruth_schemas: Mapping[str, Path],
    command: list[str],
    system_prompt: str,
    agent_schema: dict[str, Any],
    repair_attempts: list[_RepairAttempt],
    max_repair_attempts: int,
) -> Path:
    """Persist a completed but rejected model run for diagnosis and provenance."""

    rejected_dir = _rejected_output_dir(output_dir)
    if rejected_dir.exists():
        raise ClaudeAuditError(f"rejected run output already exists: {rejected_dir}")
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{rejected_dir.name}.building-", dir=rejected_dir.parent
        )
    )
    try:
        transcript_path = temporary_dir / "transcript.jsonl"
        transcript_path.write_bytes(completed.stdout)
        stderr_path = temporary_dir / "stderr.txt"
        stderr_path.write_bytes(completed.stderr)
        artifact_path: Path | None = None
        if artifact is not None:
            artifact_path = temporary_dir / "rejected-artifact.json"
            write_json_atomic(artifact_path, artifact)
        repair_summaries = _write_repair_attempts(temporary_dir, repair_attempts)
        failure = {
            "status": "rejected",
            "phase": phase,
            "run": run,
            "reason": str(error),
            "error_type": type(error).__name__,
            "returncode": completed.returncode,
            "packet_sha256": sha256_file(packet_path),
            "transcript_path": "transcript.jsonl",
            "transcript_sha256": sha256_file(transcript_path),
            "stderr_path": "stderr.txt",
            "stderr_sha256": sha256_file(stderr_path),
            "artifact_path": artifact_path.name if artifact_path else None,
            "artifact_sha256": sha256_file(artifact_path) if artifact_path else None,
            "duration_seconds": round(duration, 6),
            "max_budget_usd": max_budget_usd,
            "timeout_seconds": timeout_seconds,
            "effort": effort,
            "validation_repair": {
                "status": _repair_failure_status(repair_attempts),
                "max_attempts": max_repair_attempts,
                "attempt_count": len(repair_attempts),
                "attempts": repair_summaries,
            },
            "groundtruth_schemas": [
                {
                    "task": task,
                    "filename": path.name,
                    "sha256": sha256_file(path),
                }
                for task, path in sorted(groundtruth_schemas.items())
            ],
            "command": _redacted_command(command, system_prompt, agent_schema),
        }
        write_json_atomic(temporary_dir / "failure.json", failure)
        temporary_dir.rename(rejected_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return rejected_dir


def _repair_comparison_artifact(
    *,
    artifact: dict[str, Any],
    initial_artifact: dict[str, Any],
    validation_error: _RepairableValidationError,
    max_attempts: int,
    output_dir: Path,
    output_schema_path: Path,
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
    run: dict[str, Any],
    groundtruth_schemas: Mapping[str, Path],
    agent_schema: dict[str, Any],
    repair_prompt: str,
    repair_prompt_sha256: str,
    model: str,
    effort: str,
    max_budget_usd: float,
    timeout_seconds: int,
    claude_executable: str,
    progress: Callable[[str], None],
) -> tuple[dict[str, Any], list[_RepairAttempt], Exception | None]:
    current_artifact = artifact
    current_error: Exception = validation_error
    attempts: list[_RepairAttempt] = []
    for attempt_number in range(1, max_attempts + 1):
        progress(
            f"repair attempt {attempt_number}/{max_attempts}: "
            f"{current_error}"
        )
        attempt, candidate, result_error = _run_repair_attempt(
            attempt_number=attempt_number,
            input_artifact=current_artifact,
            initial_artifact=initial_artifact,
            validator_errors=[str(current_error)],
            output_dir=output_dir,
            output_schema_path=output_schema_path,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            run=run,
            groundtruth_schemas=groundtruth_schemas,
            agent_schema=agent_schema,
            repair_prompt=repair_prompt,
            repair_prompt_sha256=repair_prompt_sha256,
            model=model,
            effort=effort,
            max_budget_usd=max_budget_usd,
            timeout_seconds=timeout_seconds,
            claude_executable=claude_executable,
            progress=progress,
        )
        attempts.append(attempt)
        if candidate is not None:
            current_artifact = candidate
        if result_error is None:
            return current_artifact, attempts, None
        current_error = result_error
        if not isinstance(result_error, _RepairableValidationError):
            break
    return current_artifact, attempts, current_error


def _run_repair_attempt(
    *,
    attempt_number: int,
    input_artifact: dict[str, Any],
    initial_artifact: dict[str, Any],
    validator_errors: list[str],
    output_dir: Path,
    output_schema_path: Path,
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
    run: dict[str, Any],
    groundtruth_schemas: Mapping[str, Path],
    agent_schema: dict[str, Any],
    repair_prompt: str,
    repair_prompt_sha256: str,
    model: str,
    effort: str,
    max_budget_usd: float,
    timeout_seconds: int,
    claude_executable: str,
    progress: Callable[[str], None],
) -> tuple[_RepairAttempt, dict[str, Any] | None, Exception | None]:
    repair_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.repair-{attempt_number:03d}-",
            dir=output_dir.parent,
        )
    )
    repair_input_path = repair_dir / "repair-input.json"
    write_json_atomic(
        repair_input_path,
        {
            "mode": "deterministic_validation_repair",
            "attempt": attempt_number,
            "protocol_id": packet["protocol_id"],
            "source_artifact_sha256": _artifact_sha256(input_artifact),
            "validator_errors": validator_errors,
            "artifact": input_artifact,
        },
    )
    system_prompt = _repair_system_prompt(
        repair_prompt=repair_prompt,
        groundtruth_schemas=groundtruth_schemas,
    )
    command = [
        claude_executable,
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        "--no-chrome",
        "--strict-mcp-config",
        "--permission-mode",
        "plan",
        "--tools",
        ",".join(REPAIR_TOOLS),
        "--model",
        model,
        "--effort",
        effort,
        "--max-budget-usd",
        str(max_budget_usd),
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(agent_schema, separators=(",", ":")),
        "--system-prompt",
        system_prompt,
        _repair_user_prompt(repair_input_path),
    ]
    started_at = _now()
    started_monotonic = time.monotonic()
    completed: subprocess.CompletedProcess[bytes] | None = None
    candidate: dict[str, Any] | None = None
    changed_paths: list[str] = []
    result_error: Exception | None = None
    status = "execution_failed"
    try:
        completed = _run_streaming(
            command,
            cwd=repair_dir,
            timeout_seconds=timeout_seconds,
            progress=progress,
        )
        if completed.returncode != 0:
            stdout_error = _claude_result_error(completed.stdout)
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            details = []
            if stdout_error:
                details.append(stdout_error)
            if stderr:
                details.append(f"stderr: {stderr[-1000:]}")
            raise ClaudeAuditError(
                f"repair attempt {attempt_number} exited with "
                f"{completed.returncode}: "
                + ("; ".join(details) or "no error details were emitted")
            )
        candidate = _extract_artifact(completed.stdout, phase)
        candidate = _finalize_artifact(
            artifact=candidate,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            run=run,
        )
        changed_paths = _changed_json_paths(input_artifact, candidate)
        _assert_repair_scope(initial_artifact, candidate)
        _validate_comparison_artifact(
            artifact=candidate,
            output_schema_path=output_schema_path,
            packet=packet,
            packet_dir=packet_dir,
            phase=phase,
            groundtruth_schemas=groundtruth_schemas,
        )
        status = "validated"
    except _RepairableValidationError as error:
        status = "validation_failed"
        result_error = error
    except subprocess.TimeoutExpired:
        result_error = ClaudeAuditError(
            f"comparison repair attempt {attempt_number} exceeded "
            f"{timeout_seconds} seconds"
        )
    except Exception as error:
        status = "rejected"
        result_error = (
            error
            if isinstance(error, ClaudeAuditError)
            else ClaudeAuditError(
                f"comparison repair attempt {attempt_number} failed: {error}"
            )
        )
    finally:
        completed_at = _now()
        duration = max(0.0, time.monotonic() - started_monotonic)
        shutil.rmtree(repair_dir, ignore_errors=True)

    attempt = _RepairAttempt(
        attempt=attempt_number,
        input_artifact=copy.deepcopy(input_artifact),
        validator_errors=list(validator_errors),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        status=status,
        completed=completed,
        candidate_artifact=copy.deepcopy(candidate),
        changed_paths=changed_paths,
        result_error=str(result_error) if result_error is not None else None,
        error_type=type(result_error).__name__ if result_error is not None else None,
        command=command,
        system_prompt=system_prompt,
        agent_schema=agent_schema,
        repair_prompt_sha256=repair_prompt_sha256,
    )
    return attempt, candidate, result_error


def _repair_system_prompt(
    *,
    repair_prompt: str,
    groundtruth_schemas: Mapping[str, Path],
) -> str:
    rendered_schemas = []
    for task, path in sorted(groundtruth_schemas.items()):
        rendered_schemas.append(
            f"{task} — {path.name}\n{path.read_text(encoding='utf-8')}"
        )
    return (
        "You are a read-only deterministic-validation repair worker. "
        "You cannot adjudicate, apply, or introduce scientific findings.\n\n"
        "REPAIR INSTRUCTIONS\n"
        f"{repair_prompt}\n\n"
        "CANONICAL GROUND-TRUTH ARTIFACT SCHEMAS\n"
        "These schemas are formatting and consistency constraints, not evidence.\n"
        + "\n\n".join(rendered_schemas)
    )


def _repair_user_prompt(repair_input_path: Path) -> str:
    return (
        f"Read {repair_input_path.name}. It contains the complete failed artifact "
        "and the exact deterministic validator errors. Read no other file. Make "
        "only the smallest allowed repair and return the complete audit artifact "
        "satisfying the supplied JSON Schema."
    )


def _assert_repair_scope(
    initial_artifact: dict[str, Any], candidate: dict[str, Any]
) -> None:
    mutable_top_level = {"run", "audited_fields", "issues"}
    for key in sorted(set(initial_artifact) | set(candidate)):
        if key in mutable_top_level:
            continue
        if key not in initial_artifact or key not in candidate:
            raise ClaudeAuditError(
                f"repair changed protected comparison field {key!r}"
            )
        if candidate[key] != initial_artifact[key]:
            raise ClaudeAuditError(
                f"repair changed protected comparison field {key!r}"
            )

    initial_fields = _objects_by_id(
        initial_artifact.get("audited_fields"), "field_id", "audited fields"
    )
    candidate_fields = _objects_by_id(
        candidate.get("audited_fields"), "field_id", "audited fields"
    )
    if set(initial_fields) != set(candidate_fields):
        raise ClaudeAuditError("repair changed the set of audited fields")
    for field_id, initial_field in initial_fields.items():
        repaired_field = candidate_fields[field_id]
        initial_protected = {
            key: value for key, value in initial_field.items() if key != "issue_ids"
        }
        repaired_protected = {
            key: value for key, value in repaired_field.items() if key != "issue_ids"
        }
        if repaired_protected != initial_protected:
            raise ClaudeAuditError(
                f"repair changed the conclusion of audited field {field_id}"
            )

    initial_issues = _objects_by_id(
        initial_artifact.get("issues"), "issue_id", "issues"
    )
    candidate_issues = _objects_by_id(candidate.get("issues"), "issue_id", "issues")
    if set(initial_issues) != set(candidate_issues):
        raise ClaudeAuditError("repair changed the set of comparison issues")
    for issue_id, initial_issue in initial_issues.items():
        repaired_issue = candidate_issues[issue_id]
        mutable = {"run_id", "checkpoint_id"}
        if _issue_has_root_candidate(initial_issue):
            mutable.update({"proposed_value", "proposed_patch"})
        initial_protected = {
            key: value for key, value in initial_issue.items() if key not in mutable
        }
        repaired_protected = {
            key: value for key, value in repaired_issue.items() if key not in mutable
        }
        if repaired_protected != initial_protected:
            raise ClaudeAuditError(
                f"repair changed protected conclusion fields for issue {issue_id}"
            )
        if not _issue_has_root_candidate(initial_issue):
            if repaired_issue.get("proposed_value") != initial_issue.get(
                "proposed_value"
            ) or repaired_issue.get("proposed_patch") != initial_issue.get(
                "proposed_patch"
            ):
                raise ClaudeAuditError(
                    f"repair changed a non-root proposal for issue {issue_id}"
                )
            continue
        repaired_root = _issue_root_candidate(repaired_issue)
        if repaired_root is None:
            raise ClaudeAuditError(
                f"repair removed the complete root candidate for issue {issue_id}"
            )
        if repaired_issue.get("proposed_value") != repaired_root:
            raise ClaudeAuditError(
                f"repair made proposed_value and proposed_patch disagree for "
                f"issue {issue_id}"
            )


def _objects_by_id(value: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise ClaudeAuditError(f"repair scope cannot be verified: {label} is not a list")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise ClaudeAuditError(
                f"repair scope cannot be verified: {label} has no stable {key}"
            )
        item_id = item[key]
        if item_id in result:
            raise ClaudeAuditError(
                f"repair scope cannot be verified: duplicate {key} {item_id}"
            )
        result[item_id] = item
    return result


def _issue_has_root_candidate(issue: Mapping[str, Any]) -> bool:
    return _issue_root_candidate(issue) is not None


def _issue_root_candidate(issue: Mapping[str, Any]) -> dict[str, Any] | None:
    target = issue.get("target")
    operations = issue.get("proposed_patch")
    if not isinstance(target, dict) or not isinstance(operations, list):
        return None
    if target.get("kind") not in {"groundtruth_record", "new_groundtruth_record"}:
        return None
    if not all(isinstance(item, dict) for item in operations):
        return None
    mutations = [item for item in operations if item.get("op") != "test"]
    if (
        len(mutations) != 1
        or mutations[0].get("op") not in {"add", "replace"}
        or mutations[0].get("path") != ""
        or not isinstance(mutations[0].get("value"), dict)
    ):
        return None
    return mutations[0]["value"]


def _changed_json_paths(before: Any, after: Any, path: str = "") -> list[str]:
    if type(before) is not type(after):
        return [path or ""]
    if isinstance(before, dict):
        changes: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{_json_pointer_token(str(key))}"
            if key not in before or key not in after:
                changes.append(child)
            else:
                changes.extend(_changed_json_paths(before[key], after[key], child))
        return changes
    if isinstance(before, list):
        if len(before) != len(after):
            return [path or ""]
        changes = []
        for index, (before_item, after_item) in enumerate(zip(before, after)):
            changes.extend(
                _changed_json_paths(before_item, after_item, f"{path}/{index}")
            )
        return changes
    return [] if before == after else [path or ""]


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _artifact_sha256(artifact: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(artifact))


def _write_repair_attempts(
    root: Path, attempts: Iterable[_RepairAttempt]
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for attempt in attempts:
        relative_dir = Path("repair-attempts") / f"attempt-{attempt.attempt:03d}"
        attempt_dir = root / relative_dir
        attempt_dir.mkdir(parents=True, exist_ok=False)
        input_path = attempt_dir / "input-artifact.json"
        errors_path = attempt_dir / "validator-errors.json"
        changes_path = attempt_dir / "changed-paths.json"
        write_json_atomic(input_path, attempt.input_artifact)
        write_json_atomic(errors_path, {"errors": attempt.validator_errors})
        write_json_atomic(changes_path, {"paths": attempt.changed_paths})

        transcript_path: Path | None = None
        stderr_path: Path | None = None
        if attempt.completed is not None:
            transcript_path = attempt_dir / "transcript.jsonl"
            stderr_path = attempt_dir / "stderr.txt"
            transcript_path.write_bytes(attempt.completed.stdout)
            stderr_path.write_bytes(attempt.completed.stderr)
        candidate_path: Path | None = None
        if attempt.candidate_artifact is not None:
            candidate_path = attempt_dir / "candidate-artifact.json"
            write_json_atomic(candidate_path, attempt.candidate_artifact)

        summary = {
            "attempt": attempt.attempt,
            "status": attempt.status,
            "started_at": attempt.started_at,
            "completed_at": attempt.completed_at,
            "duration_seconds": round(attempt.duration_seconds, 6),
            "input_artifact_path": str(relative_dir / input_path.name),
            "input_artifact_sha256": sha256_file(input_path),
            "validator_errors_path": str(relative_dir / errors_path.name),
            "validator_errors_sha256": sha256_file(errors_path),
            "changed_paths_path": str(relative_dir / changes_path.name),
            "changed_paths_sha256": sha256_file(changes_path),
            "candidate_artifact_path": (
                str(relative_dir / candidate_path.name) if candidate_path else None
            ),
            "candidate_artifact_sha256": (
                sha256_file(candidate_path) if candidate_path else None
            ),
            "transcript_path": (
                str(relative_dir / transcript_path.name) if transcript_path else None
            ),
            "transcript_sha256": (
                sha256_file(transcript_path) if transcript_path else None
            ),
            "stderr_path": (
                str(relative_dir / stderr_path.name) if stderr_path else None
            ),
            "stderr_sha256": sha256_file(stderr_path) if stderr_path else None,
            "result_error": attempt.result_error,
            "error_type": attempt.error_type,
            "repair_prompt_sha256": attempt.repair_prompt_sha256,
            "system_prompt_sha256": sha256_bytes(attempt.system_prompt.encode()),
            "tools": list(REPAIR_TOOLS),
            "permission_mode": "plan",
            "command": _redacted_command(
                attempt.command, attempt.system_prompt, attempt.agent_schema
            ),
        }
        metadata_path = attempt_dir / "attempt.json"
        write_json_atomic(metadata_path, summary)
        summary["metadata_path"] = str(relative_dir / metadata_path.name)
        summary["metadata_sha256"] = sha256_file(metadata_path)
        summaries.append(summary)
    return summaries


def _repair_failure_status(attempts: list[_RepairAttempt]) -> str:
    if not attempts:
        return "not_attempted"
    if all(attempt.status == "validation_failed" for attempt in attempts):
        return "exhausted"
    return "failed"


def _run_streaming(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    progress: Callable[[str], None],
) -> subprocess.CompletedProcess[bytes]:
    """Capture the exact transcript while reporting safe, readable live progress."""

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        raise ClaudeAuditError("Claude process pipes were not created")

    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    seen_tool_ids: set[str] = set()

    def read_stdout() -> None:
        for line in iter(process.stdout.readline, b""):
            stdout_parts.append(line)
            for message in _progress_messages(line, cwd, seen_tool_ids):
                progress(message)

    def read_stderr() -> None:
        for line in iter(process.stderr.readline, b""):
            stderr_parts.append(line)
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                progress(f"Claude: {message}")

    readers = [
        threading.Thread(target=read_stdout, name="claude-audit-stdout", daemon=True),
        threading.Thread(target=read_stderr, name="claude-audit-stderr", daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    next_heartbeat = started + 30
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - started
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                process.wait(timeout=min(1.0, remaining))
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if process.poll() is None and now >= next_heartbeat:
                progress(
                    f"still running ({int(now - started)}s, "
                    f"{len(seen_tool_ids)} source operations)"
                )
                next_heartbeat = now + 30
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        for reader in readers:
            reader.join()

    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=b"".join(stdout_parts),
        stderr=b"".join(stderr_parts),
    )


def _progress_reporter(protocol_id: str, phase: str) -> Callable[[str], None]:
    lock = threading.Lock()

    def report(message: str) -> None:
        with lock:
            print(f"[{protocol_id} {phase}] {message}", file=sys.stderr, flush=True)

    return report


def _progress_messages(
    line: bytes, packet_dir: Path, seen_tool_ids: set[str]
) -> list[str]:
    """Render tool activity from stream-json without exposing model reasoning."""

    try:
        event = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return []

    rendered: list[str] = []
    for block in message["content"]:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_id = block.get("id")
        if isinstance(tool_id, str):
            if tool_id in seen_tool_ids:
                continue
            seen_tool_ids.add(tool_id)
        rendered.append(_tool_progress(block, packet_dir))
    return rendered


def _claude_result_error(stdout: bytes) -> str | None:
    """Extract the useful API failure from Claude's stream-json result event."""

    error: str | None = None
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        subtype = event.get("subtype")
        status = event.get("api_error_status")
        if not event.get("is_error") and status is None and subtype == "success":
            continue
        details: list[str] = []
        result = event.get("result")
        if isinstance(result, str) and result.strip():
            details.append(result.strip())
        if status is not None:
            details.append(f"api_error_status={status}")
        if not details and isinstance(subtype, str) and subtype:
            details.append(f"result subtype={subtype}")
        if details:
            error = "; ".join(details)
    return error


def _tool_progress(block: dict[str, Any], packet_dir: Path) -> str:
    name = str(block.get("name", "tool"))
    arguments = block.get("input")
    if not isinstance(arguments, dict):
        return f"using {name}"

    if name == "Read":
        target = _display_path(arguments.get("file_path"), packet_dir)
        pages = arguments.get("pages")
        return f"reading {target}" + (f" (pages {pages})" if pages else "")
    if name == "Glob":
        pattern = arguments.get("pattern", "")
        root = _display_path(arguments.get("path"), packet_dir)
        return f"listing {pattern!r} under {root}"
    if name == "Grep":
        pattern = arguments.get("pattern", "")
        root = _display_path(arguments.get("path"), packet_dir)
        return f"searching for {pattern!r} in {root}"
    return f"using {name}"


def _display_path(value: Any, packet_dir: Path) -> str:
    if not isinstance(value, str) or not value:
        return "."
    path = Path(value)
    try:
        return str(path.resolve().relative_to(packet_dir))
    except ValueError:
        return str(path)


def packet_manifest_checkpoint(packet_dir: Path) -> str:
    manifest = load_json_object(packet_dir / "manifest.json", label="packet manifest")
    checkpoint = manifest.get("checkpoint")
    if not isinstance(checkpoint, dict) or not isinstance(
        checkpoint.get("checkpoint_id"), str
    ):
        raise ClaudeAuditError("packet manifest is missing checkpoint metadata")
    return checkpoint["checkpoint_id"]


def _system_prompt(
    *,
    phase: str,
    prompt: str,
    skill: str,
    policies: list[str],
    groundtruth_schemas: Mapping[str, Path],
) -> str:
    schema_section = ""
    if groundtruth_schemas:
        rendered = []
        for task, path in sorted(groundtruth_schemas.items()):
            rendered.append(
                f"{task} — {path.name}\n{path.read_text(encoding='utf-8')}"
            )
        schema_section = (
            "\n\nCANONICAL GROUND-TRUTH ARTIFACT SCHEMAS\n"
            "These schemas constrain proposed T1-T3 documents; they are not "
            "scientific evidence. Any complete new or root-converted "
            "ground-truth document must satisfy its task schema exactly.\n"
            + "\n\n".join(rendered)
        )
    return (
        "You are a read-only sequencing-library ground-truth audit assistant. "
        "A human reviewer is the final authority. Use only packet-listed files.\n\n"
        f"PHASE: {phase}\n\n"
        "PHASE INSTRUCTIONS\n"
        f"{prompt}\n\n"
        "AUDIT SKILL\n"
        f"{skill}\n\n"
        "AUDIT POLICIES\n"
        + "\n\n".join(policies)
        + schema_section
    )


def _user_prompt(packet_dir: Path, phase: str) -> str:
    action = (
        "Read packet.json and manifest.json. First use only legacy_curated_html and "
        "current_benchmark_record files to convert the existing human curation into "
        "canonical T1, T2, and T3 candidates. Finish that conversion before opening "
        "primary_evidence or its renditions. Then read every primary source and "
        "rendition. In source_coverage, list every included primary_evidence source "
        "exactly once and do not list legacy, current-record, TSV, or benchmark-run "
        "inputs. Verify the completed legacy-derived candidates field by field. "
        "Optional benchmark-run artifacts "
        "are only for error attribution. Use the embedded canonical schemas for every "
        "complete candidate. Propose issues, but do not approve or apply changes."
    )
    return (
        f"Audit packet: {packet_dir}\n{action}\n"
        "Return only one object satisfying the supplied JSON Schema."
    )


def _agent_output_schema(
    schema: dict[str, Any],
    phase: str,
    *,
    primary_source_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    relaxed = copy.deepcopy(schema)
    # Claude CLI does not register the 2020-12 meta-schema, and Anthropic's
    # StructuredOutput tool rejects combinators at the input-schema root. The
    # unmodified canonical schema is still applied locally after generation.
    relaxed.pop("$schema", None)
    relaxed.pop("$id", None)
    for keyword in ("allOf", "anyOf", "oneOf"):
        relaxed.pop(keyword, None)
    injected = {
        "comparison": {
            "audit_id", "protocol_id", "packet_sha256",
            "input_manifest_sha256", "baseline_artifacts", "run",
        },
    }[phase]
    relaxed["required"] = [
        item for item in relaxed.get("required", []) if item not in injected
    ]
    if phase == "comparison" and primary_source_ids is not None:
        source_ids = sorted(set(primary_source_ids))
        if not source_ids:
            raise ClaudeAuditError(
                "comparison output schema requires at least one primary source ID"
            )
        relaxed["$defs"]["source_coverage"]["properties"]["source_id"] = {
            "type": "string",
            "enum": source_ids,
        }
    return relaxed


def _finalize_artifact(
    *,
    artifact: dict[str, Any],
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(artifact)
    value["protocol_id"] = packet["protocol_id"]
    value["packet_sha256"] = sha256_file(packet_dir / "packet.json")
    value["input_manifest_sha256"] = packet["input_manifest"]["source_sha256"]
    value["run"] = run
    value["audit_id"] = f"{packet['protocol_id']}:audit:{run['run_id']}"
    value["baseline_artifacts"] = [
        {"source_id": item["source_id"], "sha256": item["sha256"]}
        for item in packet["files"]
        if item["role"] == "current_benchmark_record"
        and item["source_kind"] in {"current_t1", "current_t2", "current_t3"}
    ]
    for issue in value.get("issues", []):
        issue["run_id"] = run["run_id"]
        issue["checkpoint_id"] = run["checkpoint_id"]
    return value


def _validate_comparison_artifact(
    *,
    artifact: dict[str, Any],
    output_schema_path: Path,
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
    groundtruth_schemas: Mapping[str, Path],
) -> None:
    """Run the complete deterministic validation gate for a comparison artifact."""

    _validate(
        artifact,
        output_schema_path,
        f"{phase} audit artifact",
        repairable=True,
        validation_kind="audit_schema",
    )
    _validate_semantics(artifact, packet, packet_dir, phase, groundtruth_schemas)


def _validate_semantics(
    artifact: dict[str, Any],
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
    groundtruth_schemas: Mapping[str, Path],
) -> None:
    expected_primary = {
        item["source_id"]
        for item in packet["files"]
        if item["role"] == "primary_evidence"
    }
    coverage = artifact["source_coverage"]
    covered_primary = {item["source_id"] for item in coverage}
    if len(covered_primary) != len(coverage):
        raise ClaudeAuditError("source_coverage contains duplicate source IDs")
    if covered_primary != expected_primary:
        raise ClaudeAuditError(
            "primary-source coverage mismatch; "
            f"missing={sorted(expected_primary - covered_primary)}, "
            f"extra={sorted(covered_primary - expected_primary)}"
        )
    unreadable = [
        item["source_id"] for item in coverage if item["status"] == "unreadable"
    ]
    if unreadable:
        raise ClaudeAuditError(
            "primary sources were unreadable and must be repaired before review: "
            + ", ".join(unreadable)
        )

    field_values = artifact["audited_fields"]
    field_ids = {field["field_id"] for field in field_values}
    if len(field_ids) != len(field_values):
        raise ClaudeAuditError("audited field ledger contains duplicate field IDs")
    issue_ids: set[str] = set()
    baseline_ids = {item["source_id"] for item in artifact["baseline_artifacts"]}
    baseline_tasks, legacy_baseline_ids = _baseline_schema_status(
        packet, packet_dir, groundtruth_schemas
    )
    new_artifact_ids: set[str] = set()
    new_artifact_filenames: set[str] = set()
    root_conversion_issues: dict[str, str] = {}
    root_conversion_candidates: dict[str, dict[str, Any]] = {}
    groundtruth_patch_issues: dict[str, list[str]] = {}
    new_groundtruth_candidates: dict[str, dict[str, Any]] = {}
    expected_filenames = {
        task: details["filename"] for task, details in TASK_ARTIFACTS.items()
    }
    allowed_evidence_ids = {item["source_id"] for item in packet["files"]}
    conversion_evidence_ids = {
        item["source_id"]
        for item in packet["files"]
        if item["role"] in {"legacy_curated_html", "current_benchmark_record"}
    }
    for issue in artifact["issues"]:
        issue_id = issue["issue_id"]
        if issue_id in issue_ids:
            raise ClaudeAuditError(f"duplicate issue ID: {issue_id}")
        issue_ids.add(issue_id)
        if issue["field_id"] not in field_ids:
            raise ClaudeAuditError(
                f"issue {issue_id} references unknown field {issue['field_id']}"
            )
        cited_ids = {item["source_id"] for item in issue["evidence"]}
        if not cited_ids.issubset(allowed_evidence_ids):
            raise ClaudeAuditError(
                f"issue {issue_id} cites sources outside the comparison packet"
            )
        if issue["recommendation"] != "propose_change":
            continue
        target_kind = issue["target"]["kind"]
        source_id = issue["target"].get("artifact_source_id")
        if target_kind == "groundtruth_record" and source_id not in baseline_ids:
            raise ClaudeAuditError(
                f"issue {issue_id} targets unknown baseline {source_id!r}"
            )
        if target_kind == "groundtruth_record":
            expected_task = baseline_tasks.get(source_id)
            if issue["task"] != expected_task:
                raise ClaudeAuditError(
                    f"issue {issue_id} targets {source_id!r} as {issue['task']}; "
                    f"expected {expected_task}"
                )
            groundtruth_patch_issues.setdefault(source_id, []).append(issue_id)
            candidate = _root_replacement_candidate(issue["proposed_patch"])
            if candidate is not None:
                previous = root_conversion_issues.setdefault(source_id, issue_id)
                if previous != issue_id:
                    raise ClaudeAuditError(
                        f"baseline {source_id!r} has multiple root conversion issues"
                    )
                if candidate.get("protocol_id") != packet["protocol_id"]:
                    raise ClaudeAuditError(
                        f"root conversion candidate {issue_id} uses the wrong protocol_id"
                    )
                _validate(
                    candidate,
                    groundtruth_schemas[issue["task"]],
                    f"root conversion candidate {issue_id}",
                    repairable=True,
                    validation_kind="groundtruth_schema",
                )
                unexpected_evidence = (
                    _nested_evidence_source_ids(candidate)
                    - conversion_evidence_ids
                )
                if unexpected_evidence:
                    raise ClaudeAuditError(
                        f"root conversion candidate {issue_id} uses non-legacy "
                        "evidence: " + ", ".join(sorted(unexpected_evidence))
                    )
                root_conversion_candidates[source_id] = candidate
        if target_kind == "new_groundtruth_record":
            if source_id in baseline_ids:
                raise ClaudeAuditError(
                    f"issue {issue_id} tries to recreate existing baseline {source_id!r}"
                )
            filename = issue["target"]["artifact_filename"]
            if source_id in new_artifact_ids or filename in new_artifact_filenames:
                raise ClaudeAuditError(
                    f"duplicate new ground-truth artifact target in issue {issue_id}"
                )
            _validate_new_artifact_patch(issue_id, issue["proposed_patch"])
            if issue["task"] not in expected_filenames or filename != expected_filenames[issue["task"]]:
                raise ClaudeAuditError(
                    f"new artifact issue {issue_id} uses the wrong task filename"
                )
            candidate = _new_artifact_candidate(issue_id, issue["proposed_patch"])
            if candidate.get("protocol_id") != packet["protocol_id"]:
                raise ClaudeAuditError(
                    f"new ground-truth candidate {issue_id} uses the wrong protocol_id"
                )
            _validate(
                candidate,
                groundtruth_schemas[issue["task"]],
                f"new ground-truth candidate {issue_id}",
                repairable=True,
                validation_kind="groundtruth_schema",
            )
            new_groundtruth_candidates[source_id] = candidate
            new_artifact_ids.add(source_id)
            new_artifact_filenames.add(filename)
    missing_conversions = sorted(legacy_baseline_ids - set(root_conversion_issues))
    if missing_conversions:
        raise ClaudeAuditError(
            "legacy-shaped baselines require schema-valid root conversion issues: "
            + ", ".join(missing_conversions)
        )
    overlapping_conversions = sorted(
        source_id
        for source_id in root_conversion_issues
        if len(groundtruth_patch_issues.get(source_id, [])) > 1
    )
    if overlapping_conversions:
        raise ClaudeAuditError(
            "root-converted baselines cannot also receive field patches; record "
            "source deltas without patches and compile them into the reviewed root: "
            + ", ".join(overlapping_conversions)
        )
    _validate_linked_root_candidates(
        packet=packet,
        packet_dir=packet_dir,
        baseline_tasks=baseline_tasks,
        root_candidates=root_conversion_candidates,
        new_candidates=new_groundtruth_candidates,
    )
    referenced = {
        issue_id
        for field in artifact["audited_fields"]
        for issue_id in field.get("issue_ids", [])
    }
    if referenced != issue_ids:
        missing = sorted(issue_ids - referenced)
        unknown = sorted(referenced - issue_ids)
        raise _RepairableValidationError(
            "audited field ledger and issues must reference each other exactly; "
            f"issues_missing_from_ledger={missing}, "
            f"unknown_issue_ids_in_ledger={unknown}",
            validation_kind="field_issue_linkage",
        )


def _validate_new_artifact_patch(
    issue_id: str, operations: list[dict[str, Any]]
) -> None:
    mutations = [item for item in operations if item["op"] != "test"]
    if (
        len(mutations) != 1
        or mutations[0]["op"] not in {"add", "replace"}
        or mutations[0]["path"] != ""
    ):
        raise ClaudeAuditError(
            f"new artifact issue {issue_id} must contain one root add/replace patch"
        )


def _new_artifact_candidate(
    issue_id: str, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    mutation = next(item for item in operations if item["op"] != "test")
    value = mutation.get("value")
    if not isinstance(value, dict):
        raise ClaudeAuditError(
            f"new ground-truth candidate {issue_id} must be a JSON object"
        )
    return value


def _root_replacement_candidate(
    operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    mutations = [item for item in operations if item["op"] != "test"]
    if (
        len(mutations) != 1
        or mutations[0]["op"] != "replace"
        or mutations[0]["path"] != ""
    ):
        return None
    value = mutations[0].get("value")
    return value if isinstance(value, dict) else None


def _nested_evidence_source_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and isinstance(item.get("source_id"), str):
                    result.add(item["source_id"])
        for child in value.values():
            result.update(_nested_evidence_source_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_nested_evidence_source_ids(child))
    return result


def _baseline_schema_status(
    packet: dict[str, Any],
    packet_dir: Path,
    groundtruth_schemas: Mapping[str, Path],
) -> tuple[dict[str, str], set[str]]:
    tasks: dict[str, str] = {}
    legacy: set[str] = set()
    for item in packet["files"]:
        task = CURRENT_TASK_BY_SOURCE_KIND.get(item["source_kind"])
        if task is None:
            continue
        source_id = item["source_id"]
        tasks[source_id] = task
        document = load_json_object(
            packet_dir / item["path"], label=f"baseline {source_id}"
        )
        try:
            validate_document(
                document,
                groundtruth_schemas[task],
                label=f"baseline {source_id}",
            )
        except AuditArtifactError:
            legacy.add(source_id)
    return tasks, legacy


def _validate_linked_root_candidates(
    *,
    packet: dict[str, Any],
    packet_dir: Path,
    baseline_tasks: Mapping[str, str],
    root_candidates: Mapping[str, dict[str, Any]],
    new_candidates: Mapping[str, dict[str, Any]],
) -> None:
    documents: dict[str, dict[str, Any]] = {}
    for item in packet["files"]:
        source_id = item["source_id"]
        if source_id not in baseline_tasks:
            continue
        documents[source_id] = root_candidates.get(source_id) or load_json_object(
            packet_dir / item["path"], label=f"baseline {source_id}"
        )
    documents.update(new_candidates)
    try:
        validate_cross_task_links(documents_by_task(documents))
    except GroundtruthValidationError as error:
        raise _RepairableValidationError(
            f"linked root conversion candidates are inconsistent: {error}",
            validation_kind="linked_groundtruth",
        ) from error


def _resolve_groundtruth_schemas(
    phase: str, configured: Mapping[str, Path] | None
) -> dict[str, Path]:
    if phase != "comparison":
        raise ClaudeAuditError(f"unsupported audit phase: {phase}")
    paths = configured or {
        task: GROUNDTRUTH_SCHEMA_DIR / details["schema"]
        for task, details in TASK_ARTIFACTS.items()
    }
    expected = set(TASK_ARTIFACTS)
    if set(paths) != expected:
        raise ClaudeAuditError(
            "ground-truth schema map must contain exactly T1, T2, and T3"
        )
    resolved: dict[str, Path] = {}
    for task, path in paths.items():
        schema_path = _file(path, f"{task} ground-truth schema")
        expected_name = TASK_ARTIFACTS[task]["schema"]
        if schema_path.name != expected_name:
            raise ClaudeAuditError(
                f"{task} requires canonical schema filename {expected_name}"
            )
        resolved[task] = schema_path
    return resolved


def _verify_packet_files(packet_dir: Path, packet: dict[str, Any]) -> None:
    manifest_path = packet_dir / packet["input_manifest"]["path"]
    if sha256_file(manifest_path) != packet["input_manifest"]["sha256"]:
        raise ClaudeAuditError("packet manifest hash mismatch")
    for item in packet["files"]:
        path = packet_dir / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ClaudeAuditError(f"packet file is missing or stale: {item['path']}")
    for item in packet.get("renditions", []):
        path = packet_dir / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ClaudeAuditError(
                f"packet rendition is missing or stale: {item['path']}"
            )
def _extract_artifact(stdout: bytes, phase: str) -> dict[str, Any]:
    required_markers = {
        "source_coverage",
        "audited_fields",
        "issues",
        "disposition",
    }
    candidates: list[Any] = []
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        candidates.extend(_walk_candidates(event))
    for candidate in reversed(candidates):
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                continue
        if isinstance(candidate, dict) and required_markers.issubset(candidate):
            return candidate
    raise ClaudeAuditError("Claude transcript did not contain a structured audit artifact")


def _walk_candidates(value: Any) -> list[Any]:
    found = [value]
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"structured_output", "result", "content", "message"}:
                found.extend(_walk_candidates(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_candidates(child))
    return found


def _claude_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"], check=True, capture_output=True, timeout=30
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ClaudeAuditError(f"cannot determine Claude version: {error}") from error
    version = result.stdout.decode("utf-8", errors="replace").strip()
    if not version:
        raise ClaudeAuditError("Claude --version returned an empty value")
    return version


def _redacted_command(
    command: list[str], system_prompt: str, agent_schema: dict[str, Any]
) -> list[str]:
    result = list(command)
    result[result.index(system_prompt)] = f"<system-prompt:{sha256_bytes(system_prompt.encode())}>"
    schema_text = json.dumps(agent_schema, separators=(",", ":"))
    result[result.index(schema_text)] = f"<json-schema:{sha256_bytes(schema_text.encode())}>"
    return result


def _validate(
    document: dict[str, Any],
    schema: Path,
    label: str,
    *,
    repairable: bool = False,
    validation_kind: str = "schema",
) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        if repairable:
            raise _RepairableValidationError(
                str(error), validation_kind=validation_kind
            ) from error
        raise ClaudeAuditError(str(error)) from error


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ClaudeAuditError(f"{label} does not exist: {path}")
    return resolved


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ClaudeAuditError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(output_dir: Path, packet_dir: Path) -> None:
    if output_dir == packet_dir or output_dir.is_relative_to(packet_dir):
        raise ClaudeAuditError("run output must not be inside the read-only packet")
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (
        output_dir == repo or output_dir.is_relative_to(repo)
    ):
        raise ClaudeAuditError("audit run output must be outside libstruct-bench")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
