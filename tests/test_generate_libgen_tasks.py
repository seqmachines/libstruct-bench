from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from libstruct_bench.cli.generate_libgen_tasks import (
    _environment_dockerfile,
    _protocols_from_audit_manifests,
    main,
)
from libstruct_bench.cli.plan_libgen_matrix import (
    _validate_network_smoke_report,
    main as plan_main,
)
from libstruct_bench.cli.prepare_libgen_hf_export import main as export_main
from libstruct_bench.cli.resume_libgen_job import main as resume_main
from libstruct_bench.cli.summarize_libgen_runs import (
    RAW_PUBLIC_METRICS,
    main as summarize_main,
)
from libstruct_bench.libgen.scoring import LIBGEN_PUBLIC_METRIC_KEYS
from libstruct_bench.libgen.telemetry import normalized_api_cost
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION
from tests.libgen_fixtures import (
    t1_groundtruth,
    t2_groundtruth,
    t2_prediction,
    t3_groundtruth,
    t3_prediction,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_METRIC_KEYS = LIBGEN_PUBLIC_METRIC_KEYS


def _public_metrics(factor: float = 1.0) -> dict[str, float]:
    t2_family = 0.2 * factor
    t3_transition = 0.4 * factor
    return {
        "reward": 0.30 * t2_family + 0.70 * t3_transition,
        "t2_required_family_f1": t2_family,
        "t2_exact_required_family_recall": 0.3 * factor,
        "t3_molecular_transition_f1": t3_transition,
        "t3_state_f1": 0.5 * factor,
        "t3_typed_edge_f1": 0.6 * factor,
    }


def test_summarizer_primary_metric_surface_matches_scorer() -> None:
    assert RAW_PUBLIC_METRICS == LIBGEN_PUBLIC_METRIC_KEYS


def _fixture_release(root: Path) -> tuple[Path, Path, Path]:
    source_root = root / "sources"
    truth_root = root / "truth"
    source = source_root / "example_protocol" / "source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"primary protocol fixture")
    truth_dir = truth_root / "example_protocol"
    truth_dir.mkdir(parents=True)
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (truth_dir / filename).write_text(json.dumps(document), encoding="utf-8")
    protocols = root / "protocols.json"
    protocols.write_text(
        json.dumps(
            {
                "protocols": [
                    {
                        "protocol_id": "example_protocol",
                        "display_name": "Example protocol",
                        "sources": [
                            {
                                "path": "example_protocol/source.pdf",
                                "sha256": hashlib.sha256(
                                    source.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                        "groundtruth_prefix": "example_protocol",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "rules.md").write_text((ROOT / "benchmarks/libgen/rules.md").read_text())
    return protocols, source_root, truth_root


def test_generator_builds_separate_docker_task_without_truth_leakage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )
            == 0
        )
        task = out / "example_protocol"
        task_toml = (task / "task.toml").read_text()
        dockerfile = (task / "environment/Dockerfile").read_text()
        instruction = (task / "instruction.md").read_text()
        manifest = (task / "input_manifest.json").read_text()
        rules = (task / "rules.md").read_text()
        test_sh = (task / "tests/test.sh").read_text()
        assert 'network_profile = "docker-provider-only"' in task_toml
        assert 'environment_mode = "separate"' in task_toml
        assert 'HF_TOKEN = "${HF_TOKEN:-}"' in task_toml
        assert "RUN python /workspace/fetch_input.py" in dockerfile
        assert "s|http://deb.debian.org|https://deb.debian.org|g" in dockerfile
        assert (
            "s|http://security.debian.org|https://security.debian.org|g" in dockerfile
        )
        task_config = tomllib.loads(task_toml)
        assert task_config["agent"]["timeout_sec"] == 3600.0
        assert task_config["verifier"]["environment"]["network_mode"] == "public"
        assert (
            task_config["verifier"]["environment"]["env"]["HF_TOKEN"] == "${HF_TOKEN:-}"
        )
        assert task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            },
            {
                "source": "/logs/network/provider-egress.jsonl",
                "destination": "provider_egress.jsonl",
                "service": "provider-egress",
            },
        ]
        assert "/logs/artifacts/t2_prediction.json" in rules
        assert "/logs/artifacts/t3_prediction.json" in rules
        assert "one record per oligo family" in rules
        assert "one family template" in instruction
        assert "groundtruth" not in instruction.lower()
        assert "groundtruth" not in manifest.lower()
        assert "org/private-groundtruth" not in dockerfile
        assert "--groundtruth-dir /tests/groundtruth" in test_sh
        assert '--groundtruth-repo "org/private-groundtruth"' in test_sh
        assert f'--groundtruth-revision "{"b" * 40}"' in test_sh
        assert "--error-analysis-out /logs/verifier/error_analysis.json" in test_sh
        assert "--trajectory /logs/agent/trajectory.json" in test_sh
        assert (
            task / "environment/schemas/benchmark/oligo_prediction.schema.json"
        ).is_file()
        agent_package = task / "environment/libstruct_bench"
        assert {
            path.relative_to(agent_package).as_posix()
            for path in agent_package.rglob("*")
            if path.is_file()
        } == {
            "__init__.py",
            "cli/__init__.py",
            "cli/validate_libgen_predictions.py",
            "libgen/__init__.py",
            "libgen/prediction_validation.py",
        }
        assert not (task / "environment/schemas/audit").exists()
        assert not (task / "environment/schemas/groundtruth").exists()
        assert not (agent_package / "audit").exists()
        assert not (agent_package / "libgen/scoring.py").exists()
        assert not (agent_package / "libgen/error_analysis.py").exists()
        assert not list(agent_package.rglob("*connected_process*"))
        private_groundtruth = task / "tests/groundtruth"
        assert {
            path.name for path in private_groundtruth.iterdir() if path.is_file()
        } == {
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        }
        for path in private_groundtruth.iterdir():
            assert (
                path.read_bytes()
                == (truth_root / "example_protocol" / path.name).read_bytes()
            )
            assert not (task / "environment" / path.name).exists()
        agent_runtime_text = "\n".join(
            path.read_text(encoding="utf-8") for path in agent_package.rglob("*.py")
        )
        private_t3 = t3_groundtruth()
        private_answer_ids = {
            item["state_id"]
            for workflow in private_t3["workflows"]
            for item in workflow["states"]
        } | {
            item["transition_id"]
            for workflow in private_t3["workflows"]
            for item in workflow["transitions"]
        }
        assert not any(item in agent_runtime_text for item in private_answer_ids)
        compose = (task / "environment/docker-compose.yaml").read_text()
        policy = json.loads((task / "environment/egress_policy.json").read_text())
        assert "internal: true" in compose
        assert "provider-egress:3128" in compose
        assert "coding-intl.dashscope.aliyuncs.com" in policy["provider_hosts"]
        assert "releases.astral.sh" in policy["setup_hosts"]
        assert "antigravity.google" in policy["setup_hosts"]
        assert (
            "antigravity-cli-auto-updater-974169037036.us-central1.run.app"
            in policy["setup_hosts"]
        )
        assert (task / "tests/libstruct_bench/libgen/scoring.py").is_file()
        assert (task / "tests/libstruct_bench/libgen/error_analysis.py").is_file()

        t2_path = root / "t2.json"
        t3_path = root / "t3.json"
        t2_path.write_text(json.dumps(t2_prediction()))
        t3_path.write_text(json.dumps(t3_prediction()))
        validation = subprocess.run(
            [
                sys.executable,
                "-m",
                "libstruct_bench.cli.validate_libgen_predictions",
                "--t2",
                str(t2_path),
                "--t3",
                str(t3_path),
                "--protocol-id",
                "example_protocol",
                "--schema-root",
                str(task / "environment/schemas"),
            ],
            env={**os.environ, "PYTHONPATH": str(task / "environment")},
            capture_output=True,
            text=True,
            check=False,
        )
        assert validation.returncode == 0, validation.stderr + validation.stdout


def test_generator_stages_legacy_terminal_contract_as_verifier_only_derivation() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        source_t3_path = (
            truth_root
            / "example_protocol"
            / "groundtruth_library_generation_workflow.json"
        )
        source_t3 = json.loads(source_t3_path.read_text())
        source_workflow = source_t3["workflows"][0]
        final_outputs = source_workflow.pop("final_outputs")
        source_workflow["modality"] = final_outputs[0]["modality"]
        source_workflow["final_state_ids"] = [
            item["state_id"] for item in final_outputs
        ]
        source_t3_path.write_text(json.dumps(source_t3), encoding="utf-8")

        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )
            == 0
        )

        task = out / "example_protocol"
        task_config = tomllib.loads((task / "task.toml").read_text())
        staged_t3 = json.loads(
            (
                task / "tests/groundtruth/groundtruth_library_generation_workflow.json"
            ).read_text()
        )
        staged_workflow = staged_t3["workflows"][0]
        test_sh = (task / "tests/test.sh").read_text()

        assert task_config["metadata"]["groundtruth_transform"] == (
            "legacy_workflow_terminal_contract_to_final_outputs_v1"
        )
        assert (
            task_config["metadata"]["groundtruth_source_bundle_sha256"]
            != (task_config["metadata"]["groundtruth_bundle_sha256"])
        )
        assert staged_workflow["final_outputs"] == final_outputs
        assert "modality" not in staged_workflow
        assert "final_state_ids" not in staged_workflow
        assert "--groundtruth-dir /tests/groundtruth" in test_sh
        assert "--groundtruth-repo" not in test_sh
        assert json.loads(source_t3_path.read_text()) == source_t3


def test_generator_can_embed_hash_verified_sources_for_unpublished_protocol() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                    "--source-delivery",
                    "embedded",
                ]
            )
            == 0
        )

        task = out / "example_protocol"
        task_config = tomllib.loads((task / "task.toml").read_text())
        embedded = task / "environment/protocol_sources/source.pdf"
        dockerfile = (task / "environment/Dockerfile").read_text()
        fetch_input = (task / "environment/fetch_input.py").read_text()

        assert task_config["metadata"]["source_delivery"] == "embedded"
        assert embedded.read_bytes() == b"primary protocol fixture"
        assert "COPY protocol_sources /workspace/input" in dockerfile
        assert "urllib" not in fetch_input
        assert "hashlib.sha256" in fetch_input
        assert not (
            task / "environment/protocol_sources/groundtruth_oligos.json"
        ).exists()


