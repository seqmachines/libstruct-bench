import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")

from libstruct_bench.harbor_agents.antigravity_pinned import (
    AntigravityPinned, PINNED_VERSION, RELEASES, build_install_command,
)


def make_agent(tmp_path):
    return AntigravityPinned(
        tmp_path / "logs", model_name="google/gemini-3.7-flash-high",
        version="1.1.13",
    )


@pytest.mark.parametrize("fail_model", [False, True])
def test_account_install_and_run_preserve_native_settings(tmp_path, monkeypatch, fail_model):
    token = tmp_path / "oauth-token"
    token.write_text("fake-account-secret")
    monkeypatch.setenv("AGY_AUTH_JSON_PATH", str(token))
    monkeypatch.setenv("AGY_ADC_AUTH", "true")
    monkeypatch.setenv("AGY_CLI_DISABLE_AUTO_UPDATE", "false")
    for key in AntigravityPinned._CLOUD_AUTH:
        monkeypatch.setenv(key, "unrelated-cloud-secret")
    agent = make_agent(tmp_path)

    async def execute(**kwargs):
        command = kwargs["command"]
        if fail_model and "--prompt=" in command:
            raise RuntimeError("simulated provider failure")
        return SimpleNamespace(
            return_code=0, stdout="1.1.13\n" if "--version" in command else "", stderr="",
        )

    environment = SimpleNamespace(
        default_user="agent", upload_file=AsyncMock(), exec=AsyncMock(side_effect=execute),
    )
    asyncio.run(agent.install(environment))
    environment.upload_file.assert_awaited_once_with(str(token), agent._REMOTE_TOKEN_STAGING)
    receipt = json.loads((tmp_path / "logs/cli-install.json").read_text())
    assert receipt["observed_version"] == "1.1.13"
    assert receipt["releases"] == RELEASES
    assert agent.extra_env == {}
    settings, alias = agent._build_settings_config("gemini-3.7-flash-high")
    assert "modelProvider" not in settings
    assert alias is None
    assert settings["modelConfigs"] == {"defaultModel": "gemini-3.7-flash-high"}
    if fail_model:
        with pytest.raises(RuntimeError, match="simulated provider failure"):
            asyncio.run(agent.run("Test instruction", environment, SimpleNamespace()))
    else:
        asyncio.run(agent.run("Test instruction", environment, SimpleNamespace()))
    calls = [call.kwargs for call in environment.exec.call_args_list]
    model_call = next(c for c in calls if "--prompt=" in c["command"])
    assert "--model gemini-3.7-flash-high" in model_call["command"]
    assert "--print-timeout 55m" in model_call["command"]
    assert model_call["env"]["AGY_ADC_AUTH"] == "false"
    assert model_call["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert not (agent._CLOUD_AUTH & model_call["env"].keys())
    assert calls[-1]["command"].endswith(f'rm -f "{agent._REMOTE_TOKEN_PATH}"')
    assert "fake-account-secret" not in str(calls)
    assert "unrelated-cloud-secret" not in str(calls)


def test_version_drift_stops_before_token_upload(tmp_path, monkeypatch):
    token = tmp_path / "oauth-token"
    token.touch()
    monkeypatch.setenv("AGY_AUTH_JSON_PATH", str(token))
    agent = make_agent(tmp_path)
    agent.exec_as_root = AsyncMock()
    agent.exec_as_agent = AsyncMock(return_value=SimpleNamespace(stdout="1.3.2"))
    environment = SimpleNamespace(upload_file=AsyncMock())
    with pytest.raises(RuntimeError, match="expected '1.1.13'"):
        asyncio.run(agent.install(environment))
    environment.upload_file.assert_not_awaited()


def test_missing_account_auth_fails_before_install(tmp_path, monkeypatch):
    monkeypatch.delenv("AGY_AUTH_JSON_PATH", raising=False)
    monkeypatch.delenv("AGY_FORCE_AUTH_JSON", raising=False)
    agent = make_agent(tmp_path)
    agent.exec_as_root = AsyncMock()
    with pytest.raises(ValueError, match="Account OAuth is required"):
        asyncio.run(agent.install(SimpleNamespace()))
    agent.exec_as_root.assert_not_awaited()


def test_rejects_other_versions_and_scrubbed_boolean_env(tmp_path):
    with pytest.raises(ValueError, match="pins CLI 1.1.13"):
        build_install_command("1.3.2")
    with pytest.raises(ValueError, match="pins CLI 1.1.13"):
        AntigravityPinned(tmp_path / "logs", version="1.3.2")
    with pytest.raises(ValueError, match="leave agent env empty"):
        AntigravityPinned(tmp_path / "logs", extra_env={"AGY_FORCE_AUTH_JSON": "1"})
    assert PINNED_VERSION == "1.1.13"
    assert all(f"/{PINNED_VERSION}-" in r["url"] for r in RELEASES.values())
