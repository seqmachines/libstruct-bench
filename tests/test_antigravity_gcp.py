import asyncio
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")

from libstruct_bench.harbor_agents.antigravity_gcp import (
    AntigravityGcp, PINNED_VERSION, RELEASES, build_install_command, resolve_adc,
)
from libstruct_bench.harbor_agents import antigravity_gcp, antigravity_pinned


def make_agent(tmp_path, **kwargs):
    credential = tmp_path / "adc.json"
    credential.write_text(json.dumps({
        "type": "authorized_user", "quota_project_id": "test-project",
        "refresh_token": "fake-secret-not-for-logs",
    }))
    return AntigravityGcp(
        tmp_path / "logs", gcp_project="test-project", adc_path=str(credential),
        model_name="google/gemini-3.7-flash-high", version=PINNED_VERSION,
        reasoning_effort="high", print_timeout="29m", **kwargs,
    )


def test_adc_rejects_wrong_project_before_upload(tmp_path):
    credential = tmp_path / "adc.json"
    credential.write_text('{"quota_project_id":"different-project"}')
    with pytest.raises(ValueError, match="set-quota-project test-project"):
        resolve_adc("test-project", str(credential))


@pytest.mark.parametrize("fail_model", [False, True])
def test_adc_passed_to_container_without_secrets_in_logs(tmp_path, monkeypatch, fail_model):
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated-api-secret")
    monkeypatch.setenv("AGY_FORCE_AUTH_JSON", "1")
    agent = make_agent(tmp_path)
    assert agent.extra_env == {}
    assert agent._resolve_auth_token_path() is None
    assert "modelProvider" not in agent._build_settings_config()[0]

    async def execute(**kwargs):
        if fail_model and "--prompt=" in kwargs["command"]:
            raise RuntimeError("simulated provider failure")
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    environment = SimpleNamespace(
        default_user="agent", upload_file=AsyncMock(), exec=AsyncMock(side_effect=execute),
    )
    if fail_model:
        with pytest.raises(RuntimeError, match="simulated provider failure"):
            asyncio.run(agent.run("Test instruction", environment, SimpleNamespace()))
    else:
        asyncio.run(agent.run("Test instruction", environment, SimpleNamespace()))
    environment.upload_file.assert_awaited_once_with(str(tmp_path / "adc.json"), agent._ADC_REMOTE)
    calls = [call.kwargs for call in environment.exec.call_args_list]
    model_call = next(c for c in calls if "--prompt=" in c["command"])
    assert "--model gemini-3.7-flash-high" in model_call["command"]
    assert "--print-timeout 29m" in model_call["command"]
    assert model_call["env"]["AGY_ADC_AUTH"] == "true"
    assert model_call["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert model_call["env"]["GOOGLE_APPLICATION_CREDENTIALS"] == agent._ADC_REMOTE
    assert model_call["env"]["GOOGLE_CLOUD_QUOTA_PROJECT"] == "test-project"
    assert "GEMINI_API_KEY" not in model_call["env"]
    assert "rm -f" in calls[-1]["command"] and agent._ADC_REMOTE in calls[-1]["command"]
    assert "fake-secret" not in str(calls)
    assert "fake-secret" not in (tmp_path / "logs/gcp-auth.json").read_text()


@pytest.mark.parametrize("valid_checksum", [False, True])
@pytest.mark.parametrize("adapter", [antigravity_gcp, antigravity_pinned])
def test_pinned_installer_verifies_archive_before_install(tmp_path, monkeypatch, valid_checksum, adapter):
    payload = f"#!/bin/bash\nprintf '{adapter.PINNED_VERSION}'\n".encode()
    fixture = tmp_path / "fixture"
    with tarfile.open(fixture, "w:gz") as archive:
        member = tarfile.TarInfo("antigravity")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    checksum = hashlib.sha512(fixture.read_bytes()).hexdigest() if valid_checksum else "0" * 128
    for arch in adapter.RELEASES:
        monkeypatch.setitem(adapter.RELEASES, arch, {"url": "https://example.invalid/test-only", "sha512": checksum})
    fake_curl = tmp_path / "curl"
    fake_curl.write_text('#!/bin/bash\nwhile [ "$1" != "-o" ]; do shift; done\ncp "$AGY_TEST_PAYLOAD" "$2"\n')
    fake_curl.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", adapter.build_install_command(adapter.PINNED_VERSION, str(tmp_path / "bin/agy"))],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "AGY_TEST_PAYLOAD": str(fixture)},
    )
    binary = tmp_path / "bin/agy"
    if valid_checksum:
        assert result.returncode == 0, result.stderr
        assert binary.read_bytes() == payload
        assert os.access(binary, os.X_OK)
    else:
        assert result.returncode != 0
        assert "checksum mismatch" in result.stderr
        assert not binary.exists()


