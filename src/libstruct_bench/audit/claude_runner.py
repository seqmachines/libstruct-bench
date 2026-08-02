from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_bytes,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .groundtruth import TASK_ARTIFACTS


HARNESS_VERSION = "libstruct-bench-audit/0.4.0"
PHASE_SCHEMA_FILES = {
    "evidence": "protocol_evidence.schema.json",
    "comparison": "protocol_audit.schema.json",
}
MODEL_ALIASES = {"default", "sonnet", "opus", "haiku", "fable"}
READ_ONLY_TOOLS = ("Read", "Glob", "Grep")


class ClaudeAuditError(ValueError):
    """Raised when a bounded Claude audit run cannot produce a valid artifact."""


@dataclass(frozen=True)
class ClaudeRunResult:
    output_dir: Path
    artifact_path: Path
    transcript_path: Path
    metadata_path: Path
    phase: str
    run_id: str


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
) -> ClaudeRunResult:
    """Run one isolated Claude evidence or comparison phase."""

    packet_dir = _directory(packet_dir, "audit packet")
    packet_path = _file(packet_dir / "packet.json", "packet metadata")
    output_schema_path = _file(output_schema_path, "output schema")
    packet_schema_path = _file(packet_schema_path, "packet schema")
    prompt_path = _file(prompt_path, "phase prompt")
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

    packet = load_json_object(packet_path, label="packet metadata")
    _validate(packet, packet_schema_path, "packet metadata")
    phase = packet["phase"]
    if phase not in PHASE_SCHEMA_FILES:
        raise ClaudeAuditError(f"unsupported audit phase: {phase}")
    if output_schema_path.name != PHASE_SCHEMA_FILES[phase]:
        raise ClaudeAuditError(
            f"{phase} phase requires {PHASE_SCHEMA_FILES[phase]}"
        )
    _verify_packet_files(packet_dir, packet)
    output_schema = load_json_object(output_schema_path, label="output schema")

    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir, packet_dir)
    if output_dir.exists():
        raise ClaudeAuditError(f"run output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    prompt_bytes = prompt_path.read_bytes()
    skill_bytes = skill_path.read_bytes()
    policy_bytes = b"\n".join(path.read_bytes() for path in policies)
    system_prompt = _system_prompt(
        phase=phase,
        prompt=prompt_bytes.decode("utf-8"),
        skill=skill_bytes.decode("utf-8"),
        policies=[path.read_text(encoding="utf-8") for path in policies],
    )
    agent_schema = _agent_output_schema(output_schema, phase)
    claude_version = _claude_version(claude_executable)
    started_at = _now()
    started_monotonic = datetime.now(timezone.utc).timestamp()
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
    try:
        completed = subprocess.run(
            command,
            cwd=packet_dir,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise ClaudeAuditError(f"Claude executable not found: {claude_executable}") from error
    except subprocess.TimeoutExpired as error:
        raise ClaudeAuditError(
            f"Claude audit exceeded {timeout_seconds} seconds"
        ) from error
    completed_at = _now()
    duration = max(0.0, datetime.now(timezone.utc).timestamp() - started_monotonic)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ClaudeAuditError(
            f"Claude exited with {completed.returncode}: {stderr[-1000:]}"
        )

    artifact = _extract_artifact(completed.stdout, phase)
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
    artifact = _finalize_artifact(
        artifact=artifact,
        packet=packet,
        packet_dir=packet_dir,
        phase=phase,
        run=run,
    )
    _validate(artifact, output_schema_path, f"{phase} audit artifact")
    _validate_semantics(artifact, packet, packet_dir, phase)

    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    try:
        transcript_path = temporary_dir / "transcript.jsonl"
        transcript_path.write_bytes(completed.stdout)
        stderr_path = temporary_dir / "stderr.txt"
        stderr_path.write_bytes(completed.stderr)
        artifact_name = "evidence.json" if phase == "evidence" else "audit.json"
        artifact_path = temporary_dir / artifact_name
        write_json_atomic(artifact_path, artifact)
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
            "command": _redacted_command(command, system_prompt, agent_schema),
        }
        metadata_path = temporary_dir / "run-metadata.json"
        write_json_atomic(metadata_path, metadata)
        temporary_dir.rename(output_dir)
    except BaseException:
        import shutil

        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return ClaudeRunResult(
        output_dir=output_dir,
        artifact_path=output_dir / artifact_name,
        transcript_path=output_dir / "transcript.jsonl",
        metadata_path=output_dir / "run-metadata.json",
        phase=phase,
        run_id=run_id,
    )


def packet_manifest_checkpoint(packet_dir: Path) -> str:
    manifest = load_json_object(packet_dir / "manifest.json", label="packet manifest")
    checkpoint = manifest.get("checkpoint")
    if not isinstance(checkpoint, dict) or not isinstance(
        checkpoint.get("checkpoint_id"), str
    ):
        raise ClaudeAuditError("packet manifest is missing checkpoint metadata")
    return checkpoint["checkpoint_id"]


def _system_prompt(
    *, phase: str, prompt: str, skill: str, policies: list[str]
) -> str:
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
    )


