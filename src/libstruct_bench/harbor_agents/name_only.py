"""Native Harbor agents restricted for the protocol-name-only control.

No inference implementation is replaced: installation, authentication, native
agent loops and trajectories are inherited. Only tool availability and the
pre-inference isolation checks differ from the document-grounded condition.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

from harbor.agents.installed.codex import Codex
from harbor.agents.installed.claude_code import ClaudeCode
from .antigravity_gcp import AntigravityGcp


# CLI 1.3.2's custom-agent registry does not resolve command_status or
# send_command_input, even though its init event advertises those names.
AGY_PROFILE = """---
name: libgen-name-only
description: Write and validate structured outputs using local tools only.
mainAgent: true
subagent: false
inheritMcp: false
tools:
  - run_command
  - view_file
  - write_to_file
  - replace_file_content
  - multi_replace_file_content
---
Follow the supplied task and /workspace/rules.md. Use local tools only to read
the generic schemas and rules and to construct, write, and validate the two
structured outputs. Do not access external knowledge, other agents, or APIs.
"""

# Feature names accepted by the pinned Codex CLI. Shell/file tools remain.
CODEX_DISABLED = (
    "apps", "remote_plugin", "plugins", "multi_agent", "memories",
    "browser_use", "browser_use_external", "computer_use", "image_generation",
    "in_app_browser", "hooks", "multi_agent_v2",
    "skill_search", "skill_mcp_dependency_install", "workspace_dependencies",
    "tool_suggest", "recommended_plugins", "standalone_web_search",
)

SANDBOX_CHECK = r'''
import hashlib, json, socket
from pathlib import Path
inventory = json.loads(Path('/workspace/name_only_inventory.json').read_text())
for relative, expected in inventory.items():
    path = Path('/workspace') / relative
    assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
assert not Path('/workspace/input').exists()
assert not Path('/tests').exists()
assert not Path('/var/run/docker.sock').exists()
def connect(host):
    with socket.create_connection(('provider-egress', 3128), 15) as s:
        s.settimeout(40)
        s.sendall(('CONNECT '+host+':443 HTTP/1.1\r\nHost: '+host+':443\r\n\r\n').encode())
        return s.recv(4096).split(b'\r\n', 1)[0].decode()
# Switch off installation destinations BEFORE giving the model its first turn.
# CONNECT opens TLS transport only: it sends no prompt or authenticated API call.
provider = connect(PROVIDER_HOST)
assert ' 200 ' in provider, provider
blocked = {h: connect(h) for h in ('huggingface.co', 'example.com', 'github.com',
                                  'storage.googleapis.com', 'deb.debian.org')}
assert all(' 403 ' in status for status in blocked.values()), blocked
try:
    s = socket.create_connection(('1.1.1.1', 443), 3)
except OSError:
    direct_blocked = True
else:
    s.close()
    direct_blocked = False
assert direct_blocked, 'unproxied external TCP is reachable'
print(json.dumps({'ready': True, 'provider_connect': provider, 'blocked': blocked,
                  'direct_tcp_blocked': direct_blocked, 'workspace_hashes': inventory}))
'''


def validate_lock(path: Path) -> dict:
    lock = json.loads(path.read_text())
    root = path.parent
    for relative, digest in lock["generated_files"].items():
        p = root / relative
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Name-only experiment file changed or missing: {p}")
    for filename, digest in lock.get("controller_files", {}).items():
        p = Path(filename)
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Name-only controller changed or missing: {p}")
    return lock


class NameOnlyMixin:
    provider_host: str
    expected_model: str
    expected_version: str
    expected_effort = "max"

    def __init__(self, *args, experiment_lock: str, **kwargs):
        self._name_only_lock_path = Path(experiment_lock).resolve()
        self._name_only_lock = validate_lock(self._name_only_lock_path)
        if kwargs.get("version") != self.expected_version:
            raise ValueError("Name-only CLI version does not match the locked baseline")
        if kwargs.get("model_name") != self.expected_model:
            raise ValueError("Name-only model does not match the locked baseline")
        if kwargs.get("reasoning_effort") != self.expected_effort:
            raise ValueError("Name-only effort does not match the locked baseline")
        if kwargs.get("resume_trajectory") or kwargs.get("load_trajectory"):
            raise ValueError("Name-only trials must start fresh")
        if kwargs.get("mcp_servers") or kwargs.get("skills_dir"):
            raise ValueError("Name-only trials cannot load MCP servers or skills")
        super().__init__(*args, **kwargs)

    async def install(self, environment):
        await super().install(environment)
        result = await self.exec_as_agent(environment, self.get_version_command())
        match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout or "")
        if result.return_code or not match or match.group(0) != self.expected_version:
            raise RuntimeError(f"Name-only CLI installation must be {self.expected_version}")

    async def run(self, instruction, environment, context):
        check = "PROVIDER_HOST = " + repr(self.provider_host) + "\n" + SANDBOX_CHECK
        result = await self.exec_as_agent(environment, "python -c " + shlex.quote(check))
        if result.return_code != 0:
            raise RuntimeError("Name-only isolation preflight failed: " + (result.stderr or result.stdout or ""))
        receipt = json.loads(result.stdout.strip().splitlines()[-1])
        receipt.update(condition="protocol-name-only (parametric-knowledge control)",
                       experiment_lock_sha256=hashlib.sha256(self._name_only_lock_path.read_bytes()).hexdigest(),
                       adapter=type(self).__name__)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "name-only-isolation.json").write_text(json.dumps(receipt, indent=2) + "\n")
        await super().run(instruction, environment, context)


class CodexExecutionHostError(RuntimeError):
    """The native Codex local-tool runtime could not execute the task."""


class CodexNameOnly(NameOnlyMixin, Codex):
    provider_host = "chatgpt.com"
    expected_model = "gpt-5.6-sol"
    expected_version = "0.147.0"

    def build_cli_flags(self):
        # Code Mode dispatches local shell/file tools for the pinned model.
        # Disabling its host leaves those tools advertised but unusable.
        return (super().build_cli_flags()
                + ' -c web_search="disabled" -c features.code_mode_host=true '
                + " ".join(f"-c features.{feature}=false" for feature in CODEX_DISABLED))

    def populate_context_post_run(self, context):
        super().populate_context_post_run(context)
        # Harbor calls this again during output recovery if context is empty.
        # Report once so recovery can still preserve artifacts and finish.
        if getattr(self, "_host_failure_reported", False):
            return
        # Harbor's native adapter pipes the CLI through tee, so a CLI/tool-host
        # failure can otherwise look like a successful, zero-scoring attempt.
        log = self.logs_dir / self._OUTPUT_FILENAME
        if log.is_file():
            for line in log.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item", {}) if isinstance(event, dict) else {}
                if (isinstance(item, dict) and item.get("type") == "error"
                        and "code mode is unavailable" in str(item.get("message", "")).lower()):
                    self._host_failure_reported = True
                    raise CodexExecutionHostError("Codex local execution unavailable: " + item["message"])


class ClaudeNameOnly(NameOnlyMixin, ClaudeCode):
    provider_host = "api.anthropic.com"
    expected_model = "claude-opus-5"
    expected_version = "2.1.233"

    def build_cli_flags(self):
        return (super().build_cli_flags()
                + " --tools Bash,Read,Write,Edit --disable-slash-commands"
                + " --safe-mode --no-chrome"
                + " --strict-mcp-config --mcp-config " + shlex.quote('{"mcpServers":{}}'))


class AntigravityNameOnly(NameOnlyMixin, AntigravityGcp):
    provider_host = "aiplatform.googleapis.com"
    expected_model = "google/gemini-3.7-flash-high"
    expected_version = "1.3.2"
    expected_effort = "high"

    def build_cli_flags(self):
        return super().build_cli_flags() + " --agent libgen-name-only --disable-slash-commands"

    async def install(self, environment):
        await super().install(environment)
        # The pinned CLI's offline `agent` command discovers its container-local
        # global directory; workspace discovery requires an initialized project.
        # No host Gemini configuration is copied into this fresh home.
        command = ("mkdir -p ~/.gemini/config/agents/libgen-name-only && printf %s "
                   + shlex.quote(AGY_PROFILE)
                   + " > ~/.gemini/config/agents/libgen-name-only/agent.md")
        result = await self.exec_as_agent(environment, command)
        if result.return_code != 0:
            raise RuntimeError("Could not install restricted Antigravity tool profile")
        listed = await self.exec_as_agent(environment, "~/.local/bin/agy agent")
        if listed.return_code or "libgen-name-only" not in (listed.stdout or "").splitlines():
            raise RuntimeError("Antigravity did not discover the restricted tool profile; stopping before inference")