def test_generator_projects_only_included_primary_audit_sources() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        del protocols
        source = source_root / "example_protocol/source.pdf"
        manifest_root = root / "audit-manifests"
        manifest_root.mkdir()
        manifest = {
            "manifest_id": "example_protocol:inputs:test",
            "protocol_id": "example_protocol",
            "created_at": "2026-08-23T00:00:00Z",
            "source_catalog_sha256": "c" * 64,
            "checkpoint": {
                "checkpoint_id": "test-checkpoint",
                "protocol_ordinal": 1,
                "reviewed_protocol_count": 1,
            },
            "sources": [
                {
                    "source_id": "primary:included",
                    "role": "primary_evidence",
                    "source_kind": "protocol_document",
                    "approval_status": "included",
                    "task_relevance": ["T1", "T2", "T3"],
                    "path": "protocols/example_protocol/source.pdf",
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "size_bytes": source.stat().st_size,
                    "media_type": "application/pdf",
                    "dataset_reference": {
                        "provider": "local_fixture",
                        "repository": "fixture",
                        "revision": "fixture1",
                        "path": "example_protocol/source.pdf",
                    },
                },
                {
                    "source_id": "primary:unavailable",
                    "role": "primary_evidence",
                    "source_kind": "paper",
                    "approval_status": "unavailable",
                    "task_relevance": ["T1", "T2", "T3"],
                    "path": "protocols/example_protocol/missing.pdf",
                },
                {
                    "source_id": "legacy:fixture",
                    "role": "legacy_curated_html",
                    "source_kind": "legacy_html",
                    "approval_status": "included",
                    "task_relevance": ["T1", "T2", "T3"],
                    "path": "scg_html/example.html",
                    "sha256": "d" * 64,
                    "size_bytes": 1,
                    "media_type": "text/html",
                    "dataset_reference": {
                        "provider": "local_fixture",
                        "repository": "fixture",
                        "revision": "fixture1",
                        "path": "scg_html/example.html",
                    },
                },
                {
                    "source_id": "benchmark:fixture",
                    "role": "current_benchmark_record",
                    "source_kind": "current_t1",
                    "approval_status": "included",
                    "task_relevance": ["T1"],
                    "path": "ground_truth/example_protocol/t1.json",
                    "sha256": "e" * 64,
                    "size_bytes": 1,
                    "media_type": "application/json",
                    "dataset_reference": {
                        "provider": "local_fixture",
                        "repository": "fixture",
                        "revision": "fixture1",
                        "path": "ground_truth/example_protocol/t1.json",
                    },
                },
            ],
        }
        (manifest_root / "example_protocol.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        projected = _protocols_from_audit_manifests(
            manifest_root, ["example_protocol"]
        )
        assert projected == [
            {
                "protocol_id": "example_protocol",
                "display_name": "example_protocol",
                "sources": [
                    {
                        "path": "example_protocol/source.pdf",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
                "groundtruth_prefix": "example_protocol",
            }
        ]

        out = root / "audit-derived-tasks"
        assert (
            main(
                [
                    "--audit-manifest-root",
                    str(manifest_root),
                    "--rules",
                    str(ROOT / "benchmarks/libgen/rules.md"),
                    "--protocol-id",
                    "example_protocol",
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                    "--source-delivery",
                    "embedded",
                ]
            )
            == 0
        )
        task_manifest = json.loads(
            (out / "example_protocol/input_manifest.json").read_text()
        )
        assert [item["local_path"] for item in task_manifest["sources"]] == [
            "source.pdf"
        ]


def test_generator_can_build_native_harbor_allowlist_task() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                    "--network-profile",
                    "harbor-allowlist",
                ]
            )
            == 0
        )
        task_config = tomllib.loads((out / "example_protocol/task.toml").read_text())
        assert task_config["agent"]["timeout_sec"] == 3600.0
        assert task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            }
        ]
        assert task_config["metadata"]["network_profile"] == "harbor-allowlist"
        assert task_config["agent"]["network_mode"] == "allowlist"
        assert (
            "coding-intl.dashscope.aliyuncs.com"
            in task_config["agent"]["allowed_hosts"]
        )
        assert task_config["verifier"]["environment_mode"] == "separate"
        assert task_config["verifier"]["network_mode"] == "allowlist"
        assert task_config["environment"]["network_mode"] == "public"
        assert task_config["verifier"]["environment"]["network_mode"] == "public"
        assert (
            task_config["verifier"]["environment"]["env"]["HF_TOKEN"] == "${HF_TOKEN:-}"
        )