def _user_prompt(packet_dir: Path, phase: str) -> str:
    if phase == "evidence":
        action = (
            "Read packet.json, manifest.json, every file under primary_evidence, "
            "and every packet-listed rendition. Account for every approved source "
            "before independently extracting T1, T2, and graph-shaped T3 evidence."
        )
    else:
        action = (
            "Read the frozen evidence first. Then compare it with every packet-listed "
            "legacy HTML, current T1/T2/T3 record, reviewed TSV projection, and optional "
            "benchmark-run artifact. Do not alter the frozen evidence or approve changes."
        )
    return (
        f"Audit packet: {packet_dir}\n{action}\n"
        "Return only one object satisfying the supplied JSON Schema."
    )


def _agent_output_schema(schema: dict[str, Any], phase: str) -> dict[str, Any]:
    relaxed = copy.deepcopy(schema)
    # Claude CLI's validator does not register the 2020-12 meta-schema. The
    # unmodified schema is still applied locally after generation.
    relaxed.pop("$schema", None)
    relaxed.pop("$id", None)
    injected = {
        "evidence": {
            "evidence_id", "protocol_id", "packet_sha256",
            "input_manifest_sha256", "run",
        },
        "comparison": {
            "audit_id", "protocol_id", "packet_sha256",
            "input_manifest_sha256", "evidence_id", "evidence_sha256",
            "baseline_artifacts", "run",
        },
    }[phase]
    relaxed["required"] = [
        item for item in relaxed.get("required", []) if item not in injected
    ]
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
    if phase == "evidence":
        value["evidence_id"] = f"{packet['protocol_id']}:evidence:{run['run_id']}"
    else:
        evidence_path = packet_dir / packet["frozen_evidence"]["path"]
        evidence = load_json_object(evidence_path, label="frozen evidence")
        value["audit_id"] = f"{packet['protocol_id']}:audit:{run['run_id']}"
        value["evidence_id"] = evidence["evidence_id"]
        value["evidence_sha256"] = packet["frozen_evidence"]["sha256"]
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


def _validate_semantics(
    artifact: dict[str, Any],
    packet: dict[str, Any],
    packet_dir: Path,
    phase: str,
) -> None:
    if phase == "evidence":
        expected = {
            item["source_id"]
            for item in packet["files"]
            if item["role"] == "primary_evidence"
        }
        coverage = artifact["source_coverage"]
        actual = {item["source_id"] for item in coverage}
        if len(actual) != len(coverage):
            raise ClaudeAuditError("source_coverage contains duplicate source IDs")
        if actual != expected:
            raise ClaudeAuditError(
                "source coverage mismatch; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        unreadable = [
            item["source_id"] for item in coverage if item["status"] == "unreadable"
        ]
        if unreadable:
            raise ClaudeAuditError(
                "primary sources were unreadable and must be repaired before comparison: "
                + ", ".join(unreadable)
            )
        return

    evidence_path = packet_dir / packet["frozen_evidence"]["path"]
    if sha256_file(evidence_path) != artifact["evidence_sha256"]:
        raise ClaudeAuditError("comparison artifact references stale frozen evidence")
    evidence = load_json_object(evidence_path, label="frozen evidence")
    if evidence["evidence_id"] != artifact["evidence_id"]:
        raise ClaudeAuditError("comparison artifact references the wrong evidence ID")

    field_values = artifact["audited_fields"]
    field_ids = {field["field_id"] for field in field_values}
    if len(field_ids) != len(field_values):
        raise ClaudeAuditError("audited field ledger contains duplicate field IDs")
    issue_ids: set[str] = set()
    baseline_ids = {item["source_id"] for item in artifact["baseline_artifacts"]}
    new_artifact_ids: set[str] = set()
    new_artifact_filenames: set[str] = set()
    expected_filenames = {
        task: details["filename"] for task, details in TASK_ARTIFACTS.items()
    }
    allowed_evidence_ids = {
        item["source_id"] for item in packet["files"]
    } | {item["source_id"] for item in evidence["source_coverage"]}
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
            new_artifact_ids.add(source_id)
            new_artifact_filenames.add(filename)
    referenced = {
        issue_id
        for field in artifact["audited_fields"]
        for issue_id in field.get("issue_ids", [])
    }
    if referenced != issue_ids:
        raise ClaudeAuditError(
            "audited field ledger and issues must reference each other exactly"
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
    frozen = packet.get("frozen_evidence")
    if frozen is not None:
        path = packet_dir / frozen["path"]
        if not path.is_file() or sha256_file(path) != frozen["sha256"]:
            raise ClaudeAuditError("frozen evidence is missing or stale")


def _extract_artifact(stdout: bytes, phase: str) -> dict[str, Any]:
    required_markers = (
        {"source_coverage", "t1", "t2", "t3"}
        if phase == "evidence"
        else {"audited_fields", "issues", "disposition"}
    )
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


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
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
