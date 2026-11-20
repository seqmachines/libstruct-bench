from __future__ import annotations

import json
import os
import time
import urllib.error
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
    max_attempts: int = 3,
    retry_delay_sec: float = 0.5,
) -> bytes:
    if not repo_id:
        raise ValueError("repo_id is required")
    if not path:
        raise ValueError("path is required")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if retry_delay_sec < 0:
        raise ValueError("retry_delay_sec must be non-negative")

    quoted_repo = urllib.parse.quote(
        repo_id.strip().removeprefix("datasets/"), safe="/"
    )
    quoted_revision = urllib.parse.quote(revision or "main", safe="")
    quoted_path = urllib.parse.quote(path.lstrip("/"), safe="/")
    url = f"https://huggingface.co/datasets/{quoted_repo}/resolve/{quoted_revision}/{quoted_path}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in {408, 425, 429, 500, 502, 503, 504}:
                raise
            if attempt == max_attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == max_attempts:
                raise
        time.sleep(retry_delay_sec * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


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