def test_docker_proxy_switches_from_setup_to_provider_only() -> None:
    proxy_path = ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    spec = importlib.util.spec_from_file_location("libgen_provider_proxy", proxy_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.Policy(ROOT / "benchmarks/libgen/network-policy.json")
    for host in (
        "registry.npmjs.org",
        "antigravity.google",
        "antigravity-cli-auto-updater-974169037036.us-central1.run.app",
    ):
        assert policy.authorize(host, 443)[0] is True
    assert policy.phase == "setup"
    for host in (
        "chatgpt.com",
        "auth.openai.com",
        "api.anthropic.com",
        "oauth2.googleapis.com",
        "www.googleapis.com",
        "cloudcode-pa.googleapis.com",
        "daily-cloudcode-pa.googleapis.com",
        "play.googleapis.com",
        "antigravity-unleash.goog",
        "lh3.googleusercontent.com",
    ):
        assert policy.authorize(host, 443)[0] is True
    assert policy.authorize("coding-intl.dashscope.aliyuncs.com", 443)[0] is True
    assert policy.phase == "agent"
    assert policy.authorize("registry.npmjs.org", 443)[0] is False
    assert policy.authorize("example.com", 443)[0] is False
    assert policy.authorize("api.openai.com", 80)[0] is False


def test_docker_proxy_uses_independent_long_lived_tunnel_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_path = ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    spec = importlib.util.spec_from_file_location(
        "libgen_provider_proxy_timeout", proxy_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.ProxyHandler.timeout == 30.0
    assert module.ProxyHandler.connect_timeout == 30.0
    assert module.ProxyHandler.tunnel_idle_timeout == 900.0

    class FakeSocket:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.sent: list[bytes] = []

        def recv(self, _size: int) -> bytes:
            return self.chunks.pop(0)

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    client = FakeSocket([b"request", b""])
    upstream = FakeSocket([])
    observed_timeouts: list[float] = []

    def fake_select(readers, _writers, _exceptional, timeout):
        observed_timeouts.append(timeout)
        return [readers[0]], [], []

    monotonic_values = iter((10.0, 12.5))
    monkeypatch.setattr(module.select, "select", fake_select)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))

    result = module._tunnel(client, upstream, idle_timeout=900.0)

    assert observed_timeouts == [900.0, 900.0]
    assert upstream.sent == [b"request"]
    assert result.reason == "client_eof"
    assert result.duration_sec == 2.5
    assert result.bytes_client_to_upstream == 7
    assert result.bytes_upstream_to_client == 0
    assert result.error_type is None


def test_docker_proxy_reports_idle_tunnel_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_path = ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    spec = importlib.util.spec_from_file_location(
        "libgen_provider_proxy_idle", proxy_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module.select,
        "select",
        lambda readers, writers, exceptional, timeout: ([], [], []),
    )
    monotonic_values = iter((20.0, 920.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))

    result = module._tunnel(object(), object(), idle_timeout=900.0)

    assert result.reason == "idle_timeout"
    assert result.duration_sec == 900.0
    assert result.bytes_client_to_upstream == 0
    assert result.bytes_upstream_to_client == 0


