from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from libstruct_bench.library_structure import parse_library_entries
from libstruct_bench.library_structure_inputs import (
    input_paths as _input_paths,
    input_policy_instruction,
    validate_input_policy,
)

from libstruct_bench.library_structure_policy import (
    BIOLOGICAL_PAYLOAD_POLICY,
    HARBOR_SOURCE_POLICY,
    LIBRARY_DERIVATION_POLICY,
    LIBRARY_DERIVATION_SELF_CHECK_RULE,
    LIBRARY_ENTRY_POLICY,
    PLACEHOLDER_POLICY,
)


DEFAULT_BENCHMARK_DIR = Path("benchmarks/task1")
SHARED_RULES_RELATIVE_PATH = Path("benchmarks/shared/protocol_processing_rules.md")
SHARED_RULES_SECTION = """## Shared Protocol Processing Rules

Read `protocol_processing_rules.md` before working. It defines source-grounding, oligo-extraction, normalization, and library-construction rules shared by protocol-processing tasks. The task-specific instructions below define the required output schema and which section or sections to complete.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Harbor tasks for v0 library-structure scoring.")
    parser.add_argument("--protocols", default=str(DEFAULT_BENCHMARK_DIR / "protocols.json"))
    parser.add_argument("--out", default=str(DEFAULT_BENCHMARK_DIR / "tasks"))
    parser.add_argument(
        "--groundtruth-dir",
        help="Use frozen <protocol_id>.json labels in the private verifier instead of downloading labels.",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing generated task directories")
    args = parser.parse_args(argv)

    config_path = Path(args.protocols)
    if not config_path.exists() and config_path.name == "protocols.json":
        config_path = config_path.with_name("protocols.example.json")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_input_policy(config)
    output_dir = Path(args.out)
    package_src = Path(__file__).resolve().parents[1]
    shared_rules_path = _shared_rules_path()
    input_repo = _required(config, "input_repo")
    groundtruth_repo = _required(config, "groundtruth_repo")
    input_revision = _required(config, "input_revision")
    groundtruth_revision = _required(config, "groundtruth_revision")
    frozen_labels: dict[str, bytes] = {}
    if args.groundtruth_dir:
        for protocol in config.get("protocols", []):
            protocol_id = _required(protocol, "protocol_id")
            raw = (Path(args.groundtruth_dir) / f"{protocol_id}.json").read_bytes()
            parse_library_entries(json.loads(raw), expected_protocol_id=protocol_id)
            frozen_labels[protocol_id] = raw

    for protocol in config.get("protocols", []):
        _write_task(
            protocol=protocol,
            output_dir=output_dir,
            package_src=package_src,
            shared_rules_path=shared_rules_path,
            input_repo=input_repo,
            groundtruth_repo=groundtruth_repo,
            input_revision=input_revision,
            groundtruth_revision=groundtruth_revision,
            force=args.force,
            input_policy=config.get("input_policy"),
            groundtruth_data=frozen_labels.get(protocol["protocol_id"]),
        )

    return 0


def _write_task(
    *,
    protocol: dict[str, Any],
    output_dir: Path,
    package_src: Path,
    shared_rules_path: Path,
    input_repo: str,
    groundtruth_repo: str,
    input_revision: str,
    groundtruth_revision: str,
    force: bool,
    input_policy: str | None = None,
    groundtruth_data: bytes | None = None,
) -> None:
    protocol_id = _required(protocol, "protocol_id")
    task_dir = output_dir / protocol_id
    if task_dir.exists():
        if not force:
            raise FileExistsError(f"{task_dir} already exists; pass --force to overwrite")
        shutil.rmtree(task_dir)

    tests_dir = task_dir / "tests"
    environment_dir = task_dir / "environment"
    tests_dir.mkdir(parents=True)
    environment_dir.mkdir(parents=True)
    shutil.copyfile(shared_rules_path, task_dir / "protocol_processing_rules.md")
    shutil.copyfile(shared_rules_path, environment_dir / "protocol_processing_rules.md")

    display_name = protocol.get("display_name", protocol_id)
    input_paths = _input_paths(protocol)
    groundtruth_path = _required(protocol, "groundtruth_path")
    protocol_input_revision = protocol.get("input_revision", protocol.get("revision", input_revision))
    protocol_groundtruth_revision = protocol.get(
        "groundtruth_revision",
        protocol.get("revision", groundtruth_revision),
    )
    if groundtruth_data is not None:
        protocol_groundtruth_revision = "sha256:" + hashlib.sha256(groundtruth_data).hexdigest()
        (tests_dir / "groundtruth.json").write_bytes(groundtruth_data)

    (task_dir / "instruction.md").write_text(
        _instruction(
            protocol_id=protocol_id,
            display_name=display_name,
            input_kind=protocol.get("input_kind", "protocol"),
            input_paths=input_paths,
            input_policy=input_policy,
        ),
        encoding="utf-8",
    )
    (task_dir / "task.toml").write_text(
        _task_toml(
            protocol_id=protocol_id,
            display_name=display_name,
            input_repo=input_repo,
            groundtruth_repo=groundtruth_repo,
            input_revision=protocol_input_revision,
            groundtruth_revision=protocol_groundtruth_revision,
            local_groundtruth=groundtruth_data is not None,
        ),
        encoding="utf-8",
    )
    (environment_dir / "fetch_input.py").write_text(
        _fetch_input_py(
            protocol_id=protocol_id,
            repo_id=input_repo,
            input_paths=input_paths,
            revision=protocol_input_revision,
        ),
        encoding="utf-8",
    )
    (environment_dir / "Dockerfile").write_text(_environment_dockerfile(), encoding="utf-8")
    (tests_dir / "Dockerfile").write_text(_tests_dockerfile(), encoding="utf-8")
    (tests_dir / "test.sh").write_text(
        _test_sh(
            protocol_id=protocol_id,
            groundtruth_repo=groundtruth_repo,
            groundtruth_path=groundtruth_path,
            revision=protocol_groundtruth_revision,
            local_groundtruth=groundtruth_data is not None,
        ),
        encoding="utf-8",
    )
    (tests_dir / "grade.py").write_text(_grade_py(), encoding="utf-8")
    shutil.copytree(
        package_src,
        tests_dir / "libstruct_bench",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _required(protocol: dict[str, Any], key: str) -> str:
    value = protocol.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"protocol entry is missing required string field {key!r}")
    return value


def _shared_rules_path() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    candidates = (
        repo_root / SHARED_RULES_RELATIVE_PATH,
        Path.cwd() / SHARED_RULES_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"missing shared rules file: {SHARED_RULES_RELATIVE_PATH}")


def _instruction(
    *,
    protocol_id: str,
    display_name: str,
    input_kind: str,
    input_paths: list[str],
    input_policy: str | None = None,
) -> str:
    input_list = "\n".join(f"- `input/{Path(path).name}`" for path in input_paths)
    spreadsheet_instruction = ""
    if any(Path(path).suffix.lower() == ".xlsx" for path in input_paths):
        spreadsheet_instruction = """- For spreadsheet parsing, use `openpyxl` for `.xlsx` files. If a workbook is
  malformed or `openpyxl` cannot read it, then fall back to inspecting the
  zipped XML parts directly."""
    return f"""# Library Structure: {display_name}

