"""Pinned Antigravity account-auth adapter for the original libgen repeats."""

from __future__ import annotations

import json
import re
import shlex

from harbor.agents.installed.antigravity_cli import AntigravityCli


PINNED_VERSION = "1.1.13"
# Official release metadata retained in the 2026-10-09 installation preflight.
RELEASE_BASE = (
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
    "1.1.13-6057583128215552"
)
RELEASES = {
    "arm64": {
        "url": f"{RELEASE_BASE}/linux-arm/cli_linux_arm64.tar.gz",
        "sha512": "d2f3642873e3283265ebf4d4e2cc8c3652dff5a0b193a33ebfe0dded68521dd02b4c28ed92ff54784f6a6267e95c807b4d8194e8a6d619d4348638ab5c810d44",
    },
    "amd64": {
        "url": f"{RELEASE_BASE}/linux-x64/cli_linux_x64.tar.gz",
        "sha512": "89c6881b6c1999cb8236e7181c2192ae8f372b0413396c0f7bcff83d27ac9c0cc1202795cc0d629ec1ecbf4937d1c294cf4f5e4f9f8e05b1e972e27198313442",
    },
}

_INSTALL_SCRIPT = r'''
import hashlib, json, platform, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path
releases = json.loads(sys.argv[1])
arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine())
if arch not in releases:
    raise RuntimeError("Unsupported Antigravity architecture: " + platform.machine())
release = releases[arch]
destination = Path(sys.argv[2]) if sys.argv[2] else Path.home() / ".local/bin/agy"
with tempfile.TemporaryDirectory(prefix="agy-pinned-") as tmp:
    archive = Path(tmp) / "agy.tar.gz"
    subprocess.run(["curl", "--compressed", "--retry", "3", "--retry-delay", "2", "-fsSL", release["url"], "-o", str(archive)], check=True)
    if hashlib.sha512(archive.read_bytes()).hexdigest() != release["sha512"]:
        raise RuntimeError("Antigravity archive checksum mismatch; refusing installation")
    # Extract only the binary; no archive paths are trusted as destinations.
    with tarfile.open(archive, "r:gz") as bundle:
        member = bundle.getmember("antigravity")
        if not member.isfile():
            raise RuntimeError("Antigravity archive does not contain a regular binary")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with bundle.extractfile(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)
    destination.chmod(0o755)
print("Installed pinned Antigravity CLI archive for " + arch)
'''


def build_release_install_command(releases: dict, destination: str = "") -> str:
    """Build the checksum-verified installer shared by the pinned adapters."""
    return "python -c " + shlex.quote(_INSTALL_SCRIPT) + " " + shlex.quote(
        json.dumps(releases)
    ) + " " + shlex.quote(destination)


def build_install_command(version: str, destination: str = "") -> str:
    if version != PINNED_VERSION:
        raise ValueError(f"This adapter pins CLI {PINNED_VERSION}; got {version!r}")
    return build_release_install_command(RELEASES, destination)


class AntigravityPinned(AntigravityCli):
    """Retain Harbor's native harness and account auth, with a fixed binary."""

    _CLOUD_AUTH = frozenset({
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_CLOUD_LOCATION", "GCLOUD_PROJECT",
    })
    _PINNED_ENV = {
        "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
        "AGY_ADC_AUTH": "false",
    }

    def __init__(self, *args, version: str = PINNED_VERSION, **kwargs):
        build_install_command(version)
        super().__init__(*args, version=version, **kwargs)
        if self.extra_env:
            raise ValueError("Set account auth in the host shell; leave agent env empty")

    def _get_env(self, key: str) -> str | None:
        if key in self._CLOUD_AUTH:
            return None
        return self._PINNED_ENV.get(key) or super()._get_env(key)

    async def exec_as_agent(self, environment, command, env=None,
                            cwd=None, timeout_sec=None):
        # Do not put boolean values in extra_env: Harbor scrubs those values
        # from prediction artifacts as though they were credentials.
        account_env = {k: v for k, v in (env or {}).items() if k not in self._CLOUD_AUTH}
        return await super().exec_as_agent(
            environment, command, env={**account_env, **self._PINNED_ENV},
            cwd=cwd, timeout_sec=timeout_sec,
        )

    async def install(self, environment):
        if self._resolve_auth_token_path() is None:
            raise ValueError(
                "Account OAuth is required for the original libgen baseline. "
                "Set AGY_FORCE_AUTH_JSON=1 or AGY_AUTH_JSON_PATH in the host shell."
            )
        await self.exec_as_root(
            environment, "apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(environment, build_install_command(self._version))
        result = await self.exec_as_agent(environment, self.get_version_command())
        match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout or "")
        actual = match.group(0) if match else None
        if actual != self._version:
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
            "authentication": "account_oauth",
            "releases": RELEASES,
        }, indent=2) + "\n")
        settings_command, _ = self._build_settings_command()
        await self.exec_as_agent(environment, settings_command)
        await self._seed_oauth_token(environment)
