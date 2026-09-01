from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from libstruct_bench.improvement.artifacts import CapabilityImprovementError
from libstruct_bench.improvement import worker_runtime
from libstruct_bench.improvement.local_learning import LocalCodexRunRequest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "improvement" / "worker_runtime"
PROXY_PATH = REPO_ROOT / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
POLICY_PATH = RUNTIME_ROOT / "provider-api-policy.json"


def _rendered_contract(tmp_path: Path) -> tuple[dict, dict[str, Path]]:
    paths = {
        "workspace": tmp_path / "isolated-workspace",
        "auth": tmp_path / "credential" / "auth.json",
        "network_logs": tmp_path / "isolated-workspace" / "outputs" / "network",
    }
    document = {
        "networks": {
            "agent-internal": {"internal": True},
            "provider-external": {},
        },
        "services": {
            "worker": {
                "build": {
                    "context": RUNTIME_ROOT.as_posix(),
                    "dockerfile": "Dockerfile",
                    "args": {"CODEX_VERSION": "0.147.0"},
                },
                "cap_drop": ["ALL"],
                "command": None,
                "depends_on": {
                    "provider-egress": {
                        "condition": "service_healthy",
                        "required": True,
                    }
                },
                "entrypoint": None,
                "environment": {
                    "HOME": "/tmp/home",
                    "CODEX_HOME": "/codex-home",
                    "CODEX_FORCE_AUTH_JSON": "1",
                    "LIBGEN_CODEX_MODEL": "gpt-5.6-sol",
                    "LIBGEN_CODEX_REASONING_EFFORT": "max",
                    "LIBGEN_CODEX_VERSION": "0.147.0",
                    "HTTP_PROXY": "http://provider-egress:3128",
                    "HTTPS_PROXY": "http://provider-egress:3128",
                    "http_proxy": "http://provider-egress:3128",
                    "https_proxy": "http://provider-egress:3128",
                    "NO_PROXY": "localhost,127.0.0.1,provider-egress",
                    "no_proxy": "localhost,127.0.0.1,provider-egress",
                },
                "networks": {"agent-internal": None},
                "pids_limit": 512,
                "read_only": True,
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": [
                    "/tmp:size=1g,mode=1777",
                    (
                        f"/codex-home:size=32m,mode=0700,uid={os.getuid()},"
                        f"gid={os.getgid()}"
                    ),
                ],
                "user": f"{os.getuid()}:{os.getgid()}",
                "volumes": [
                    {
                        "type": "bind",
                        "source": paths["workspace"].as_posix(),
                        "target": "/workspace",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": (paths["workspace"] / "candidates").as_posix(),
                        "target": "/workspace/candidates",
                    },
                    {
                        "type": "bind",
                        "source": (paths["workspace"] / "outputs").as_posix(),
                        "target": "/workspace/outputs",
                    },
                    {
                        "type": "bind",
                        "source": paths["auth"].as_posix(),
                        "target": "/run/secrets/libgen-codex-auth.json",
                        "read_only": True,
                    },
                ],
            },
            "provider-egress": {
                "build": {
                    "context": RUNTIME_ROOT.as_posix(),
                    "dockerfile": "Dockerfile",
                    "args": {"CODEX_VERSION": "0.147.0"},
                },
                "cap_drop": ["ALL"],
                "command": None,
                "entrypoint": [
                    "python3",
                    "/proxy/provider_egress_proxy.py",
                    "/proxy/egress_policy.json",
                ],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python3",
                        "-c",
                        (
                            "import socket; socket.create_connection("
                            "('127.0.0.1', 3128), 1).close()"
                        ),
                    ],
                    "timeout": "2s",
                    "interval": "2s",
                    "retries": 30,
                },
                "networks": {
                    "agent-internal": None,
                    "provider-external": None,
                },
                "pids_limit": 128,
                "read_only": True,
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:size=64m,mode=1777"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": PROXY_PATH.as_posix(),
                        "target": "/proxy/provider_egress_proxy.py",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": POLICY_PATH.as_posix(),
                        "target": "/proxy/egress_policy.json",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": paths["network_logs"].as_posix(),
                        "target": "/logs/network",
                    },
                ],
            },
        },
    }
    return document, paths


