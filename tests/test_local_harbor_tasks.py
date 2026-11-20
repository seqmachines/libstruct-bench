"""Prepare both public task layouts and exercise the generated verifier entry points."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from libstruct_bench.cli.generate_libgen_tasks import main as build_workflow
from libstruct_bench.cli.generate_library_v0_tasks import main as build_final_library
from tests.libgen_fixtures import t1_groundtruth, t2_prediction, t3_prediction
from tests.test_generate_libgen_tasks import _fixture_release


@pytest.mark.parametrize("layout", ["task1", "tasks2_3"])
def test_scored_local_harbor_task(tmp_path, layout):
    linked_inventory, sources, truth = _fixture_release(tmp_path)
    protocol = "example_protocol"
    out = tmp_path / "prepared"
    args = [
        "--protocol-id",
        protocol,
        "--source-root",
        str(sources),
        "--groundtruth-root",
        str(truth),
        "--out",
        str(out),
    ]
    if layout == "task1":
        inventory = tmp_path / "final-library.json"
        inventory.write_text(
            json.dumps(
                {
                    "protocols": [
                        {
                            "protocol_id": protocol,
                            "input_paths": [f"{protocol}/source.pdf"],
                        },
                        {
                            "protocol_id": "unselected",
                            "input_path": "unselected/manual.pdf",
                        },
                    ]
                }
            )
        )
        build = build_final_library
        predictions = {
            "prediction.json": {
                **t1_groundtruth(),
                "schema_version": "libstruct.library_structure.v0",
            }
        }
    else:
        inventory = linked_inventory
        build = build_workflow
        predictions = {
            "t2_prediction.json": t2_prediction(),
            "t3_prediction.json": t3_prediction(),
        }
    args += ["--protocols", str(inventory)]
    assert build(args) == 0

    task = out / protocol
    env = task / "environment"
    verifier = task / "tests"
    bundle = json.loads((env / "input_manifest.json").read_text())
    (document,) = bundle["sources"]
    assert (
        hashlib.sha256(
            (env / "protocol_sources" / document["local_path"]).read_bytes()
        ).hexdigest()
        == document["sha256"]
    )
    assert list(out.iterdir()) == [task]
    assert not list(env.rglob("groundtruth*.json"))
    assert not (verifier / "libstruct_bench/archive").exists()
    assert not (verifier / "libstruct_bench/improvement").exists()
    assert "HF_TOKEN" not in (task / "task.toml").read_text()
    if layout == "task1":
        assert "fetch_input.py" not in (task / "instruction.md").read_text()

    logs = tmp_path / "logs"
    artifacts = logs / "artifacts"
    artifacts.mkdir(parents=True)
    for filename, doc in predictions.items():
        (artifacts / filename).write_text(json.dumps(doc))
    # Run the same shell entry point with its container mount paths mapped to this fixture.
    script = (
        (verifier / "test.sh")
        .read_text()
        .replace("/logs/", f"{logs}/")
        .replace("/tests/", f"{verifier}/")
    )
    assert "--groundtruth-repo" not in script
    subprocess.run(
        ["bash", "-e"],
        input=script,
        text=True,
        check=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        },
    )
    metrics = json.loads((logs / "verifier/reward.json").read_text())
    assert metrics["reward"] == pytest.approx(1)

    # A failed rebuild must preserve the previously usable task.
    before = (task / "task.toml").read_bytes()
    (sources / protocol / "source.pdf").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        build([*args, "--force"])
    assert (task / "task.toml").read_bytes() == before
    assert (env / "protocol_sources" / document["local_path"]).is_file()