def test_docker_proxy_clears_socket_timeouts_after_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_path = ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    spec = importlib.util.spec_from_file_location(
        "libgen_provider_proxy_connect", proxy_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakePolicy:
        phase = "agent"

        def authorize(self, _host: str, _port: int) -> tuple[bool, str]:
            return True, "provider"

    class FakeSocket:
        def __init__(self) -> None:
            self.timeouts: list[float | None] = []
            self.closed = False

        def settimeout(self, timeout: float | None) -> None:
            self.timeouts.append(timeout)

        def close(self) -> None:
            self.closed = True

    client = FakeSocket()
    upstream = FakeSocket()
    connect_calls: list[tuple[tuple[str, int], float]] = []
    tunnel_calls: list[tuple[object, object, float]] = []
    events: list[tuple[str, dict[str, object]]] = []

    def fake_create_connection(target, timeout):
        connect_calls.append((target, timeout))
        return upstream

    def fake_tunnel(left, right, *, idle_timeout):
        tunnel_calls.append((left, right, idle_timeout))
        return module.TunnelResult("upstream_eof", 1.0, 12, 34)

    handler = object.__new__(module.ProxyHandler)
    handler.path = "api.anthropic.com:443"
    handler.policy = FakePolicy()
    handler.connection = client
    handler.send_response = lambda *_args, **_kwargs: None
    handler.end_headers = lambda: None
    handler.log_message = lambda *_args, **_kwargs: None
    monkeypatch.setattr(module.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(module, "_tunnel", fake_tunnel)
    monkeypatch.setattr(
        module,
        "_write_event",
        lambda event, **fields: events.append((event, fields)),
    )

    handler.do_CONNECT()

    assert connect_calls == [(("api.anthropic.com", 443), 30.0)]
    assert client.timeouts == [None]
    assert upstream.timeouts == [None]
    assert upstream.closed is True
    assert tunnel_calls == [(client, upstream, 900.0)]
    assert [event for event, _fields in events] == [
        "tunnel_opened",
        "tunnel_closed",
    ]
    assert events[-1][1]["reason"] == "upstream_eof"


def test_docker_proxy_writes_structured_diagnostic_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy_path = ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    spec = importlib.util.spec_from_file_location(
        "libgen_provider_proxy_diagnostics", proxy_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    event_log = tmp_path / "network/provider-egress.jsonl"
    monkeypatch.setattr(module, "EVENT_LOG_PATH", event_log)

    module._write_event(
        "tunnel_closed",
        target="api.anthropic.com:443",
        reason="upstream_eof",
        duration_sec=42.0,
        bytes_client_to_upstream=100,
        bytes_upstream_to_client=200,
    )

    event = json.loads(event_log.read_text(encoding="utf-8"))
    assert event["event"] == "tunnel_closed"
    assert event["target"] == "api.anthropic.com:443"
    assert event["reason"] == "upstream_eof"
    assert event["bytes_client_to_upstream"] == 100
    assert event["bytes_upstream_to_client"] == 200
    assert "timestamp" in event


def test_matrix_planner_rejects_network_smoke_without_setup_phase() -> None:
    policy = json.loads((ROOT / "benchmarks/libgen/network-policy.json").read_text())
    policy_digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stale_report = {
        "schema_version": 1,
        "ready": True,
        "network_policy_sha256": policy_digest,
        "probe": {
            "provider_api_access": {"reachable": True},
            "qwen_api_access": {"reachable": True},
            "unrelated_public_web_access": {"reachable": False},
            "direct_external_access": {"reachable": False},
        },
    }
    with pytest.raises(ValueError, match="schema must be version 2"):
        _validate_network_smoke_report(stale_report, policy_digest)

    failed_uv_report = {
        "schema_version": 2,
        "ready": True,
        "network_policy_sha256": policy_digest,
        "probe": {
            "setup_repository_access": {"reachable": True, "status": 200},
            "uv_installer_access": {"reachable": True, "status": 403},
            "antigravity_installer_access": {"reachable": True, "status": 200},
            "antigravity_manifest_access": {"reachable": True, "status": 200},
            "provider_api_access": {"reachable": True, "status": 401},
            "codex_subscription_access": {"reachable": True, "status": 401},
            "codex_oauth_access": {"reachable": True, "status": 401},
            "claude_subscription_access": {"reachable": True, "status": 401},
            "gemini_oauth_access": {"reachable": True, "status": 400},
            "gemini_userinfo_access": {"reachable": True, "status": 401},
            "gemini_code_assist_access": {"reachable": True, "status": 404},
            "antigravity_code_assist_access": {
                "reachable": True,
                "status": 404,
            },
            "antigravity_profile_access": {"reachable": True, "status": 404},
            "qwen_api_access": {"reachable": True, "status": 200},
            "setup_repository_access_after_provider": {"reachable": False},
            "unrelated_public_web_access": {"reachable": False},
            "direct_external_access": {"reachable": False},
        },
    }
    with pytest.raises(ValueError, match="failed uv_installer_access"):
        _validate_network_smoke_report(failed_uv_report, policy_digest)


def test_checked_in_libgen_tasks_keep_verifier_snapshots_synchronized() -> None:
    protocol_config = json.loads(
        (ROOT / "benchmarks/libgen/protocols.json").read_text()
    )
    protocol_ids = {item["protocol_id"] for item in protocol_config["protocols"]}
    task_root = ROOT / "benchmarks/libgen/tasks"
    task_dirs = {path.name: path for path in task_root.iterdir() if path.is_dir()}
    assert set(task_dirs) == protocol_ids

    source_package = ROOT / "src/libstruct_bench"
    synchronized_files = (
        "audit/groundtruth.py",
        "cli/grade_libgen.py",
        "libgen/error_analysis.py",
        "libgen/scoring.py",
        "libgen/validation.py",
        "libgen/version.py",
        "matching.py",
        "normalization.py",
    )
    synchronized_schemas = (
        "analysis/libgen_error_analysis.schema.json",
        "benchmark/library_generation_workflow_prediction.schema.json",
        "benchmark/oligo_prediction.schema.json",
        "groundtruth/library_generation_workflow.schema.json",
    )

    for protocol_id, task in sorted(task_dirs.items()):
        task_config = tomllib.loads((task / "task.toml").read_text())
        assert task_config["agent"]["timeout_sec"] == 3600.0, protocol_id
        assert (
            task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        ), protocol_id
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            },
            {
                "source": "/logs/network/provider-egress.jsonl",
                "destination": "provider_egress.jsonl",
                "service": "provider-egress",
            },
        ], protocol_id

        test_sh = (task / "tests/test.sh").read_text()
        assert "--groundtruth-repo" in test_sh
        assert "--groundtruth-dir /tests/groundtruth" in test_sh
        assert "--error-analysis-out /logs/verifier/error_analysis.json" in test_sh
        assert "--trajectory /logs/agent/trajectory.json" in test_sh

        private_groundtruth = task / "tests/groundtruth"
        assert {
            path.name for path in private_groundtruth.iterdir() if path.is_file()
        } == {
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        }, f"{protocol_id}: incomplete private verifier fallback"
        for filename in (
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        ):
            assert not list((task / "environment").rglob(filename)), (
                f"{protocol_id}: ground truth leaked into agent build context"
            )

        agent_package = task / "environment/libstruct_bench"
        agent_files = {
            path.relative_to(agent_package).as_posix()
            for path in agent_package.rglob("*")
            if path.is_file()
        }
        assert agent_files == {
            "__init__.py",
            "cli/__init__.py",
            "cli/validate_libgen_predictions.py",
            "libgen/__init__.py",
            "libgen/prediction_validation.py",
        }, f"{protocol_id}: agent runtime leakage"
        assert not any("connected_process" in item for item in agent_files)
        assert not (task / "environment/schemas/audit").exists()
        assert not (task / "environment/schemas/groundtruth").exists()
        assert (agent_package / "libgen/prediction_validation.py").read_bytes() == (
            source_package / "libgen/prediction_validation.py"
        ).read_bytes()

        package_copy = task / "tests/libstruct_bench"
        for relative in synchronized_files + ("libgen/prediction_validation.py",):
            assert (package_copy / relative).read_bytes() == (
                source_package / relative
            ).read_bytes(), f"{protocol_id}: stale {package_copy / relative}"

        for schema_relative in synchronized_schemas:
            assert (task / "tests/schemas" / schema_relative).read_bytes() == (
                ROOT / "schemas" / schema_relative
            ).read_bytes(), f"{protocol_id}: stale verifier {schema_relative}"
        for schema_relative in (
            "benchmark/library_generation_workflow_prediction.schema.json",
            "benchmark/oligo_prediction.schema.json",
        ):
            assert (task / "environment/schemas" / schema_relative).read_bytes() == (
                ROOT / "schemas" / schema_relative
            ).read_bytes(), f"{protocol_id}: stale agent {schema_relative}"
        assert (task / "rules.md").read_bytes() == (
            ROOT / "benchmarks/libgen/rules.md"
        ).read_bytes(), f"{protocol_id}: stale rules.md"
        assert (task / "environment/rules.md").read_bytes() == (
            ROOT / "benchmarks/libgen/rules.md"
        ).read_bytes(), f"{protocol_id}: stale environment/rules.md"
        assert (task / "environment/Dockerfile").read_text() == (
            _environment_dockerfile()
        ), f"{protocol_id}: stale agent Dockerfile"
        for network_asset in (
            "ProviderProxy.Dockerfile",
            "docker-compose.yaml",
            "network_smoke.py",
            "provider_egress_proxy.py",
        ):
            assert (task / "environment" / network_asset).read_bytes() == (
                ROOT / "benchmarks/libgen/docker_network" / network_asset
            ).read_bytes(), f"{protocol_id}: stale {network_asset}"


def test_generator_refuses_mutable_revisions_and_mixed_source_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        with pytest.raises(ValueError, match="immutable"):
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(root / "tasks"),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/source",
                    "--input-revision",
                    "main",
                    "--groundtruth-repo",
                    "org/truth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )

        leaked = source_root / "example_protocol/groundtruth_oligos.json"
        leaked.write_text("{}")
        with pytest.raises(ValueError, match="split export"):
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(root / "tasks"),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/source",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/truth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )


def test_split_export_copies_only_manifest_sources_and_canonical_truth() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        (source_root / "example_protocol/legacy.html").write_text("legacy")
        (source_root / "example_protocol/groundtruth_oligos.json").write_text("{}")
        out = root / "export"
        assert (
            export_main(
                [
                    "--protocols",
                    str(protocols),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--schema-root",
                    str(ROOT / "schemas"),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        assert (out / "protocol_sources/example_protocol/source.pdf").is_file()
        assert not (out / "protocol_sources/example_protocol/legacy.html").exists()
        assert not (
            out / "protocol_sources/example_protocol/groundtruth_oligos.json"
        ).exists()
        assert (out / "groundtruth/example_protocol/groundtruth_oligos.json").is_file()


def test_matrix_planner_creates_16_unique_cells_and_64_trial_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tasks = root / "tasks"
        protocols = json.loads((ROOT / "benchmarks/libgen/protocols.json").read_text())
        for protocol in protocols["protocols"]:
            task = tasks / protocol["protocol_id"]
            task.mkdir(parents=True)
            (task / "task.toml").write_text('schema_version = "1.3"\n')
        env_names = [
            "LIBGEN_CORE_MODEL_GPT_5_6_SOL",
            "LIBGEN_NATIVE_MODEL_GPT_5_6_SOL",
            "LIBGEN_CORE_MODEL_CLAUDE_OPUS_5",
            "LIBGEN_NATIVE_MODEL_CLAUDE_OPUS_5",
            "LIBGEN_CORE_MODEL_GEMINI_3_7_FLASH",
            "LIBGEN_NATIVE_MODEL_GEMINI_3_7_FLASH",
            "LIBGEN_CORE_MODEL_KIMI_K3",
            "LIBGEN_NATIVE_MODEL_QWEN_3_8_MAX",
            "LIBGEN_KIMI_CLI_VERSION",
            "LIBGEN_MINI_SWE_AGENT_VERSION",
            "LIBGEN_PI_VERSION",
            "LIBGEN_CODEX_VERSION",
            "LIBGEN_CLAUDE_CODE_VERSION",
            "LIBGEN_GEMINI_CLI_VERSION",
            "LIBGEN_QWEN_CODE_VERSION",
            "LIBGEN_QWEN_OPENAI_BASE_URL",
        ]
        for name in env_names:
            value = (
                f"test-provider/{name.lower()}"
                if "MODEL" in name
                else f"pinned-{name.lower()}"
            )
            if name == "LIBGEN_QWEN_CODE_VERSION":
                value = "0.21.12"
            if name == "LIBGEN_QWEN_OPENAI_BASE_URL":
                value = "https://coding-intl.dashscope.aliyuncs.com/v1"
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            "libstruct_bench.cli.plan_libgen_matrix._harbor_version", lambda: "9.9.9"
        )
        policy = json.loads(
            (ROOT / "benchmarks/libgen/network-policy.json").read_text()
        )
        policy_digest = hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        network_smoke = root / "docker-network-smoke.json"
        network_smoke.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "ready": True,
                    "network_policy_sha256": policy_digest,
                    "probe": {
                        "setup_repository_access": {
                            "reachable": True,
                            "status": 200,
                        },
                        "uv_installer_access": {"reachable": True, "status": 200},
                        "antigravity_installer_access": {
                            "reachable": True,
                            "status": 200,
                        },
                        "antigravity_manifest_access": {
                            "reachable": True,
                            "status": 200,
                        },
                        "provider_api_access": {"reachable": True, "status": 401},
                        "codex_subscription_access": {
                            "reachable": True,
                            "status": 401,
                        },
                        "codex_oauth_access": {"reachable": True, "status": 401},
                        "claude_subscription_access": {
                            "reachable": True,
                            "status": 401,
                        },
                        "gemini_oauth_access": {"reachable": True, "status": 400},
                        "gemini_userinfo_access": {
                            "reachable": True,
                            "status": 401,
                        },
                        "gemini_code_assist_access": {
                            "reachable": True,
                            "status": 404,
                        },
                        "antigravity_code_assist_access": {
                            "reachable": True,
                            "status": 404,
                        },
                        "antigravity_profile_access": {
                            "reachable": True,
                            "status": 404,
                        },
                        "qwen_api_access": {"reachable": True, "status": 200},
                        "setup_repository_access_after_provider": {"reachable": False},
                        "unrelated_public_web_access": {"reachable": False},
                        "direct_external_access": {"reachable": False},
                    },
                }
            )
        )
        out = root / "plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "pilot",
                    "--out",
                    str(out),
                    "--harbor-version",
                    "9.9.9",
                    "--network-smoke-report",
                    str(network_smoke),
                ]
            )
            == 0
        )
        lock = json.loads((out / "experiment_lock.json").read_text())
        assert len(lock["cells"]) == 16
        assert lock["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert lock["expected_trial_count"] == 64
        assert sum(item["design"] == "balanced_core" for item in lock["cells"]) == 12
        assert sum(item["native_pairing"] for item in lock["cells"]) == 5
        assert len(list((out / "jobs").glob("*.json"))) == 16
        kimi_native = [
            item
            for item in lock["cells"]
            if item["model_key"] == "kimi_k3" and item["harness_key"] == "kimi_code"
        ]
        assert len(kimi_native) == 1
        assert kimi_native[0]["design"] == "balanced_core"
        assert kimi_native[0]["native_pairing"] is True
        qwen_native = [
            item
            for item in lock["cells"]
            if item["model_key"] == "qwen_3_8_max"
            and item["harness_key"] == "qwen_code"
        ]
        assert len(qwen_native) == 1
        assert qwen_native[0]["design"] == "native_extension"
        assert qwen_native[0]["harbor_agent"] == "qwen-coder"
        assert qwen_native[0]["harness_version"] == "0.21.12"
        assert qwen_native[0]["auth_mode"] == "provider_api_key"
        native_by_agent = {
            item["harbor_agent"]: item
            for item in lock["cells"]
            if item["design"] == "native_extension"
        }
        assert native_by_agent["codex"]["auth_mode"] == "host_cli_subscription"
        assert native_by_agent["claude-code"]["auth_mode"] == ("host_cli_subscription")
        assert native_by_agent["gemini-cli"]["auth_mode"] == ("host_cli_subscription")
        assert lock["analysis_design"] == {
            "balanced_core_cells": 12,
            "model_and_harness_effects_use": "balanced_core",
            "native_descriptive_pairings": 5,
            "native_extension_cells": 4,
            "native_only_cells": 4,
            "native_pairings_are_reported_separately": True,
            "overlapping_core_native_pairings": 1,
            "overlapping_pairings_execute_once": True,
            "unique_execution_cells": 16,
        }
        first = json.loads(next((out / "jobs").glob("*.json")).read_text())
        assert first["environment"]["type"] == "docker"
        assert first["agents"][0]["skills"] == []
        assert first["agents"][0]["mcp_servers"] == []
        assert first["agents"][0]["include_logs"] == []
        assert first["agents"][0]["exclude_logs"] == []
        assert first["retry"]["max_retries"] == 0
        assert "artifacts" not in first
        assert len(lock["task_bundle_sha256"]) == 64
        assert len(lock["pricing_snapshot_sha256"]) == 64
        assert lock["pricing_snapshot"]["snapshot_id"].startswith("libgen-api-pricing-")
        assert lock["error_analysis_policy"]["automatic_process_attribution"] is False
        assert lock["network_policy"]["enforcement"] == (
            "docker_internal_network_with_connect_proxy"
        )
        assert lock["network_smoke_required_before_execution"] is True
        assert lock["network_smoke_report"]["ready"] is True
        assert (
            lock["network_smoke_report"]["probe"]["unrelated_public_web_access"][
                "reachable"
            ]
            is False
        )
        assert lock["native_auth_policy"] == {
            "codex_claude_gemini": "host_cli_subscription",
            "api_key_fallback_allowed": False,
            "credential_contents_persisted_in_plan_or_lock": False,
        }
        codex_job = json.loads((out / "jobs/gpt_5_6_sol__codex.json").read_text())
        assert codex_job["agents"][0]["env"] == {"CODEX_FORCE_AUTH_JSON": "1"}
        claude_job = json.loads(
            (out / "jobs/claude_opus_5__claude_code.json").read_text()
        )
        assert claude_job["agents"][0]["env"] == {
            "CLAUDE_FORCE_OAUTH": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "${CLAUDE_CODE_OAUTH_TOKEN}",
        }
        gemini_job = json.loads(
            (out / "jobs/gemini_3_7_flash__gemini_cli.json").read_text()
        )
        assert gemini_job["agents"][0]["env"] == {"GEMINI_FORCE_OAUTH": "1"}
        for job in (codex_job, claude_job, gemini_job):
            assert "OPENAI_API_KEY" not in job["agents"][0]["env"]
            assert "ANTHROPIC_API_KEY" not in job["agents"][0]["env"]
            assert "GEMINI_API_KEY" not in job["agents"][0]["env"]
        run_script = (out / "run.sh").read_text()
        assert "codex login" in run_script
        assert "claude setup-token" in run_script
        assert "Login with Google" in run_script
        assert "CLAUDE_CODE_OAUTH_TOKEN=${" not in run_script
        assert lock["primary_aggregation_policy"]["agent_timeout"] == "zero"
        assert (
            lock["primary_aggregation_policy"]["agent_timeout_rerun_eligible"] is False
        )
        assert lock["primary_aggregation_policy"]["metrics"] == list(PUBLIC_METRIC_KEYS)
        assert lock["primary_aggregation_policy"]["cost_performance_metric"] == (
            "primary_t3_molecular_transition_f1"
        )

        with pytest.raises(ValueError, match="pilot-clearance"):
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "full",
                    "--out",
                    str(root / "full-without-clearance"),
                    "--harbor-version",
                    "9.9.9",
                    "--network-smoke-report",
                    str(network_smoke),
                ]
            )

        clearance = root / "pilot_review_status.json"
        clearance.write_text(
            json.dumps(
                {
                    "full_run_ready": True,
                    "expected_trial_count": 64,
                    "task_bundle_sha256": lock["task_bundle_sha256"],
                }
            ),
            encoding="utf-8",
        )
        full_out = root / "full-plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "full",
                    "--out",
                    str(full_out),
                    "--harbor-version",
                    "9.9.9",
                    "--pilot-clearance",
                    str(clearance),
                    "--network-smoke-report",
                    str(network_smoke),
                ]
            )
            == 0
        )
        full_lock = json.loads((full_out / "experiment_lock.json").read_text())
        assert full_lock["expected_trial_count"] == 640
        assert full_lock["pilot_clearance"]["full_run_ready"] is True

        smoke_out = root / "smoke-plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "smoke",
                    "--out",
                    str(smoke_out),
                    "--harbor-version",
                    "9.9.9",
                    "--network-smoke-report",
                    str(network_smoke),
                ]
            )
            == 0
        )
        smoke_lock = json.loads((smoke_out / "experiment_lock.json").read_text())
        assert smoke_lock["protocol_ids"] == ["s3_atac"]
        assert smoke_lock["expected_trial_count"] == 16
        smoke_config = json.loads(next((smoke_out / "jobs").glob("*.json")).read_text())
        assert smoke_config["datasets"][0]["task_names"] == ["s3_atac"]
        qwen_config = json.loads(
            (smoke_out / "jobs/qwen_3_8_max__qwen_code.json").read_text()
        )
        assert qwen_config["agents"][0]["env"]["OPENAI_BASE_URL"] == (
            "https://coding-intl.dashscope.aliyuncs.com/v1"
        )


