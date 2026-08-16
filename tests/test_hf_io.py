from __future__ import annotations

import urllib.error

import pytest

from libstruct_bench.hf_io import download_hf_dataset_file


class _Response:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.data


def test_hf_download_retries_transient_dns_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def urlopen(*args: object, **kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary DNS failure")
        return _Response(b"frozen ground truth")

    sleeps: list[float] = []
    monkeypatch.setattr("libstruct_bench.hf_io.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("libstruct_bench.hf_io.time.sleep", sleeps.append)

    assert (
        download_hf_dataset_file(
            repo_id="org/private-groundtruth",
            path="protocol/groundtruth.json",
            revision="a" * 40,
            token="fixture-token",
            max_attempts=3,
            retry_delay_sec=0.25,
        )
        == b"frozen ground truth"
    )
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_hf_download_does_not_retry_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def urlopen(*args: object, **kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            url="https://huggingface.co/private",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("libstruct_bench.hf_io.urllib.request.urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        download_hf_dataset_file(
            repo_id="org/private-groundtruth",
            path="protocol/groundtruth.json",
            token="bad-token",
        )
    assert attempts == 1
