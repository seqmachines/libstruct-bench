from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


def download_hf_dataset_file(
    *,
    repo_id: str,
    path: str,
    revision: str = "main",
    token: str | None = None,
    timeout_sec: float = 60.0,
) -> bytes:
    if not repo_id:
        raise ValueError("repo_id is required")
    if not path:
        raise ValueError("path is required")

    quoted_repo = urllib.parse.quote(repo_id.strip().removeprefix("datasets/"), safe="/")
    quoted_revision = urllib.parse.quote(revision or "main", safe="")
    quoted_path = urllib.parse.quote(path.lstrip("/"), safe="/")
    url = f"https://huggingface.co/datasets/{quoted_repo}/resolve/{quoted_revision}/{quoted_path}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return response.read()


def load_hf_json(
    *,
    repo_id: str,
    path: str,
    revision: str = "main",
    token: str | None = None,
    timeout_sec: float = 60.0,
) -> Any:
    data = download_hf_dataset_file(
        repo_id=repo_id,
        path=path,
        revision=revision,
        token=token,
        timeout_sec=timeout_sec,
    )
    return json.loads(data.decode("utf-8"))


def env_token(name: str = "HF_TOKEN") -> str | None:
    value = os.environ.get(name)
    return value if value else None