def test_run_summarizer_keeps_core_and_native_estimands_separate(
    tmp_path: Path,
) -> None:
    lock = {
        "mode": "pilot",
        "expected_trial_count": 2,
        "cells": [
            {
                "design": "balanced_core",
                "native_pairing": True,
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "harness_a",
                "harness_version": "1.0",
            },
            {
                "design": "native_extension",
                "native_pairing": True,
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "native_a",
                "harness_version": "2.0",
            },
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    runs = tmp_path / "runs"
    for harness, reward in (("harness_a", 0.75), ("native_a", 0.9)):
        trial = runs / f"libgen-pilot-model_a-{harness}" / "example_protocol__abc"
        (trial / "verifier").mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": trial.name,
                    "config": {"task": {"path": "tasks/example_protocol"}},
                    "agent_result": {"n_input_tokens": 10, "n_output_tokens": 2},
                    "verifier_result": {
                        "rewards": {
                            "reward": reward,
                            "t2_required_family_f1": reward,
                        }
                    },
                    "exception_info": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": "2026-01-01T00:01:00+00:00",
                }
            )
        )
        (trial / "verifier/details.json").write_text(
            json.dumps({"prediction_valid": True})
        )
    output = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(runs),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["balanced_core"]["grand_mean_reward"] == 0.75
    assert summary["native_pairings"]["cell_mean_reward"] == {
        "model_a__harness_a": 0.75,
        "model_a__native_a": 0.9,
    }
    assert summary["native_extensions"]["cell_mean_reward"] == {
        "model_a__native_a": 0.9
    }


