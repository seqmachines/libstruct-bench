"""Small host-side hooks. Scoring and historical execution require no cloud libraries."""

import os


def enabled():
    return os.environ.get("LSB_ARCHIVE_MODE") in {"local", "r2"}


def isolated():
    return enabled() or any(
        k == "MONGODB_URI" or k.startswith("R2_") for k in os.environ
    )


def launcher():
    return ["libstruct-archive", "launch"] if isolated() else ["harbor", "run"]


def host_hook(job, *, starting=False):
    if enabled():
        from .runtime import hook

        hook(job, starting=starting)


def agent_env(env=None):
    return {
        k: v
        for k, v in (os.environ if env is None else env).items()
        if not k.startswith(("LSB_", "R2_"))
        and k not in {"MONGODB_URI", "LIBSTRUCT_DATA_ROOT"}
    }
