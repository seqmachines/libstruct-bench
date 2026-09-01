from __future__ import annotations

import hashlib
import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .artifacts import CapabilityImprovementError
from .isolation import validate_isolated_worker_workspace


_AUTH_TARGET = "/run/secrets/libgen-codex-auth.json"
_WORKSPACE_TARGET = "/workspace"
_WORKER_NETWORK = "agent-internal"
_PROVIDER_NETWORK = "provider-external"
_PROVIDER_HEALTHCHECK = {
    "test": [
        "CMD",
        "python3",
        "-c",
        "import socket; socket.create_connection(('127.0.0.1', 3128), 1).close()",
    ],
    "timeout": "2s",
    "interval": "2s",
    "retries": 30,
}
_FORBIDDEN_WORKSPACE_PATH_PARTS = frozenset(
    {
        "design",
        "final",
        "final-test",
        "final_test",
        "ground_truth",
        "groundtruth",
        "history",
        "validation_access_policy.json",
    }
)
_DOCKER_ENV_ALLOWLIST = frozenset(
    {
        "COMPOSE_PARALLEL_LIMIT",
        "DOCKER_CERT_PATH",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_DEFAULT_PLATFORM",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "HOME",
        "PATH",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    }
)


class IsolatedCodexRequest(Protocol):
    workspace: Path
    prompt_path: Path
    output_schema_path: Path
    draft_output_path: Path
    event_log_path: Path
    stderr_log_path: Path
    model: str
    version: str
    reasoning_effort: str
    codex_executable: str


@dataclass(frozen=True)
class IsolatedWorkerLaunch:
    command: tuple[str, ...]
    cleanup_command: tuple[str, ...]
    environment: Mapping[str, str]
    workspace: Path
    auth_file: Path
    compose_path: Path
    project_name: str


def prepare_isolated_codex_launch(
    request: IsolatedCodexRequest,
    *,
    environ: Mapping[str, str] | None = None,
) -> IsolatedWorkerLaunch:
    """Validate and describe a fail-closed Docker Compose Codex launch.

    The worker container receives only the staged workspace and the resolved
    local ``auth.json`` file.  The Docker CLI remains on the host; the Codex
    process itself has no host filesystem view and can reach the provider only
    through the exact-host CONNECT proxy.
    """

    values = dict(os.environ if environ is None else environ)
    if request.codex_executable != "codex":
        raise CapabilityImprovementError(
            "isolated capability workers use the image-pinned Codex executable; "
            "a host --codex-executable override is forbidden"
        )
    workspace = _validate_workspace_contract(request)
    auth_file = _resolve_auth_file(values)
    docker = _resolve_docker_executable(values)
    runtime_root = _runtime_root()
    compose_path = runtime_root / "docker-compose.yaml"
    proxy_path = (
        _repository_root() / "benchmarks/libgen/docker_network/provider_egress_proxy.py"
    )
    policy_path = runtime_root / "provider-api-policy.json"
    for path, label in (
        (compose_path, "worker Compose file"),
        (runtime_root / "Dockerfile", "worker Dockerfile"),
        (runtime_root / "container-run-worker.sh", "worker entrypoint"),
        (proxy_path, "provider egress proxy"),
        (policy_path, "provider-only network policy"),
    ):
        if path.is_symlink() or not path.is_file():
            raise CapabilityImprovementError(f"{label} is missing or symlinked: {path}")
    _validate_provider_policy(policy_path)

    digest = validate_isolated_worker_workspace(workspace)["workspace_digest"]
    project_name = (
        "libgen-capability-"
        + hashlib.sha256(
            f"{workspace.as_posix()}\0{digest}".encode("utf-8")
        ).hexdigest()[:16]
    )
    network_log_root = workspace / "outputs" / "network"
    network_log_root.mkdir(parents=True, exist_ok=True)

    child_env = {
        key: value
        for key, value in values.items()
        if key in _DOCKER_ENV_ALLOWLIST and value
    }
    child_env.update(
        {
            "LIBGEN_WORKSPACE": workspace.as_posix(),
            "LIBGEN_CANDIDATE_DIR": (workspace / "candidates").as_posix(),
            "LIBGEN_OUTPUT_DIR": (workspace / "outputs").as_posix(),
            "LIBGEN_CODEX_AUTH_FILE": auth_file.as_posix(),
            "LIBGEN_CODEX_FORCE_AUTH_JSON": "1",
            "LIBGEN_CODEX_MODEL": _required_scalar(request.model, "model"),
            "LIBGEN_CODEX_REASONING_EFFORT": _reasoning_effort(
                request.reasoning_effort
            ),
            "LIBGEN_CODEX_VERSION": _version(request.version),
            "LIBGEN_PROVIDER_PROXY": proxy_path.as_posix(),
            "LIBGEN_NETWORK_POLICY": policy_path.as_posix(),
            "LIBGEN_NETWORK_LOG_DIR": network_log_root.as_posix(),
            "LIBGEN_WORKER_UID": str(os.getuid()),
            "LIBGEN_WORKER_GID": str(os.getgid()),
        }
    )
    compose_prefix = (
        docker,
        "compose",
        "--ansi",
        "never",
        "--project-name",
        project_name,
        "--file",
        compose_path.as_posix(),
    )
    rendered = _render_compose(compose_prefix, child_env)
    validate_rendered_worker_compose(
        rendered,
        workspace=workspace,
        auth_file=auth_file,
        proxy_path=proxy_path,
        policy_path=policy_path,
        network_log_root=network_log_root,
        runtime_root=runtime_root,
        model=request.model,
        version=request.version,
        reasoning_effort=request.reasoning_effort,
    )
    return IsolatedWorkerLaunch(
        command=(*compose_prefix, "run", "--build", "--rm", "--no-TTY", "worker"),
        cleanup_command=(*compose_prefix, "down", "--remove-orphans", "--volumes"),
        environment=child_env,
        workspace=workspace,
        auth_file=auth_file,
        compose_path=compose_path,
        project_name=project_name,
    )