def test_telemetry_keeps_timeout_usage_and_does_not_invalidate_prediction(
    tmp_path: Path,
) -> None:
    pricing = {
        "schema_version": 1,
        "snapshot_id": "fixture-pricing",
        "as_of": "2026-01-01",
        "unit_tokens": 1_000_000,
        "models": {
            "model_a": {
                "source_url": "https://provider.example/pricing",
                "source_retrieved_at": "2026-01-01",
                "rates_per_million_tokens": {
                    "uncached_input": 1.0,
                    "cached_input": 0.1,
                    "cache_creation_5m": 1.25,
                    "output": 2.0,
                },
            }
        },
    }
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 1,
        "pricing_snapshot": pricing,
        "telemetry_policy": {"automatic_retries": 0},
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    trial = tmp_path / "runs/libgen-smoke-model_a-codex/example_protocol__timeout"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "agent_result": {
                    "n_input_tokens": 100,
                    "n_cache_tokens": 40,
                    "n_output_tokens": 10,
                    "cost_usd": 0.0123,
                },
                "verifier_result": {"rewards": _public_metrics()},
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:10Z",
                "agent_execution": {
                    "started_at": "2026-01-01T00:00:05Z",
                    "finished_at": "2026-01-01T00:01:05Z",
                },
                "verifier": {
                    "started_at": "2026-01-01T00:01:05Z",
                    "finished_at": "2026-01-01T00:01:10Z",
                },
            }
        )
    )
    (trial / "verifier/details.json").write_text(json.dumps({"prediction_valid": True}))
    (trial / "agent/trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "steps": [
                    {
                        "source": "agent",
                        "metrics": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "cached_tokens": 40,
                            "extra": {
                                "reasoning_output_tokens": 4,
                                "cache_creation_input_tokens": 20,
                            },
                        },
                    }
                ],
                "final_metrics": {
                    "total_prompt_tokens": 100,
                    "total_completion_tokens": 10,
                    "total_cached_tokens": 40,
                    "total_cost_usd": 0.0123,
                    "extra": {"reasoning_output_tokens": 4},
                },
            }
        )
    )
    out = tmp_path / "out"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    with (out / "trials.csv").open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["exception_type"] == "AgentTimeoutError"
    assert row["prediction_valid"] == "True"
    assert row["verifier_completed"] == "True"
    assert row["valid_completion"] == "True"
    assert row["agent_duration_seconds"] == "60.0"
    assert row["verifier_duration_seconds"] == "5.0"
    assert row["input_tokens"] == "100"
    assert row["reasoning_tokens"] == "4"
    assert row["cache_creation_tokens"] == "20"
    assert row["reported_cost_usd"] == "0.0123"
    assert row["reported_cost_kind"] == "estimated"
    assert float(row["normalized_api_cost_usd"]) == pytest.approx(89 / 1_000_000)
    assert json.loads(row["telemetry_missing_fields_json"]) == []
    summary = json.loads((out / "summary.json").read_text())
    assert summary["agent_exception_count"] == 1
    assert summary["valid_completion_rate"] == 1.0
    assert summary["scored_current_trial_count"] == 1
    assert summary["balanced_core"]["grand_mean_reward"] == 0.0
    assert summary["primary_outcome_counts"] == {"agent_timeout": 1}
    assert summary["valid_output_only"][
        "grand_mean_reward_balanced_core"
    ] == pytest.approx(_public_metrics()["reward"])
    assert (
        summary["plot_readiness"]["api_equivalent_cost_vs_t3_transition_f1"]["y"]
        == "primary_t3_molecular_transition_f1"
    )
    assert (
        summary["plot_readiness"]["api_equivalent_cost_vs_t3_transition_f1"][
            "complete_row_count"
        ]
        == 1
    )
    with (out / "primary_attempts.csv").open(newline="") as handle:
        primary = next(csv.DictReader(handle))
    assert primary["primary_reward"] == "0.0"
    assert primary["primary_score_reason"] == "agent_timeout"
    for metric, raw_value in _public_metrics().items():
        assert float(primary[metric]) == pytest.approx(raw_value)
        assert float(primary[f"primary_{metric}"]) == 0.0
        assert float(primary[f"valid_output_{metric}"]) == pytest.approx(raw_value)
        assert (
            summary["balanced_core"]["primary_metric_means"]["grand"][
                f"primary_{metric}"
            ]
            == 0.0
        )
        assert summary["valid_output_only"]["metric_means"]["grand"][
            f"valid_output_{metric}"
        ] == pytest.approx(raw_value)