def _validate(document: dict, paths: dict[str, Path]) -> None:
    worker_runtime.validate_rendered_worker_compose(
        document,
        workspace=paths["workspace"],
        auth_file=paths["auth"],
        proxy_path=PROXY_PATH,
        policy_path=POLICY_PATH,
        network_log_root=paths["network_logs"],
        runtime_root=RUNTIME_ROOT,
        model="gpt-5.6-sol",
        version="0.147.0",
        reasoning_effort="max",
    )


def test_rendered_worker_has_closed_world_mounts_and_no_policy_roots(
    tmp_path: Path,
) -> None:
    document, paths = _rendered_contract(tmp_path)
    _validate(document, paths)

    worker_mount_sources = {
        item["source"] for item in document["services"]["worker"]["volumes"]
    }
    assert worker_mount_sources == {
        paths["workspace"].as_posix(),
        (paths["workspace"] / "candidates").as_posix(),
        (paths["workspace"] / "outputs").as_posix(),
        paths["auth"].as_posix(),
    }
    forbidden_host_roots = {
        (
            tmp_path / "experiment" / "design" / "validation_access_policy.json"
        ).as_posix(),
        (
            tmp_path / "experiment" / "design" / "final_test_access_policy.json"
        ).as_posix(),
        (tmp_path / "private-ground-truth").as_posix(),
        Path.home().as_posix(),
    }
    assert worker_mount_sources.isdisjoint(forbidden_host_roots)

    escaped = copy.deepcopy(document)
    escaped["services"]["worker"]["volumes"].append(
        {
            "type": "bind",
            "source": next(iter(forbidden_host_roots)),
            "target": "/host-policy",
            "read_only": True,
        }
    )
    with pytest.raises(CapabilityImprovementError, match="mounts must be exactly"):
        _validate(escaped, paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["networks"]["agent-internal"].update(internal=False),
            "internal",
        ),
        (
            lambda value: value["services"]["worker"].update(read_only=False),
            "read-only",
        ),
        (
            lambda value: value["services"]["worker"].update(privileged=True),
            "forbidden",
        ),
        (
            lambda value: value["services"]["worker"]["environment"].update(
                LIBGEN_CODEX_MODEL="different-model"
            ),
            "unpinned",
        ),
        (
            lambda value: value["services"]["worker"]["volumes"][3].update(
                read_only=False
            ),
            "mounts must be exactly",
        ),
    ],
)
def test_rendered_worker_rejects_boundary_weakening(
    tmp_path: Path, mutation, message: str
) -> None:
    document, paths = _rendered_contract(tmp_path)
    mutation(document)
    with pytest.raises(CapabilityImprovementError, match=message):
        _validate(document, paths)


