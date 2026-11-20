"""Antigravity CLI with Google Cloud ADC inside Harbor containers.

Only installation and authentication differ from Harbor's native adapter.
The inherited prompt, model flags, tools and trajectory handling are retained.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from harbor.agents.installed.antigravity_cli import AntigravityCli

from .antigravity_pinned import build_release_install_command


PINNED_VERSION = "1.3.2"
# Official Google release URLs and SHA-512 hashes from the updater manifests,
# retrieved on 2026-10-09. Keep the resolved release fixed across trials.
# Do not use the latest-release bootstrapper: Harbor's version kwarg does not
# cause that script to install the requested version.
RELEASE_BASE = (
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
    "1.3.2-5813501495738368"
)
RELEASES = {
    "arm64": {
        "url": f"{RELEASE_BASE}/linux-arm/cli_linux_arm64.tar.gz",
        "sha512": "44c33101f05efc925a12ca87a5c1c90c594da05dfa11eb4b747f22e7d478f4e9a6b596b92076266c46153eadc87d488e9dbfc3cae99f4f50d5208e6b8814a1d4",
    },
    "amd64": {
        "url": f"{RELEASE_BASE}/linux-x64/cli_linux_x64.tar.gz",
        "sha512": "8dc2bb8471cfa6326e29e2896add767ad2d1f3714304b70fbae720d68af011182e7afde43964a7a3f54c604ab72c4fad772c8595608b059cfc582fb6798a25d2",
    },
}

def build_install_command(version: str, destination: str = "") -> str:
    if version != PINNED_VERSION:
        raise ValueError(f"This adapter pins CLI {PINNED_VERSION}; got {version!r}")
    return build_release_install_command(RELEASES, destination)


def resolve_adc(project: str, adc_path: str | None = None) -> Path:
    """Validate routing without printing or copying credential contents."""
    path = Path(
        adc_path
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or "~/.config/gcloud/application_default_credentials.json"
    ).expanduser().resolve()
    if not path.is_file():
        raise ValueError(
            "ADC credentials are missing. Run: gcloud auth application-default "
            f"login --project {project}"
        )
    try:
        document = json.loads(path.read_text())
    except (ValueError, OSError):
        raise ValueError("Cannot read a valid ADC JSON file") from None
    # ADC's quota_project_id takes precedence over environment variables in agy.
    if document.get("quota_project_id") != project:
        raise ValueError(
            "ADC quota project does not match the configured project. Run: "
            f"gcloud auth application-default set-quota-project {project}"
        )
    return path


class AntigravityGcp(AntigravityCli):
    """Use the existing CLI harness with an explicit Cloud billing project."""

    _ADC_REMOTE = "/tmp/.libstruct-agy-adc.json"
    _PERSONAL_AUTH = frozenset({
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI",
        "AGY_FORCE_AUTH_JSON", "AGY_AUTH_JSON_PATH",
    })

    def __init__(self, *args, gcp_project: str, adc_path: str | None = None,
                 gcp_location: str = "global", **kwargs):
        self._gcp_project = gcp_project
        self._gcp_location = gcp_location
        self._adc_path = resolve_adc(gcp_project, adc_path)
        super().__init__(*args, **kwargs)
        if self.extra_env:
            raise ValueError("Configure GCP through kwargs; leave agent env empty")

    def _cloud_env(self) -> dict[str, str]:
        return {
            "AGY_ADC_AUTH": "true",
            "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
            "GOOGLE_APPLICATION_CREDENTIALS": self._ADC_REMOTE,
            "GOOGLE_CLOUD_QUOTA_PROJECT": self._gcp_project,
            "GOOGLE_CLOUD_PROJECT": self._gcp_project,
            "GOOGLE_CLOUD_LOCATION": self._gcp_location,
        }

    def _get_env(self, key: str) -> str | None:
        if key in self._PERSONAL_AUTH:
            return None
        return self._cloud_env().get(key) or super()._get_env(key)

    async def exec_as_agent(self, environment, command, env=None,
                            cwd=None, timeout_sec=None):
        # Keep boolean AUTH switches out of extra_env: Harbor scrubs those
        # values from all artifacts, including JSON booleans and sequences.
        return await super().exec_as_agent(
            environment, command, env={**(env or {}), **self._cloud_env()},
            cwd=cwd, timeout_sec=timeout_sec,
        )

    async def install(self, environment):
        command = build_install_command(self._version)
        await self.exec_as_root(
            environment, "apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(environment, command)
        result = await self.exec_as_agent(environment, self.get_version_command())
        match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout or "")
        actual = match.group(0) if match else None
        if not self._version or actual != self._version:
            raise RuntimeError(
                f"Antigravity installed version {actual!r}; expected "
                f"{self._version!r}. Stopping before any model requests."
            )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "cli-install.json").write_text(json.dumps({
            "requested_version": self._version,
            "observed_version": actual,
            "version_stdout": result.stdout,
            "auto_update_disabled": True,
            "releases": RELEASES,
        }, indent=2) + "\n")

    async def run(self, instruction, environment, context):
        # Transfer through upload_file, never inline credentials into commands
        # or place them under the collected logs/artifacts directories.
        try:
            await environment.upload_file(str(self._adc_path), self._ADC_REMOTE)
            await self.exec_as_root(
                environment, f"chmod 600 {shlex.quote(self._ADC_REMOTE)}",
            )
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    f"chown {shlex.quote(str(environment.default_user))} "
                    f"{shlex.quote(self._ADC_REMOTE)}",
                )
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "gcp-auth.json").write_text(json.dumps({
                "authentication": "google_cloud_adc",
                "project": self._gcp_project,
                "location": self._gcp_location,
                "model": self.model_name,
                "cli_version": self._version,
            }, indent=2) + "\n")
            await super().run(instruction, environment, context)
        finally:
            await self.exec_as_root(
                environment, f"rm -f {shlex.quote(self._ADC_REMOTE)}",
            )