def test_resume_wrapper_preserves_superseded_timeout_execution(tmp_path: Path) -> None:
    job = tmp_path / "runs/libgen-smoke-model_a-codex"
    trial = job / "example_protocol__failed"
    trial.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({"retry": {"max_retries": 0}}))
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "agent_result": {
                    "n_input_tokens": 123,
                    "n_output_tokens": 9,
                    "cost_usd": 0.4,
                },
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:00Z",
            }
        )
    )
    assert (
        resume_main(
            [
                "-p",
                str(job),
                "-f",
                "AgentTimeoutError",
                "--snapshot-only",
            ]
        )
        == 0
    )
    snapshots = list(job.glob(".libgen_telemetry/resume_snapshots/*/*/result.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text())["agent_result"]["n_input_tokens"] == 123

    pricing = {
        "snapshot_id": "fixture",
        "as_of": "2026-01-01",
        "unit_tokens": 1_000_000,
        "models": {
            "model_a": {
                "source_url": "https://provider.example/pricing",
                "source_retrieved_at": "2026-01-01",
                "rates_per_million_tokens": {
                    "uncached_input": 1.0,
                    "cached_input": 0.1,
                    "output": 2.0,
                },
            }
        },
    }
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 1,
        "pricing_snapshot": pricing,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    out = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    with (out / "trials.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    preserved = next(row for row in rows if row["is_current_execution"] == "False")
    assert preserved["superseded_by_resume"] == "True"
    assert preserved["input_tokens"] == "123"
    assert preserved["reported_cost_usd"] == "0.4"
    assert preserved["resume_count"] == "1"
    primary = next(row for row in rows if row["is_primary_execution"] == "True")
    assert primary["primary_score_reason"] == "agent_timeout"
    assert primary["primary_reward"] == "0.0"


def test_invalid_and_incomplete_attempts_zero_every_primary_metric(
    tmp_path: Path,
) -> None:
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["invalid_protocol", "incomplete_protocol"],
        "expected_trial_count": 2,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    job = tmp_path / "runs/libgen-smoke-model_a-codex"
    fixtures = (
        ("invalid_protocol", False, _public_metrics(1.25)),
        ("incomplete_protocol", True, None),
    )
    for index, (protocol_id, prediction_valid, metrics) in enumerate(fixtures):
        trial = job / f"{protocol_id}__fixture"
        (trial / "verifier").mkdir(parents=True)
        result = {
            "trial_name": trial.name,
            "config": {"task": {"path": f"tasks/{protocol_id}"}},
            "exception_info": None,
            "started_at": f"2026-01-01T00:0{index}:00Z",
            "finished_at": f"2026-01-01T00:0{index}:30Z",
        }
        if metrics is not None:
            result["verifier_result"] = {"rewards": metrics}
        (trial / "result.json").write_text(json.dumps(result))
        (trial / "verifier/details.json").write_text(
            json.dumps({"prediction_valid": prediction_valid})
        )

    out = tmp_path / "out"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    with (out / "primary_attempts.csv").open(newline="") as handle:
        primary = {row["protocol_id"]: row for row in csv.DictReader(handle)}
    assert primary["invalid_protocol"]["primary_score_reason"] == "invalid_prediction"
    assert primary["incomplete_protocol"]["primary_score_reason"] == (
        "incomplete_output"
    )
    for row in primary.values():
        for metric in PUBLIC_METRIC_KEYS:
            assert float(row[f"primary_{metric}"]) == 0.0
    for metric, raw_value in _public_metrics(1.25).items():
        assert float(primary["invalid_protocol"][metric]) == pytest.approx(raw_value)
        assert primary["invalid_protocol"][f"valid_output_{metric}"] == ""
    summary = json.loads((out / "summary.json").read_text())
    for metric in PUBLIC_METRIC_KEYS:
        assert (
            summary["balanced_core"]["primary_metric_means"]["grand"][
                f"primary_{metric}"
            ]
            == 0.0
        )
    assert summary["valid_output_only"]["attempt_count"] == 0


def test_confirmed_infrastructure_rerun_can_replace_primary_execution(
    tmp_path: Path,
) -> None:
    job = tmp_path / "runs/libgen-smoke-model_a-codex"
    trial = job / "example_protocol__failed"
    (trial / "verifier").mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({"retry": {"max_retries": 0}}))
    result_path = trial / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "exception_info": {"exception_type": "ApiRateLimitError"},
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:00Z",
            }
        )
    )
    assert (
        resume_main(
            [
                "-p",
                str(job),
                "-f",
                "ApiRateLimitError",
                "--confirmed-infrastructure-outage",
                "--confirmed-by",
                "reviewer",
                "--reason",
                "provider status incident 123",
                "--snapshot-only",
            ]
        )
        == 0
    )
    result_path.write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "verifier_result": {"rewards": _public_metrics(1.5)},
                "exception_info": None,
                "started_at": "2026-01-01T01:00:00Z",
                "finished_at": "2026-01-01T01:01:00Z",
            }
        )
    )
    (trial / "verifier/details.json").write_text(json.dumps({"prediction_valid": True}))
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 1,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    out = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    summary = json.loads((out / "summary.json").read_text())
    assert summary["balanced_core"]["grand_mean_reward"] == pytest.approx(
        _public_metrics(1.5)["reward"]
    )
    assert summary["primary_outcome_counts"] == {"valid_prediction": 1}
    with (out / "primary_attempts.csv").open(newline="") as handle:
        replacement = next(csv.DictReader(handle))
    for metric, raw_value in _public_metrics(1.5).items():
        assert float(replacement[f"primary_{metric}"]) == pytest.approx(raw_value)
        assert summary["balanced_core"]["primary_metric_means"]["cell"][
            f"primary_{metric}"
        ]["model_a__codex"] == pytest.approx(raw_value)


