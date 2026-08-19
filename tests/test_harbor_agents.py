from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("harbor")

from libstruct_bench.harbor_agents.pi_max import (  # noqa: E402
    PI_MAX_PACKAGE,
    PI_MAX_VERSION,
    PiMax,
    _provider_environment,
)


def test_pi_max_exposes_literal_max_reasoning(tmp_path: Path) -> None:
    thinking_flag = next(flag for flag in PiMax.CLI_FLAGS if flag.kwarg == "thinking")
    assert "max" in thinking_flag.choices
    assert PI_MAX_PACKAGE == "@earendil-works/pi-coding-agent"
    assert PI_MAX_VERSION == "0.84.2"

    agent = PiMax(
        tmp_path,
        version=PI_MAX_VERSION,
        thinking="max",
        model_name="anthropic/claude-opus-5",
    )
    assert agent.build_cli_flags() == "--thinking max"


def test_pi_max_passes_anthropic_auth_without_inventing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)

    assert _provider_environment("anthropic") == {
        "ANTHROPIC_API_KEY": "test-key"
    }