{SHARED_RULES_SECTION}

Extract the final sequencing library structure from the provided {input_kind} files.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

{input_list}

{input_policy_instruction(input_policy)}

Create `/logs/artifacts/prediction.json` using this JSON shape. The verifier reads only that file.
JSON returned only in your final chat response is ignored and will score as
missing output. The values in the block below are dummy schema examples only. Do
not copy the example `library_id`, `modality`,
`library_sequence`, `annotated_library_sequence`, bases, placeholder lengths, or
segment order unless the downloaded source files support them.

```json
{{
  "schema_version": "libstruct.library_structure.v0",
  "protocol_id": "{protocol_id}",
  "libraries": [
    {{
      "library_id": "example_library",
      "modality": "example",
      "library_sequence": "ACGT####~~~~TGCA",
      "annotated_library_sequence": "ACGT[CELL_BARCODE:4][UMI:4]TGCA"
    }}
  ]
}}
```

{LIBRARY_DERIVATION_POLICY}

Rules:
{HARBOR_SOURCE_POLICY}
- The schema block above is only a JSON shape example, not a partial answer and
  not source evidence. If any source-derived sequence conflicts with the
  example, the source wins.
{LIBRARY_ENTRY_POLICY}
- For PDF parsing, use Docling with OCR disabled when available. PyMuPDF
  (`import fitz`) and `pypdf` layout extraction are acceptable stable
  alternatives and cross-checks. Do not run OCR or use OCR-derived text because
  OCR can introduce unstable sequence conversions. Combine native text,
  layout-sorted text blocks, table cells, appendix text, and stable
  vector/text-layer labels from diagrams. Render pages only for visual layout
  checks, not OCR conversion.
{spreadsheet_instruction}
{BIOLOGICAL_PAYLOAD_POLICY}
{PLACEHOLDER_POLICY}
- The file `/logs/artifacts/prediction.json` must contain only JSON. Do not put
  Markdown, comments, or explanations in that file.
