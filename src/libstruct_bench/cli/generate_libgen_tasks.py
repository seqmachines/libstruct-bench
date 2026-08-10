from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from libstruct_bench.libgen.validation import validate_groundtruth_bundle


DEFAULT_BENCHMARK_DIR = Path("benchmarks/libgen")
GROUNDTRUTH_FILENAMES = (
    "groundtruth_final_lib_struct.json",
    "groundtruth_oligos.json",
    "groundtruth_library_generation_workflow.json",
)
AGENT_ALLOWED_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
    "api.moonshot.ai",
    "api.moonshot.cn",
    "api.kimi.com",
)
VERIFIER_ALLOWED_HOSTS = (
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cas-bridge.xethub.hf.co",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate linked T2/T3 Harbor tasks from pinned HF repositories."
    )
    parser.add_argument("--protocols", default=str(DEFAULT_BENCHMARK_DIR / "protocols.json"))
    parser.add_argument("--out", default=str(DEFAULT_BENCHMARK_DIR / "tasks"))
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--input-repo", required=True)
    parser.add_argument("--input-revision", required=True)
    parser.add_argument("--groundtruth-repo", required=True)
    parser.add_argument("--groundtruth-revision", required=True)
    parser.add_argument("--protocol-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    _require_pinned_revision(args.input_revision, "input revision")
    _require_pinned_revision(args.groundtruth_revision, "ground-truth revision")
    if args.input_repo == args.groundtruth_repo:
        raise ValueError("agent inputs and private ground truth must use different HF repositories")

    config_path = Path(args.protocols)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    protocols = _selected_protocols(config, args.protocol_id)
    source_root = Path(args.source_root).resolve()
    groundtruth_root = Path(args.groundtruth_root).resolve()
    schema_root = _repo_root() / "schemas"
    groundtruth_hashes = _validate_local_release(
        protocols,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        schema_root=schema_root,
    )

    output_root = Path(args.out)
    package_root = _repo_root() / "src" / "libstruct_bench"
    rules_path = config_path.parent / "rules.md"
    for protocol in protocols:
        _write_task(
            protocol,
            output_root=output_root,
            package_root=package_root,
            schema_root=schema_root,
            rules_path=rules_path,
            input_repo=args.input_repo,
            input_revision=args.input_revision,
            groundtruth_repo=args.groundtruth_repo,
            groundtruth_revision=args.groundtruth_revision,
            groundtruth_hashes=groundtruth_hashes[_required_string(protocol, "protocol_id")],
            force=args.force,
        )
    return 0


def _selected_protocols(config: dict[str, Any], requested: list[str]) -> list[dict[str, Any]]:
    protocols = config.get("protocols")
    if not isinstance(protocols, list) or not protocols:
        raise ValueError("protocols.json must contain a non-empty protocols array")
    by_id = {_required_string(item, "protocol_id"): item for item in protocols}
    if len(by_id) != len(protocols):
        raise ValueError("protocol IDs must be unique")
    if not requested:
        return protocols
    missing = set(requested) - set(by_id)
    if missing:
        raise ValueError("unknown protocol IDs: " + ", ".join(sorted(missing)))
    return [by_id[item] for item in requested]


def _validate_local_release(
    protocols: list[dict[str, Any]],
    *,
    source_root: Path,
    groundtruth_root: Path,
    schema_root: Path,
) -> dict[str, dict[str, str]]:
    forbidden = sorted(
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*")
        if path.is_file()
        and (
            path.name.startswith("groundtruth")
            or path.suffix.lower() in {".html", ".htm"}
            or path.name == "groundtruth_oligos.tsv"
        )
    )
    if forbidden:
        preview = ", ".join(forbidden[:5])
        raise ValueError(
            "agent-visible source root contains ground truth or legacy curation; "
            f"prepare a split export first ({preview})"
        )

    groundtruth_hashes: dict[str, dict[str, str]] = {}
    for protocol in protocols:
        protocol_id = _required_string(protocol, "protocol_id")
        sources = protocol.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{protocol_id} has no approved primary sources")
        for source in sources:
            relative = _safe_relative_path(_required_string(source, "path"))
            expected_hash = _required_sha256(source, "sha256")
            path = source_root / relative
            if not path.is_file():
                raise FileNotFoundError(f"{protocol_id} is missing approved source {relative}")
            actual_hash = _sha256(path)
            if actual_hash != expected_hash:
                raise ValueError(
                    f"{protocol_id} source hash mismatch for {relative}: "
                    f"expected {expected_hash}, found {actual_hash}"
                )

        truth_dir = groundtruth_root / protocol_id
        groundtruth_hashes[protocol_id] = {
            filename: _sha256(truth_dir / filename)
            for filename in GROUNDTRUTH_FILENAMES
        }
        documents = {
            task: json.loads((truth_dir / filename).read_text(encoding="utf-8"))
            for task, filename in zip(("T1", "T2", "T3"), GROUNDTRUTH_FILENAMES, strict=True)
        }
        validate_groundtruth_bundle(
            documents,
            protocol_id=protocol_id,
            schema_root=schema_root,
        )
    return groundtruth_hashes


def _write_task(
    protocol: dict[str, Any],
    *,
    output_root: Path,
    package_root: Path,
    schema_root: Path,
    rules_path: Path,
    input_repo: str,
    input_revision: str,
    groundtruth_repo: str,
    groundtruth_revision: str,
    groundtruth_hashes: dict[str, str],
    force: bool,
) -> None:
    protocol_id = _required_string(protocol, "protocol_id")
    task_dir = output_root / protocol_id
    if task_dir.exists():
        if not force:
            raise FileExistsError(f"{task_dir} exists; pass --force to replace generated files")
        shutil.rmtree(task_dir)
    environment_dir = task_dir / "environment"
    tests_dir = task_dir / "tests"
    environment_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    sources = _task_sources(protocol)
    source_manifest = {
        "protocol_id": protocol_id,
        "sources": sources,
    }
    source_manifest_hash = hashlib.sha256(
        json.dumps(source_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    groundtruth_bundle_hash = hashlib.sha256(
        json.dumps(groundtruth_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    display_name = protocol.get("display_name", protocol_id)

    (task_dir / "instruction.md").write_text(
        _instruction(protocol_id, display_name, sources), encoding="utf-8"
    )
    (task_dir / "task.toml").write_text(
        _task_toml(
            protocol_id=protocol_id,
            display_name=display_name,
            input_repo=input_repo,
            input_revision=input_revision,
            groundtruth_repo=groundtruth_repo,
            groundtruth_revision=groundtruth_revision,
            source_manifest_hash=source_manifest_hash,
            groundtruth_bundle_hash=groundtruth_bundle_hash,
        ),
        encoding="utf-8",
    )
    (task_dir / "rules.md").write_text(rules_path.read_text(encoding="utf-8"), encoding="utf-8")
    (task_dir / "input_manifest.json").write_text(
        json.dumps(source_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (environment_dir / "fetch_input.py").write_text(
        _fetch_input_py(input_repo, input_revision, sources), encoding="utf-8"
    )
    (environment_dir / "Dockerfile").write_text(_environment_dockerfile(), encoding="utf-8")
    shutil.copy2(task_dir / "rules.md", environment_dir / "rules.md")
    shutil.copy2(task_dir / "input_manifest.json", environment_dir / "input_manifest.json")
    shutil.copytree(package_root, environment_dir / "libstruct_bench", ignore=_ignore_python_cache)
    shutil.copytree(schema_root, environment_dir / "schemas")

    (tests_dir / "Dockerfile").write_text(_tests_dockerfile(), encoding="utf-8")
    (tests_dir / "test.sh").write_text(
        _test_sh(
            protocol_id,
            groundtruth_repo,
            groundtruth_revision,
            groundtruth_hashes,
        ),
        encoding="utf-8",
    )
    (tests_dir / "grade.py").write_text(_grade_py(), encoding="utf-8")
    shutil.copytree(package_root, tests_dir / "libstruct_bench", ignore=_ignore_python_cache)
    shutil.copytree(schema_root, tests_dir / "schemas")


def _task_sources(protocol: dict[str, Any]) -> list[dict[str, str]]:
    protocol_id = _required_string(protocol, "protocol_id")
    result: list[dict[str, str]] = []
    for source in protocol["sources"]:
        path = _safe_relative_path(_required_string(source, "path"))
        if path.parts[0] != protocol_id:
            raise ValueError(f"{protocol_id} source path must start with {protocol_id}/: {path}")
        local_path = PurePosixPath(*path.parts[1:]).as_posix()
        result.append(
            {
                "path": path.as_posix(),
                "local_path": local_path,
                "sha256": _required_sha256(source, "sha256"),
            }
        )
    return result


def _instruction(protocol_id: str, display_name: str, sources: list[dict[str, str]]) -> str:
    listing = "\n".join(
        f"- `/workspace/input/{item['local_path']}` (`sha256:{item['sha256']}`)"
        for item in sources
    )
    return f"""# T2/T3 protocol understanding: {display_name}

Read `/workspace/rules.md` completely before starting. The approved source
bundle is already downloaded and hash-checked. Inspect every file:

{listing}

Produce a linked T2 oligo catalog and T3 molecular state-transition graph for
protocol `{protocol_id}`. Use local IDs consistently; IDs themselves are not
scored. Write the two required files under `/logs/artifacts/` and run the local
validator shown in the rules. Do not search for or reconstruct benchmark ground
truth, legacy curation, audit records, or prior answers.
"""


def _task_toml(
    *,
    protocol_id: str,
    display_name: str,
    input_repo: str,
    input_revision: str,
    groundtruth_repo: str,
    groundtruth_revision: str,
    source_manifest_hash: str,
    groundtruth_bundle_hash: str,
) -> str:
    return f"""schema_version = "1.3"
artifacts = ["/logs/artifacts/t2_prediction.json", "/logs/artifacts/t3_prediction.json"]

[task]
name = "sequencing/libgen-{protocol_id}"
description = {json.dumps(f"Reconstruct linked oligos and molecular library generation for {display_name}.")}
authors = [{{ name = "Seq Machines" }}]
keywords = ["genomics", "oligo", "molecular-workflow", "protocol-understanding"]

[metadata]
benchmark = "libgen"
protocol_id = {json.dumps(protocol_id)}
protocol_name = {json.dumps(display_name)}
input_repo = {json.dumps(input_repo)}
input_revision = {json.dumps(input_revision)}
groundtruth_repo = {json.dumps(groundtruth_repo)}
groundtruth_revision = {json.dumps(groundtruth_revision)}
source_manifest_sha256 = {json.dumps(source_manifest_hash)}
groundtruth_bundle_sha256 = {json.dumps(groundtruth_bundle_hash)}

[agent]
timeout_sec = 1800.0
network_mode = "allowlist"
allowed_hosts = {json.dumps(list(AGENT_ALLOWED_HOSTS))}

[agent.env]
LIBGEN_PROTOCOL_ID = {json.dumps(protocol_id)}

[verifier]
timeout_sec = 600.0
environment_mode = "separate"
network_mode = "allowlist"
allowed_hosts = {json.dumps(list(VERIFIER_ALLOWED_HOSTS))}

[environment]
network_mode = "public"
build_timeout_sec = 1800.0
cpus = 4
memory_mb = 8192
storage_mb = 16384

[verifier.environment]
network_mode = "public"
cpus = 1
memory_mb = 2048
storage_mb = 4096

[verifier.environment.env]
HF_TOKEN = "${{HF_TOKEN}}"
"""


def _fetch_input_py(repo_id: str, revision: str, sources: list[dict[str, str]]) -> str:
    return f'''from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ID = {repo_id!r}
REVISION = {revision!r}
SOURCES = {sources!r}


def main() -> int:
    root = Path("/workspace/input")
    for source in SOURCES:
        output = root / source["local_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(_url(source["path"]), headers={{"User-Agent": "libstruct-bench"}})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != source["sha256"]:
            raise RuntimeError(
                f"source hash mismatch for {{source['path']}}: expected {{source['sha256']}}, found {{digest}}"
            )
        output.write_bytes(data)
        print(output)
    return 0


def _url(path: str) -> str:
    repo = urllib.parse.quote(REPO_ID.strip().removeprefix("datasets/"), safe="/")
    rev = urllib.parse.quote(REVISION, safe="")
    item = urllib.parse.quote(path.lstrip("/"), safe="/")
    return f"https://huggingface.co/datasets/{{repo}}/resolve/{{rev}}/{{item}}"


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _environment_dockerfile() -> str:
    return """FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update \\
    && apt-get install -y --no-install-recommends antiword file ripgrep unzip \\
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \\
    && python -m pip install --no-cache-dir jsonschema pymupdf pypdf docling openpyxl pillow
COPY fetch_input.py /workspace/fetch_input.py
RUN python /workspace/fetch_input.py
COPY rules.md /workspace/rules.md
COPY input_manifest.json /workspace/input_manifest.json
COPY libstruct_bench /workspace/libstruct_bench
COPY schemas /workspace/schemas
RUN mkdir -p /logs/artifacts
"""


def _tests_dockerfile() -> str:
    return """FROM python:3.12-slim
WORKDIR /tests
RUN python -m pip install --no-cache-dir jsonschema
COPY . /tests
RUN chmod +x /tests/test.sh
"""


def _test_sh(
    protocol_id: str,
    groundtruth_repo: str,
    revision: str,
    groundtruth_hashes: dict[str, str],
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \\
  --t2-prediction /logs/artifacts/t2_prediction.json \\
  --t3-prediction /logs/artifacts/t3_prediction.json \\
  --protocol-id {json.dumps(protocol_id)} \\
  --groundtruth-repo {json.dumps(groundtruth_repo)} \\
  --groundtruth-revision {json.dumps(revision)} \\
  --groundtruth-prefix {json.dumps(protocol_id)} \\
  --t1-sha256 {json.dumps(groundtruth_hashes['groundtruth_final_lib_struct.json'])} \\
  --t2-sha256 {json.dumps(groundtruth_hashes['groundtruth_oligos.json'])} \\
  --t3-sha256 {json.dumps(groundtruth_hashes['groundtruth_library_generation_workflow.json'])} \\
  --schema-root /tests/schemas \\
  --reward-out /logs/verifier/reward.json \\
  --details-out /logs/verifier/details.json \\
  --error-out /logs/verifier/error.json
"""


def _grade_py() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import sys

sys.path.insert(0, "/tests")

from libstruct_bench.cli.grade_libgen import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


def _required_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing non-empty string {key!r}")
    return value.strip()


def _required_sha256(item: dict[str, Any], key: str) -> str:
    value = _required_string(item, key).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{key} must be a complete SHA-256 digest")
    return value


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def _require_pinned_revision(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise ValueError(f"{label} must be a full immutable commit hash, not a branch such as main")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignore_python_cache(_path: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


if __name__ == "__main__":
    raise SystemExit(main())