def test_agent_timeout_cannot_be_confirmed_as_infrastructure(tmp_path: Path) -> None:
    job = tmp_path / "job"
    trial = job / "example_protocol__timeout"
    trial.mkdir(parents=True)
    (job / "config.json").write_text("{}")
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "exception_info": {"exception_type": "AgentTimeoutError"},
            }
        )
    )
    with pytest.raises(ValueError, match="not eligible"):
        resume_main(
            [
                "-p",
                str(job),
                "-f",
                "AgentTimeoutError",
                "--confirmed-infrastructure-outage",
                "--confirmed-by",
                "reviewer",
                "--reason",
                "not actually infrastructure",
                "--snapshot-only",
            ]
        )


def test_missing_scheduled_attempt_contributes_zero(tmp_path: Path) -> None:
    lock = {
        "mode": "smoke",
        "attempts": 2,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 2,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    trial = tmp_path / "runs/libgen-smoke-model_a-codex/example_protocol__one"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "verifier_result": {"rewards": _public_metrics()},
                "exception_info": None,
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:00Z",
            }
        )
    )
    (trial / "verifier/details.json").write_text(json.dumps({"prediction_valid": True}))
    out = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 1
    )
    summary = json.loads((out / "summary.json").read_text())
    assert summary["balanced_core"]["grand_mean_reward"] == pytest.approx(
        _public_metrics()["reward"] / 2
    )
    assert summary["primary_outcome_counts"] == {
        "scheduled_attempt_missing": 1,
        "valid_prediction": 1,
    }
    with (out / "primary_attempts.csv").open(newline="") as handle:
        attempts = list(csv.DictReader(handle))
    assert len(attempts) == 2
    valid = next(
        row for row in attempts if row["primary_score_reason"] == "valid_prediction"
    )
    missing = next(
        row
        for row in attempts
        if row["primary_score_reason"] == "scheduled_attempt_missing"
    )
    for metric, raw_value in _public_metrics().items():
        assert float(valid[f"primary_{metric}"]) == pytest.approx(raw_value)
        assert float(missing[f"primary_{metric}"]) == 0.0
        assert summary["balanced_core"]["primary_metric_means"]["grand"][
            f"primary_{metric}"
        ] == pytest.approx(raw_value / 2)
        metric_summary = summary["balanced_core"]["primary_metric_means"]
        assert metric_summary["model"][f"primary_{metric}"]["model_a"] == pytest.approx(
            raw_value / 2
        )
        assert metric_summary["harness"][f"primary_{metric}"]["codex"] == pytest.approx(
            raw_value / 2
        )
        assert metric_summary["cell"][f"primary_{metric}"][
            "model_a__codex"
        ] == pytest.approx(raw_value / 2)


def test_api_cost_normalization_applies_per_call_long_context_and_cache_ttl() -> None:
    snapshot = {
        "unit_tokens": 1_000_000,
        "models": {
            "m": {
                "rates_per_million_tokens": {
                    "uncached_input": 2.0,
                    "cached_input": 0.2,
                    "cache_creation_5m": 2.5,
                    "cache_creation_1h": 4.0,
                    "output": 10.0,
                },
                "long_context": {
                    "threshold_input_tokens_exclusive": 100,
                    "input_multiplier": 2.0,
                    "output_multiplier": 1.5,
                },
            }
        },
    }
    result = normalized_api_cost(
        snapshot,
        "m",
        [
            {
                "prompt_tokens": 110,
                "completion_tokens": 10,
                "cached_tokens": 20,
                "cache_creation_tokens": 30,
                "cache_creation_5m_tokens": 10,
                "cache_creation_1h_tokens": 20,
            }
        ],
        {},
    )
    # Input: 60*2 + 20*.2 + 10*2.5 + 20*4 = 229, then 2x.
    # Output: 10*10 = 100, then 1.5x.
    assert result["cost_usd"] == pytest.approx(608 / 1_000_000)
    assert result["precision"] == "per_api_call"


def test_api_cost_normalization_does_not_invent_a_missing_rate() -> None:
    result = normalized_api_cost(
        {
            "unit_tokens": 1_000_000,
            "models": {
                "qwen": {
                    "pricing_status": "per_token_usd_rate_unavailable",
                    "source_url": "https://provider.example/token-plan",
                }
            },
        },
        "qwen",
        [],
        {"prompt_tokens": 100, "completion_tokens": 20},
    )

    assert result == {
        "cost_usd": None,
        "precision": "pricing_rate_unavailable",
    }