def validate_rendered_worker_compose(
    document: Mapping[str, Any],
    *,
    workspace: Path,
    auth_file: Path,
    proxy_path: Path,
    policy_path: Path,
    network_log_root: Path,
    runtime_root: Path,
    model: str,
    version: str,
    reasoning_effort: str,
) -> None:
    """Reject any Compose expansion that broadens the worker boundary."""

    services = document.get("services")
    if not isinstance(services, dict) or set(services) != {
        "worker",
        "provider-egress",
    }:
        raise CapabilityImprovementError(
            "isolated worker Compose must contain only worker and provider-egress"
        )
    networks = document.get("networks")
    if not isinstance(networks, dict) or set(networks) != {
        _WORKER_NETWORK,
        _PROVIDER_NETWORK,
    }:
        raise CapabilityImprovementError(
            "isolated worker Compose has unexpected networks"
        )
    if networks[_WORKER_NETWORK].get("internal") is not True:
        raise CapabilityImprovementError(
            "isolated worker network must be Docker-internal"
        )

    worker = services["worker"]
    _validate_service_hardening(worker, label="worker", expected_pids=512)
    if set(_service_networks(worker)) != {_WORKER_NETWORK}:
        raise CapabilityImprovementError(
            "Codex worker may join only the internal provider-proxy network"
        )
    _assert_no_namespace_escape(worker, label="worker")
    expected_worker_mounts = {
        (_resolved(workspace), _WORKSPACE_TARGET, True),
        (_resolved(workspace / "candidates"), "/workspace/candidates", False),
        (_resolved(workspace / "outputs"), "/workspace/outputs", False),
        (_resolved(auth_file), _AUTH_TARGET, True),
    }
    if _bind_mounts(worker) != expected_worker_mounts:
        raise CapabilityImprovementError(
            "Codex worker mounts must be exactly the read-only staged workspace, "
            "its two writable output roots, and read-only auth.json"
        )
    _validate_minimal_build(
        worker.get("build"),
        runtime_root=runtime_root,
        version=version,
        label="Codex worker",
    )
    if worker.get("entrypoint") is not None or worker.get("command") is not None:
        raise CapabilityImprovementError(
            "Codex worker may not override its image entrypoint or command"
        )
    if worker.get("depends_on") != {
        "provider-egress": {"condition": "service_healthy", "required": True}
    }:
        raise CapabilityImprovementError(
            "Codex worker must wait for the healthy provider proxy"
        )
    expected_environment = {
        "HOME": "/tmp/home",
        "CODEX_HOME": "/codex-home",
        "CODEX_FORCE_AUTH_JSON": "1",
        "LIBGEN_CODEX_MODEL": model,
        "LIBGEN_CODEX_REASONING_EFFORT": reasoning_effort,
        "LIBGEN_CODEX_VERSION": version,
        "HTTP_PROXY": "http://provider-egress:3128",
        "HTTPS_PROXY": "http://provider-egress:3128",
        "http_proxy": "http://provider-egress:3128",
        "https_proxy": "http://provider-egress:3128",
        "NO_PROXY": "localhost,127.0.0.1,provider-egress",
        "no_proxy": "localhost,127.0.0.1,provider-egress",
    }
    if worker.get("environment") != expected_environment:
        raise CapabilityImprovementError(
            "Codex worker environment contains unpinned or unexpected values"
        )
    expected_user = f"{os.getuid()}:{os.getgid()}"
    if worker.get("user") != expected_user:
        raise CapabilityImprovementError(
            "Codex worker must run as the invoking host UID/GID"
        )
    tmpfs = set(worker.get("tmpfs", []))
    if not any(str(value).startswith("/tmp:") for value in tmpfs) or not any(
        str(value).startswith("/codex-home:") for value in tmpfs
    ):
        raise CapabilityImprovementError(
            "Codex worker requires isolated temporary and ephemeral Codex-home filesystems"
        )

    proxy = services["provider-egress"]
    _validate_service_hardening(proxy, label="provider-egress", expected_pids=128)
    if set(_service_networks(proxy)) != {_WORKER_NETWORK, _PROVIDER_NETWORK}:
        raise CapabilityImprovementError(
            "provider proxy must bridge only the internal and provider networks"
        )
    _assert_no_namespace_escape(proxy, label="provider-egress")
    _validate_minimal_build(
        proxy.get("build"),
        runtime_root=runtime_root,
        version=version,
        label="provider proxy",
    )
    if (
        proxy.get("entrypoint")
        != [
            "python3",
            "/proxy/provider_egress_proxy.py",
            "/proxy/egress_policy.json",
        ]
        or proxy.get("command") is not None
    ):
        raise CapabilityImprovementError(
            "provider proxy entrypoint must be the checked exact-host proxy"
        )
    if proxy.get("healthcheck") != _PROVIDER_HEALTHCHECK:
        raise CapabilityImprovementError(
            "provider proxy must use the pinned Python 3 socket healthcheck"
        )
    expected_proxy_mounts = {
        (_resolved(proxy_path), "/proxy/provider_egress_proxy.py", True),
        (_resolved(policy_path), "/proxy/egress_policy.json", True),
        (_resolved(network_log_root), "/logs/network", False),
    }
    if _bind_mounts(proxy) != expected_proxy_mounts:
        raise CapabilityImprovementError(
            "provider proxy mounts do not match its exact code, policy, and log roots"
        )


