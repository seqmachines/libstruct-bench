"""Resolve explicitly named inputs within known roots. Never execute saved task code."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from libstruct_bench.protocol_paths import scg_source_roots

from .discovery import read_json
from .storage import files, key_path, sha256


def within(root, rel):
    try:
        p = root / key_path(rel)
    except ValueError:
        return None
    return p if p.resolve() == p and p.is_file() else None


def task_identity(path):
    from dirhash import dirhash

    # Task.checksum covers all directory contents. Only use a match as evidence.
    if any(p.is_symlink() for p in path.rglob("*")):
        return None, None
    legacy = dirhash(path, "sha256")
    try:
        from harbor.publisher.packager import Packager
    except ImportError:
        return legacy, None
    return legacy, "sha256:" + Packager.compute_content_hash(path)[0]


def toml_metadata(path):
    import sys

    if sys.version_info < (3, 11):
        import tomli as tomllib
    else:
        import tomllib
    if path and path.is_file():
        with path.open("rb") as handle:
            return tomllib.load(handle).get("metadata", {})
    return {}


def attachment_inputs(script):
    if not script:
        return []
    for node in ast.parse(script.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "INPUT_PATHS"
        ):
            return ast.literal_eval(node.value)
    return []


def source_context(study, row, cfg, lock, result):
    protocol = row["protocol_id"]
    task = lock.get("task") or cfg.get("task") or {}
    recorded = {
        "task_digest": task.get("digest"),
        "task_checksum": result.get("task_checksum"),
    }
    context = {
        "protocol_id": protocol,
        **recorded,
        "status": "unresolved",
        "documents": [],
    }
    attachments = []
    task_path = Path(str(task.get("path") or ""))
    if task_path.is_absolute() and task_path.is_relative_to(study.repo):
        task_path = task_path.relative_to(study.repo)
    candidates = [study.repo / task_path]
    if not task_path.is_absolute():
        candidates += [
            r / "archived" / task_path
            for r in sorted((study.repo / ".release-archive").glob("*"))
        ]
    directory = next(
        (
            p
            for p in candidates
            if p.name == protocol
            and p.resolve() == p
            and (
                p.is_relative_to(study.repo)
                or p.is_relative_to(study.data / "libgen_capability_improvement")
                and p.parent.name == "tasks"
            )
            and within(p, "task.toml")
        ),
        None,
    )
    if not directory:
        return context, attachments
    checksum, lock_digest = task_identity(directory)
    matched = bool(
        checksum
        and checksum == recorded["task_checksum"]
        or lock_digest
        and lock_digest == recorded["task_digest"]
    )
    metadata = toml_metadata(directory / "task.toml")
    if metadata.get("protocol_id", protocol) != protocol:
        return context, attachments
    context.update(
        candidate_task_checksum=checksum,
        task_match=matched,
        input_revision=metadata.get("input_revision"),
        input_repo=metadata.get("input_repo"),
        groundtruth_revision=metadata.get("groundtruth_revision"),
        verifier_version=metadata.get("benchmark_version"),
        groundtruth_repo=metadata.get("groundtruth_repo"),
    )
    for name in (
        "task.toml",
        "instruction.md",
        "environment/rules.md",
        "environment/protocol_processing_rules.md",
        "environment/input_manifest.json",
        "environment/fetch_input.py",
    ):
        path = within(directory, name)
        if path:
            attachments.append(("context/task/" + name, path, "configuration", None))
    manifest_path = within(directory, "environment/input_manifest.json") or within(
        directory, "input_manifest.json"
    )
    manifest = read_json(manifest_path) if manifest_path else {}
    if manifest.get("protocol_id", protocol) != protocol:
        raise ValueError("source manifest protocol mismatch")
    entries = manifest.get("sources") or [
        {"path": rel}
        for rel in attachment_inputs(within(directory, "environment/fetch_input.py"))
    ]
    for entry in entries:
        rel = entry["path"]
        source = study.sources[protocol].get(rel, {})
        if key_path(rel).parts[0] != protocol or not source:
            context["documents"].append(
                {"path": rel, "available": False, "sha256": entry.get("sha256")}
            )
            continue
        expected = entry.get("sha256") or source.get("sha256")
        candidates = [
            *(within(root, rel) for root in scg_source_roots(study.data)),
            within(
                directory / "environment/protocol_sources",
                entry.get("local_path") or Path(rel).name,
            ),
        ]
        source_file = next(
            (f for f in candidates if expected and f and sha256(f) == expected), None
        )
        logical = "protocols/" + key_path(rel).as_posix()
        available = source_file is not None
        context["documents"].append(
            {
                "path": rel,
                "sha256": expected,
                "available": available,
                "rights": source.get("rights_class"),
                "url": source.get("recorded_source_urls") or None,
                "version": source.get("document_version") or None,
            }
        )
        if available:
            attachments.append((logical, source_file, "source", expected))
    if entries:
        context["status"] = (
            "verified"
            if matched
            and all(entry.get("sha256") for entry in entries)
            and all(d["available"] for d in context["documents"])
            else "candidate"
        )
    elif "NameOnly" in str(row["executor"]) or "name_only" in task_path.parts:
        context["status"] = "name-only" if matched else "name-only-candidate"
    return context, attachments


def execution_inputs(study, cfg, lock):
    """Collect named read-only packs and locked instructions, never arbitrary host mounts."""
    allowed = (
        study.runs / "libgen",
        study.repo / "improvement",
        study.data / "libgen_capability_improvement",
        study.data / "external_knowledge",
    )
    attachments = {}

    def allowed_relative(path):
        if path.resolve() != path:
            return None
        for root in allowed:
            if path.is_relative_to(root) and path != root:
                return path.relative_to(root)
        return None

    for item in lock.get("extra_instructions") or []:
        path = Path(str(item.get("path") or ""))
        rel = allowed_relative(path)
        expected = str(item.get("digest") or "").removeprefix("sha256:")
        if rel and path.is_file() and sha256(path) == expected:
            attachments["instructions/" + str(rel)] = (path, "configuration", expected)
    mounts = (cfg.get("environment") or {}).get("mounts") or []
    for mount in mounts:
        if not mount.get("read_only") or mount.get("target") not in {
            "/workspace/knowledge_base",
            "/workspace/capability_pack",
            "/workspace/capability_memory",
            "/workspace/external_knowledge",
        }:
            continue
        path = Path(str(mount.get("source") or ""))
        rel = allowed_relative(path)
        if rel and path.is_dir() and len(rel.parts) >= 2:
            for f in files(path):
                attachments["execution-inputs/" + str(rel / f.relative_to(path))] = (
                    f,
                    "checkpoint",
                    None,
                )
    return [(k, *v) for k, v in attachments.items()]


def checkpoint_context(study, row, job):
    """Collect only linked experiment assets. Private audit and review trees are out of scope."""
    if not row["path"].startswith("libgen/"):
        return [], []
    branch = row["path"].split("/")[1]
    base = study.runs / "libgen" / branch
    private = study.data / "libgen_capability_improvement" / branch / "experiment"
    references, attachments = [], {}
    if not hasattr(study, "_receipts"):
        study._receipts = {}
    if branch not in study._receipts:
        receipts = {}
        for path in (base / "receipts").rglob("*.json"):
            receipt = read_json(path)
            if receipt.get("result_root"):
                receipts.setdefault(Path(receipt["result_root"]), []).append(
                    (path, receipt)
                )
        study._receipts[branch] = receipts
    for path, receipt in study._receipts[branch].get(job, []):
        attachments["learning/" + str(path.relative_to(base))] = (
            path,
            "checkpoint",
            None,
        )
        plan_path = Path(receipt.get("plan_path") or "")
        if (
            not plan_path.is_relative_to(base)
            or plan_path.resolve() != plan_path
            or not plan_path.is_file()
        ):
            continue
        if sha256(plan_path) != receipt.get("plan_sha256"):
            references.append({"status": "plan-hash-mismatch"})
            continue
        plan = read_json(plan_path)
        attachments["learning/" + str(plan_path.relative_to(base))] = (
            plan_path,
            "checkpoint",
            receipt["plan_sha256"],
        )
        for label in dict.fromkeys(
            plan.get(k)
            for k in (
                "checkpoint",
                "parent_checkpoint",
                "next_checkpoint",
                "pact_checkpoint",
                "kb_checkpoint",
            )
        ):
            if not isinstance(label, str) or not re.fullmatch(r"C\d+", label):
                continue
            cp = within(private, "checkpoints/" + label + "/checkpoint.json")
            ref = {
                "checkpoint": label,
                "plan_digest": plan.get("plan_digest"),
                "status": "missing",
            }
            references.append(ref)
            if not cp:
                continue
            desc = read_json(cp)
            if (
                plan.get("checkpoint") == label
                and plan.get("checkpoint_digest")
                and desc.get("checkpoint_digest") != plan["checkpoint_digest"]
            ):
                ref["status"] = "checkpoint-hash-mismatch"
                continue
            ref.update(
                status="recorded",
                sha256=sha256(cp),
                checkpoint_digest=desc.get("checkpoint_digest"),
            )
            prefix = "learning/checkpoints/" + label + "/"
            attachments[prefix + "checkpoint.json"] = (cp, "checkpoint", ref["sha256"])
            linked = []
            for k in ("microstate", "catalog", "pact"):
                record = desc.get(k) or {}
                rel = record.get("path", "")
                if not rel or key_path(rel).parts[0] not in {
                    "pacts",
                    "microstates",
                    "knowledge",
                }:
                    continue
                dest = private / rel
                if dest.resolve() != dest:
                    continue
                if dest.is_dir() and k == "pact":
                    linked.extend((f, None) for f in files(dest))
                elif dest.is_file() and sha256(dest) == record.get("sha256"):
                    linked.append((dest, record["sha256"]))
                    if k == "catalog":
                        for card in read_json(dest).get("entries", []):
                            for field in ("graph_card", "oligo_card"):
                                item = card.get(field) or {}
                                loc = item.get("path", "")
                                if loc.startswith("knowledge/cards/"):
                                    f = within(private, loc)
                                    if f:
                                        linked.append((f, item.get("sha256")))
            for f, expected in linked:
                attachments[prefix + str(f.relative_to(private))] = (
                    f,
                    "checkpoint",
                    expected,
                )
    return references, [(k, *v) for k, v in attachments.items()]
