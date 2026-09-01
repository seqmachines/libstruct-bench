"""Harbor Pi adapter with current adaptive-thinking support.

Harbor 0.20.0 pins the legacy Pi npm package and only accepts thinking levels
through ``xhigh``.  Pi 0.84.2 adds the ``max`` level and describes Claude Opus
5 as an adaptive-thinking model.  This adapter keeps the experiment
reproducible without modifying the globally installed Harbor package.
"""

from __future__ import annotations

import os
import shlex
from typing import override

from harbor.agents.installed.base import CliFlag, with_prompt_template
from harbor.agents.installed.node_install import nvm_node_install_snippet
from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


PI_MAX_VERSION = "0.84.2"
PI_MAX_PACKAGE = "@earendil-works/pi-coding-agent"


class PiMax(Pi):
    """Pi 0.84.2 adapter exposing its literal ``--thinking max`` setting."""

    CLI_FLAGS = [
        CliFlag(
            "thinking",
            cli="--thinking",
            type="enum",
            choices=["off", "minimal", "low", "medium", "high", "xhigh", "max"],
        ),
    ]

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if self._version != PI_MAX_VERSION:
            raise ValueError(
                f"PiMax requires version={PI_MAX_VERSION}; got {self._version!r}"
            )

        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"{nvm_node_install_snippet()} && "
                f"npm install -g --ignore-scripts {PI_MAX_PACKAGE}@{PI_MAX_VERSION} && "
                "pi --version"
            ),
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")

        provider, model = self.model_name.split("/", 1)
        env = _provider_environment(provider)

        skills_command = self._build_register_skills_command()
        if skills_command:
            await self.exec_as_agent(environment, command=skills_command)

        cli_flags = self.build_cli_flags()
        if cli_flags:
            cli_flags += " "
        resume_flag = "--continue " if self._resume else ""

        # pipefail matters here: without it a provider/API failure from Pi is
        # hidden by tee's successful exit and Harbor records a false zero-score
        # trial instead of an agent exception.
        await self.exec_as_agent(
            environment,
            command=(
                "set -o pipefail; "
                ". ~/.nvm/nvm.sh; "
                "pi --print --mode json --session-dir /logs/agent/pi/sessions "
                f"{resume_flag}"
                f"--provider {shlex.quote(provider)} --model {shlex.quote(model)} "
                f"{cli_flags}"
                f"{shlex.quote(instruction)} "
                "2>&1 </dev/null "
                "| grep -v '\"type\":\"message_update\"' "
                "| stdbuf -oL tee /logs/agent/pi.txt"
            ),
            env=env,
        )


def _provider_environment(provider: str) -> dict[str, str]:
    keys_by_provider = {
        "amazon-bedrock": (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_REGION",
        ),
        "anthropic": (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_OAUTH_TOKEN",
        ),
        "github-copilot": ("GITHUB_TOKEN",),
        "google": (
            "GEMINI_API_KEY",
            "GOOGLE_GENERATIVE_AI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_API_KEY",
        ),
        "groq": ("GROQ_API_KEY",),
        "huggingface": ("HF_TOKEN",),
        "kimi-coding": ("KIMI_API_KEY",),
        "mistral": ("MISTRAL_API_KEY",),
        "moonshotai": ("MOONSHOT_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "xai": ("XAI_API_KEY",),
    }
    return {
        key: value
        for key in keys_by_provider.get(provider, ())
        if (value := os.environ.get(key))
    }