- Before finishing, run `test -s /logs/artifacts/prediction.json` and
  `python -m json.tool /logs/artifacts/prediction.json` to confirm the artifact
  exists and is valid JSON.
{LIBRARY_DERIVATION_SELF_CHECK_RULE}
- Also inspect the JSON after validation and make sure no
  `libraries[].library_sequence` value is empty.
"""


def _task_toml(
    *,
    protocol_id: str,
    display_name: str,
    input_repo: str,
    groundtruth_repo: str,
    input_revision: str,
    groundtruth_revision: str,
    local_groundtruth: bool = False,
) -> str:
    verifier_env = "" if local_groundtruth else '''[verifier.environment.env]
HF_TOKEN = "${HF_TOKEN}"
'''
    return f"""schema_version = "1.3"

[task]
name = "sequencing/library-structure-{protocol_id}"
description = {json.dumps(f"Extract the final library structure sequence for {display_name}.")}
authors = [{{ name = "Seq Machines" }}]
keywords = ["genomics", "library-structure", "sequence-similarity", "benchmark"]

[metadata]
benchmark = "library_structure"
protocol_id = {json.dumps(protocol_id)}
protocol_name = {json.dumps(display_name)}
input_repo = {json.dumps(input_repo)}
groundtruth_repo = {json.dumps(groundtruth_repo)}
input_revision = {json.dumps(input_revision)}
groundtruth_revision = {json.dumps(groundtruth_revision)}

[agent]
timeout_sec = 1800.0

[verifier]
timeout_sec = 300.0
environment_mode = "separate"

[environment]
network_mode = "public"
cpus = 1
memory_mb = 2048
storage_mb = 4096

[verifier.environment]
network_mode = "public"
cpus = 1
memory_mb = 2048
storage_mb = 4096

{verifier_env}
"""


def _fetch_input_py(*, protocol_id: str, repo_id: str, input_paths: list[str], revision: str) -> str:
    return f'''from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL_ID = {protocol_id!r}
REPO_ID = {repo_id!r}
INPUT_PATHS = {input_paths!r}
REVISION = {revision!r}


def main() -> int:
    output_dir = Path("input")
    output_dir.mkdir(exist_ok=True)
    for input_path in INPUT_PATHS:
        url = _dataset_url(REPO_ID, REVISION, input_path)
        output_path = output_dir / Path(input_path).name
        with urllib.request.urlopen(url, timeout=120) as response:
            output_path.write_bytes(response.read())
        print(output_path)
    return 0


def _dataset_url(repo_id: str, revision: str, path: str) -> str:
    return (
        "https://huggingface.co/datasets/"
        + urllib.parse.quote(repo_id.strip().removeprefix("datasets/"), safe="/")
        + "/resolve/"
        + urllib.parse.quote(revision or "main", safe="")
        + "/"
        + urllib.parse.quote(path.lstrip("/"), safe="/")
    )


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _environment_dockerfile() -> str:
    return """FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update \\
    && apt-get install -y --no-install-recommends file \\
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \\
    && python -m pip install --no-cache-dir pymupdf pypdf docling openpyxl pillow
COPY fetch_input.py /workspace/fetch_input.py
COPY protocol_processing_rules.md /workspace/protocol_processing_rules.md
"""


def _tests_dockerfile() -> str:
    return """FROM python:3.12-slim
WORKDIR /tests
COPY . /tests
RUN chmod +x /tests/test.sh
"""


def _test_sh(
    *, protocol_id: str, groundtruth_repo: str, groundtruth_path: str, revision: str,
    local_groundtruth: bool = False,
) -> str:
    groundtruth_args = (
        "--groundtruth /tests/groundtruth.json"
        if local_groundtruth else
        f"--groundtruth-repo {json.dumps(groundtruth_repo)} "
        f"--groundtruth-path {json.dumps(groundtruth_path)} --revision {json.dumps(revision)}"
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
python /tests/grade.py \\
  --prediction /logs/artifacts/prediction.json \\
  --protocol-id {json.dumps(protocol_id)} \\
  {groundtruth_args} \\
  --reward-out /logs/verifier/reward.json \\
  --audit-out /logs/verifier/audit.json
"""


def _grade_py() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import sys

sys.path.insert(0, "/tests")

from libstruct_bench.cli.grade_library_v0 import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


if __name__ == "__main__":
    raise SystemExit(main())