def run_compose_cleanup(launch: IsolatedWorkerLaunch) -> None:
    try:
        subprocess.run(
            list(launch.cleanup_command),
            env=dict(launch.environment),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # Cleanup cannot recover or widen access. The dedicated project name
        # lets the next launch repeat the same cleanup before starting.
        pass


def validate_isolated_codex_outputs(request: IsolatedCodexRequest) -> None:
    """Reject link-based output tricks before host-side compilation."""

    workspace = request.workspace.expanduser().resolve()
    for path, label in (
        (request.draft_output_path, "draft output"),
        (request.event_log_path, "event log"),
        (request.stderr_log_path, "stderr log"),
    ):
        raw = path.expanduser()
        try:
            raw.relative_to(workspace)
        except ValueError as error:
            raise CapabilityImprovementError(
                f"isolated Codex {label} escaped the workspace"
            ) from error
        if raw.is_symlink() or (raw.exists() and not raw.is_file()):
            raise CapabilityImprovementError(
                f"isolated Codex {label} is not a regular workspace file: {raw}"
            )
        _reject_symlink_parents(raw, workspace=workspace, label=label)
    validate_isolated_worker_workspace(workspace)


def _validate_workspace_contract(request: IsolatedCodexRequest) -> Path:
    raw_workspace = request.workspace.expanduser()
    if raw_workspace.is_symlink() or not raw_workspace.is_dir():
        raise CapabilityImprovementError(
            f"isolated Codex workspace is missing or symlinked: {raw_workspace}"
        )
    workspace = raw_workspace.resolve()
    for relative in ("candidates", "outputs"):
        directory = workspace / relative
        if directory.is_symlink() or not directory.is_dir():
            raise CapabilityImprovementError(
                f"isolated Codex writable root is missing or symlinked: {directory}"
            )
    manifest = validate_isolated_worker_workspace(workspace)
    if manifest["mode"] == "human_review_console":
        raise CapabilityImprovementError(
            "the interactive human review console is not a Codex worker"
        )
    if manifest["host_paths_exposed"] is not False or manifest["network_policy"] != (
        "provider_api_only_no_web"
    ):
        raise CapabilityImprovementError(
            "workspace does not declare the isolated Codex boundary"
        )
    contract = manifest["agent_contract"]
    expected = {
        "prompt_path": request.prompt_path,
        "output_schema_path": request.output_schema_path,
        "draft_output_path": request.draft_output_path,
        "event_log_path": request.event_log_path,
    }
    for field, requested_path in expected.items():
        relative = contract[field]
        if not isinstance(relative, str):
            raise CapabilityImprovementError(
                f"workspace has no agent contract value for {field}"
            )
        contract_path = _workspace_path(workspace, relative, field)
        expected_top = (
            "inputs" if field in {"prompt_path", "output_schema_path"} else "outputs"
        )
        if Path(relative).parts[0] != expected_top:
            raise CapabilityImprovementError(
                f"staged {field} must remain under {expected_top}/"
            )
        requested = _request_path(
            workspace,
            requested_path,
            field,
            must_exist=field in {"prompt_path", "output_schema_path"},
        )
        if requested != contract_path:
            raise CapabilityImprovementError(
                f"Codex request does not match staged {field}"
            )
    stderr = _request_path(
        workspace,
        request.stderr_log_path,
        "stderr_log_path",
        must_exist=False,
    )
    if stderr.parent != workspace / "outputs":
        raise CapabilityImprovementError("Codex stderr log must stay under outputs/")
    for item in manifest["staged_files"]:
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts:
            raise CapabilityImprovementError(
                f"staged workspace path is unsafe: {item['path']}"
            )
        if _FORBIDDEN_WORKSPACE_PATH_PARTS.intersection(
            part.lower() for part in path.parts
        ):
            raise CapabilityImprovementError(
                "validation/final-test policy or experiment roots may not be staged "
                f"for Codex: {item['path']}"
            )
    return workspace


def _request_path(
    workspace: Path,
    path: Path,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    raw = path.expanduser()
    if raw.is_symlink():
        raise CapabilityImprovementError(f"staged {label} may not be a symlink")
    try:
        relative = raw.relative_to(workspace)
    except ValueError as error:
        raise CapabilityImprovementError(
            f"staged {label} is outside the isolated workspace"
        ) from error
    _reject_symlink_parents(raw, workspace=workspace, label=label)
    if must_exist and not raw.is_file():
        raise CapabilityImprovementError(f"staged {label} is missing: {raw}")
    if not must_exist and raw.exists() and not raw.is_file():
        raise CapabilityImprovementError(
            f"staged {label} is not a regular output file: {raw}"
        )
    return _workspace_path(workspace, relative.as_posix(), label)


def _reject_symlink_parents(path: Path, *, workspace: Path, label: str) -> None:
    current = path.parent
    while current != workspace:
        if current.is_symlink():
            raise CapabilityImprovementError(
                f"staged {label} has a symlinked parent: {current}"
            )
        if current == current.parent:
            raise CapabilityImprovementError(
                f"staged {label} is outside the isolated workspace"
            )
        current = current.parent


def _workspace_path(workspace: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise CapabilityImprovementError(f"unsafe staged {label}: {value}")
    path = (workspace / relative).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise CapabilityImprovementError(
            f"staged {label} escapes the isolated workspace"
        ) from error
    return path


def _resolve_auth_file(environ: Mapping[str, str]) -> Path:
    if environ.get("CODEX_FORCE_AUTH_JSON", "1") != "1":
        raise CapabilityImprovementError(
            "isolated capability learning requires CODEX_FORCE_AUTH_JSON=1"
        )
    explicit = environ.get("LIBGEN_CODEX_AUTH_FILE")
    if explicit:
        candidate = Path(explicit).expanduser()
    else:
        codex_home = environ.get("CODEX_HOME")
        candidate = (
            Path(codex_home).expanduser() / "auth.json"
            if codex_home
            else Path.home() / ".codex" / "auth.json"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CapabilityImprovementError(
            "local Codex auth.json is required for the isolated worker; run "
            "`codex login` or set LIBGEN_CODEX_AUTH_FILE"
        ) from error
    if not resolved.is_file():
        raise CapabilityImprovementError(
            f"local Codex auth.json is not a regular file: {resolved}"
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            "local Codex auth.json is not a readable JSON object"
        ) from error
    if not isinstance(value, dict) or not value:
        raise CapabilityImprovementError(
            "local Codex auth.json must be a non-empty JSON object"
        )
    return resolved


def _resolve_docker_executable(environ: Mapping[str, str]) -> str:
    requested = environ.get("LIBGEN_DOCKER_EXECUTABLE", "docker")
    if "/" in requested:
        path = Path(requested).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise CapabilityImprovementError(
                f"Docker executable is missing or not executable: {path}"
            )
        return path.as_posix()
    resolved = shutil.which(requested)
    if resolved is None:
        raise CapabilityImprovementError(
            "Docker Compose is required for read-isolated capability workers; "
            "no host Codex fallback is permitted"
        )
    return resolved


def _render_compose(
    compose_prefix: tuple[str, ...], environment: Mapping[str, str]
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [*compose_prefix, "config", "--format", "json"],
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CapabilityImprovementError(
            f"cannot render the isolated worker Compose contract: {error}"
        ) from error
    if completed.returncode != 0:
        raise CapabilityImprovementError(
            "Docker Compose could not render the isolated worker contract: "
            + completed.stderr.strip()
        )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CapabilityImprovementError(
            "Docker Compose returned an invalid worker contract"
        ) from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(
            "Docker Compose worker contract must be a JSON object"
        )
    return document


def _validate_provider_policy(path: Path) -> None:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read provider-only network policy: {error}"
        ) from error
    if (
        set(policy.get("provider_hosts", []))
        != {
            "api.openai.com",
            "auth.openai.com",
            "chatgpt.com",
        }
        or policy.get("setup_hosts") != []
    ):
        raise CapabilityImprovementError(
            "capability worker network policy must allow only OpenAI provider hosts"
        )


def _validate_service_hardening(
    service: Mapping[str, Any], *, label: str, expected_pids: int
) -> None:
    if service.get("read_only") is not True:
        raise CapabilityImprovementError(f"{label} root filesystem must be read-only")
    if set(service.get("cap_drop", [])) != {"ALL"}:
        raise CapabilityImprovementError(f"{label} must drop all Linux capabilities")
    if "no-new-privileges:true" not in set(service.get("security_opt", [])):
        raise CapabilityImprovementError(f"{label} must disable privilege escalation")
    if service.get("pids_limit") != expected_pids:
        raise CapabilityImprovementError(f"{label} has an unexpected PID limit")


def _assert_no_namespace_escape(service: Mapping[str, Any], *, label: str) -> None:
    forbidden = {
        "cgroup",
        "cgroup_parent",
        "configs",
        "credential_spec",
        "device_cgroup_rules",
        "devices",
        "dns",
        "dns_search",
        "extra_hosts",
        "group_add",
        "ipc",
        "links",
        "network_mode",
        "pid",
        "ports",
        "privileged",
        "runtime",
        "secrets",
        "sysctls",
        "userns_mode",
        "volumes_from",
    }
    present = sorted(
        key for key in forbidden if service.get(key) not in (None, False, [], {})
    )
    if present:
        raise CapabilityImprovementError(
            f"{label} uses forbidden host/privileged settings: {', '.join(present)}"
        )


def _service_networks(service: Mapping[str, Any]) -> tuple[str, ...]:
    value = service.get("networks", {})
    if isinstance(value, dict):
        return tuple(value)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _validate_minimal_build(
    value: Any, *, runtime_root: Path, version: str, label: str
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "context",
        "dockerfile",
        "args",
    }:
        raise CapabilityImprovementError(
            f"{label} build may use only context, Dockerfile, and pinned version"
        )
    if _resolved(Path(str(value["context"]))) != _resolved(runtime_root):
        raise CapabilityImprovementError(
            f"{label} build context must be the minimal worker_runtime directory"
        )
    if value["dockerfile"] != "Dockerfile":
        raise CapabilityImprovementError(f"{label} Dockerfile is unexpected")
    if value["args"] != {"CODEX_VERSION": version}:
        raise CapabilityImprovementError(
            f"{label} image version is not the experiment-pinned version"
        )


def _bind_mounts(service: Mapping[str, Any]) -> set[tuple[str, str, bool]]:
    mounts: set[tuple[str, str, bool]] = set()
    for item in service.get("volumes", []):
        if not isinstance(item, dict) or item.get("type") != "bind":
            raise CapabilityImprovementError(
                "isolated worker services may use only explicit bind mounts"
            )
        source = Path(str(item.get("source", "")))
        mounts.add(
            (
                _resolved(source),
                str(item.get("target", "")),
                item.get("read_only") is True,
            )
        )
    return mounts


def _required_scalar(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(char in value for char in ("\x00", "\n", "\r"))
    ):
        raise CapabilityImprovementError(f"invalid experiment-pinned {label}")
    return value.strip()


def _reasoning_effort(value: str) -> str:
    result = _required_scalar(value, "reasoning effort")
    if result not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        raise CapabilityImprovementError(
            f"unsupported experiment-pinned reasoning effort: {result}"
        )
    return result


def _version(value: str) -> str:
    result = _required_scalar(value, "Codex version")
    if not all(part.isdigit() for part in result.split(".")):
        raise CapabilityImprovementError(
            f"invalid experiment-pinned Codex version: {result}"
        )
    return result


def _resolved(path: Path) -> str:
    return path.expanduser().resolve().as_posix()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runtime_root() -> Path:
    return _repository_root() / "improvement" / "worker_runtime"


def standalone_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one staged capability Codex worker in its external boundary"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--experiment-manifest", required=True)
    parser.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--hard-timeout-seconds", type=float, default=7200.0)
    args = parser.parse_args(argv)

    from .experiment import validate_experiment_manifest
    from .local_learning import LocalCodexRunRequest, run_native_codex

    workspace = Path(args.workspace).expanduser().resolve()
    manifest = validate_isolated_worker_workspace(workspace)
    experiment_path = Path(args.experiment_manifest).expanduser().resolve()
    experiment = validate_experiment_manifest(
        experiment_path,
        experiment_root=experiment_path.parents[1],
    )
    if experiment["experiment_digest"] != manifest["experiment_digest"]:
        raise CapabilityImprovementError(
            "worker workspace and experiment manifest have different digests"
        )
    anchor = experiment["anchor"]
    contract = manifest["agent_contract"]
    mode = manifest["mode"]
    stderr_name = {
        "improvement_worker": "proposal.stderr.log",
        "revision_worker": "revision.stderr.log",
        "independent_critic": "critic.stderr.log",
    }.get(mode)
    if stderr_name is None:
        raise CapabilityImprovementError(
            f"workspace mode cannot run an agent worker: {mode}"
        )
    request = LocalCodexRunRequest(
        workspace=workspace,
        prompt_path=workspace / contract["prompt_path"],
        output_schema_path=workspace / contract["output_schema_path"],
        draft_output_path=workspace / contract["draft_output_path"],
        event_log_path=workspace / contract["event_log_path"],
        stderr_log_path=workspace / "outputs" / stderr_name,
        model=anchor["model"],
        version=anchor["agent_version"],
        reasoning_effort=anchor["reasoning_effort"],
        codex_executable="codex",
        idle_timeout_seconds=args.idle_timeout_seconds,
        hard_timeout_seconds=args.hard_timeout_seconds,
    )
    result = run_native_codex(request)
    print(
        json.dumps(
            {
                "completion_reason": result.completion_reason,
                "elapsed_seconds": result.elapsed_seconds,
                "returncode": result.returncode,
                "workspace": workspace.as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return (
        0
        if result.returncode == 0 or result.completion_reason == "idle_after_draft"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(standalone_main())