def test_installer_rejects_unpinned_release():
    with pytest.raises(ValueError, match="pins CLI 1.3.2"):
        build_install_command("1.1.13")


def test_install_stops_on_version_drift(tmp_path):
    agent = make_agent(tmp_path)
    agent.exec_as_root = AsyncMock()
    agent.exec_as_agent = AsyncMock(return_value=SimpleNamespace(stdout="Antigravity 9.9.9"))
    with pytest.raises(RuntimeError, match=f"expected '{PINNED_VERSION}'"):
        asyncio.run(agent.install(SimpleNamespace()))


def test_round2_config_and_installer_pin_match():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "paper/benchmarks/task1/antigravity-cli.json").read_text())
    agent = config["agents"][0]
    assert agent["kwargs"]["version"] == PINNED_VERSION
    assert agent["model_name"] == "google/gemini-3.7-flash-high"
    assert agent["kwargs"]["reasoning_effort"] == "high"
    for release in RELEASES.values():
        assert f"/antigravity-cli/{PINNED_VERSION}-" in release["url"]


def test_libgen_gcp_overlay_adds_only_vertex_and_preserves_network_policy(tmp_path, monkeypatch):
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1]
    overlay = yaml.safe_load((root / "paper/benchmarks/tasks2_3/antigravity-gcp.compose.yaml").read_text())
    entrypoint = overlay["services"]["provider-egress"]["entrypoint"]
    source = tmp_path / "base.json"
    original = (root / "benchmarks/molecular_workflow_reconstruction/network-policy.json").read_bytes()
    source.write_bytes(original)
    effective = tmp_path / "effective.json"
    events = tmp_path / "logs/provider_egress.jsonl"
    proxy_path = root / "benchmarks/molecular_workflow_reconstruction/docker_network/provider_egress_proxy.py"
    monkeypatch.setattr(sys, "argv", ["-c", str(source), str(effective), str(events), str(proxy_path)])
    executions = []
    monkeypatch.setattr(os, "execv", lambda *args: executions.append(args))
    exec(compile(entrypoint[2], "antigravity-gcp.compose.yaml", "exec"), {})
    assert executions == [(sys.executable, [sys.executable, str(proxy_path), str(effective)])]
    assert source.read_bytes() == original
    expected = json.loads(original)
    expected["provider_hosts"].append("aiplatform.googleapis.com")
    assert json.loads(effective.read_text()) == expected
    receipt = json.loads(events.read_text())
    assert receipt["effective_policy_sha256"] == hashlib.sha256(effective.read_bytes()).hexdigest()

    spec = importlib.util.spec_from_file_location("gcp_test_proxy", proxy_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.Policy(effective)
    assert policy.authorize("storage.googleapis.com", 443) == (True, "setup")
    assert policy.authorize("example.com", 443)[0] is False
    assert policy.authorize("aiplatform.googleapis.com", 80)[0] is False
    assert policy.authorize("aiplatform.googleapis.com", 443) == (True, "provider")
    assert policy.authorize("oauth2.googleapis.com", 443) == (True, "provider")
    assert policy.authorize("storage.googleapis.com", 443)[0] is False
    assert policy.authorize("example.com", 443)[0] is False
    assert policy.authorize("us-aiplatform.googleapis.com", 443)[0] is False