def test_worker_assets_have_no_pinned_model_or_host_codex_fallback() -> None:
    compose = (RUNTIME_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    dockerfile = (RUNTIME_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (RUNTIME_ROOT / "container-run-worker.sh").read_text(encoding="utf-8")
    runner = (
        REPO_ROOT / "src/libstruct_bench/improvement/local_learning.py"
    ).read_text(encoding="utf-8")

    assert "LIBGEN_CODEX_AUTH_FILE" in compose
    assert "LIBGEN_CODEX_DIR" not in compose
    assert "target: /run/secrets/libgen-codex-auth.json" in compose
    assert "read_only: true" in compose
    assert "internal: true" in compose
    assert "        - python3\n" in compose
    assert "        - python\n" not in compose
    assert dockerfile.startswith("FROM node:22-bookworm-slim@sha256:")
    assert "ARG CODEX_VERSION\n" in dockerfile
    assert "gpt-5.6-sol" not in entrypoint
    assert "0.147.0" not in entrypoint
    assert '--sandbox",\n        "workspace-write' not in runner
    assert "no host Codex fallback is permitted" in (
        REPO_ROOT / "src/libstruct_bench/improvement/worker_runtime.py"
    ).read_text(encoding="utf-8")


def test_rendered_worker_rejects_unavailable_proxy_healthcheck_interpreter(
    tmp_path: Path,
) -> None:
    document, paths = _rendered_contract(tmp_path)
    document["services"]["provider-egress"]["healthcheck"]["test"][1] = "python"
    with pytest.raises(CapabilityImprovementError, match="Python 3 socket healthcheck"):
        _validate(document, paths)


def test_provider_policy_is_openai_only_and_has_no_setup_egress() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert set(policy["provider_hosts"]) == {
        "api.openai.com",
        "auth.openai.com",
        "chatgpt.com",
    }
    assert policy["setup_hosts"] == []


def test_checked_in_compose_renders_to_the_validated_boundary(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")
    compose_version = subprocess.run(
        [docker, "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if compose_version.returncode != 0:
        pytest.skip("Docker Compose plugin is unavailable")

    document, paths = _rendered_contract(tmp_path)
    del document
    (paths["workspace"] / "candidates").mkdir(parents=True)
    (paths["workspace"] / "outputs" / "network").mkdir(parents=True)
    paths["auth"].parent.mkdir()
    paths["auth"].write_text("{}\n")
    environment = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "LIBGEN_WORKSPACE": paths["workspace"].as_posix(),
        "LIBGEN_CANDIDATE_DIR": (paths["workspace"] / "candidates").as_posix(),
        "LIBGEN_OUTPUT_DIR": (paths["workspace"] / "outputs").as_posix(),
        "LIBGEN_CODEX_AUTH_FILE": paths["auth"].as_posix(),
        "LIBGEN_CODEX_FORCE_AUTH_JSON": "1",
        "LIBGEN_CODEX_MODEL": "gpt-5.6-sol",
        "LIBGEN_CODEX_REASONING_EFFORT": "max",
        "LIBGEN_CODEX_VERSION": "0.147.0",
        "LIBGEN_PROVIDER_PROXY": PROXY_PATH.as_posix(),
        "LIBGEN_NETWORK_POLICY": POLICY_PATH.as_posix(),
        "LIBGEN_NETWORK_LOG_DIR": paths["network_logs"].as_posix(),
        "LIBGEN_WORKER_UID": str(os.getuid()),
        "LIBGEN_WORKER_GID": str(os.getgid()),
    }
    prefix = (
        docker,
        "compose",
        "--ansi",
        "never",
        "--project-name",
        "libgen-worker-contract-test",
        "--file",
        (RUNTIME_ROOT / "docker-compose.yaml").as_posix(),
    )
    rendered = worker_runtime._render_compose(prefix, environment)  # noqa: SLF001
    _validate(rendered, paths)


def test_auth_resolution_mounts_one_json_file_and_requires_auth_json_mode(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    auth = codex_home / "auth.json"
    auth.write_text('{"tokens":{"access_token":"not-a-real-token"}}\n')
    config = codex_home / "config.toml"
    config.write_text('model = "must-not-be-mounted"\n')

    resolved = worker_runtime._resolve_auth_file(  # noqa: SLF001
        {"CODEX_HOME": codex_home.as_posix(), "CODEX_FORCE_AUTH_JSON": "1"}
    )
    assert resolved == auth.resolve()
    assert resolved != config.resolve()

    with pytest.raises(
        CapabilityImprovementError, match="requires CODEX_FORCE_AUTH_JSON=1"
    ):
        worker_runtime._resolve_auth_file(  # noqa: SLF001
            {"CODEX_HOME": codex_home.as_posix(), "CODEX_FORCE_AUTH_JSON": "0"}
        )


def test_launch_uses_experiment_anchor_and_does_not_forward_host_secrets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    document, paths = _rendered_contract(tmp_path)
    paths["workspace"].mkdir()
    (paths["workspace"] / "inputs").mkdir()
    (paths["workspace"] / "candidates").mkdir()
    (paths["workspace"] / "outputs").mkdir()
    prompt = paths["workspace"] / "inputs" / "prompt.md"
    schema = paths["workspace"] / "inputs" / "schema.json"
    prompt.write_text("test\n")
    schema.write_text("{}\n")
    paths["auth"].parent.mkdir()
    paths["auth"].write_text('{"tokens":{"access_token":"local-only"}}\n')
    request = LocalCodexRunRequest(
        workspace=paths["workspace"],
        prompt_path=prompt,
        output_schema_path=schema,
        draft_output_path=paths["workspace"] / "outputs" / "proposal_draft.json",
        event_log_path=paths["workspace"] / "outputs" / "proposal.events.jsonl",
        stderr_log_path=paths["workspace"] / "outputs" / "proposal.stderr.log",
        model="gpt-5.6-sol",
        version="0.147.0",
        reasoning_effort="max",
        codex_executable="codex",
        idle_timeout_seconds=300,
        hard_timeout_seconds=7200,
    )
    monkeypatch.setattr(
        worker_runtime,
        "_validate_workspace_contract",
        lambda _request: paths["workspace"].resolve(),
    )
    monkeypatch.setattr(
        worker_runtime,
        "validate_isolated_worker_workspace",
        lambda _workspace: {"workspace_digest": "a" * 64},
    )
    monkeypatch.setattr(
        worker_runtime,
        "_resolve_docker_executable",
        lambda _environ: "/usr/local/bin/docker",
    )
    monkeypatch.setattr(
        worker_runtime,
        "_render_compose",
        lambda _command, _environment: document,
    )

    launch = worker_runtime.prepare_isolated_codex_launch(
        request,
        environ={
            "PATH": "/usr/bin",
            "CODEX_FORCE_AUTH_JSON": "1",
            "LIBGEN_CODEX_AUTH_FILE": paths["auth"].as_posix(),
            "OPENAI_API_KEY": "must-not-be-forwarded",
            "VALIDATION_ROOT": "/private/validation",
            "FINAL_TEST_ROOT": "/private/final-test",
        },
    )
    assert launch.auth_file == paths["auth"].resolve()
    assert launch.environment["LIBGEN_CODEX_MODEL"] == "gpt-5.6-sol"
    assert launch.environment["LIBGEN_CODEX_VERSION"] == "0.147.0"
    assert launch.environment["LIBGEN_CODEX_REASONING_EFFORT"] == "max"
    assert launch.environment["LIBGEN_CODEX_FORCE_AUTH_JSON"] == "1"
    assert "OPENAI_API_KEY" not in launch.environment
    assert "VALIDATION_ROOT" not in launch.environment
    assert "FINAL_TEST_ROOT" not in launch.environment
    assert launch.command[-5:] == (
        "run",
        "--build",
        "--rm",
        "--no-TTY",
        "worker",
    )


def test_host_codex_override_is_rejected_before_launch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    request = LocalCodexRunRequest(
        workspace=workspace,
        prompt_path=workspace / "inputs/prompt.md",
        output_schema_path=workspace / "inputs/schema.json",
        draft_output_path=workspace / "outputs/draft.json",
        event_log_path=workspace / "outputs/events.jsonl",
        stderr_log_path=workspace / "outputs/stderr.log",
        model="gpt-5.6-sol",
        version="0.147.0",
        reasoning_effort="max",
        codex_executable="/usr/local/bin/codex",
        idle_timeout_seconds=300,
        hard_timeout_seconds=7200,
    )
    with pytest.raises(
        CapabilityImprovementError, match="host .*override is forbidden"
    ):
        worker_runtime.prepare_isolated_codex_launch(request, environ={})
