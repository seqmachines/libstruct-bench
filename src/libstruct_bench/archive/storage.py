"""Immutable object storage. Keys and checksums have identical semantics locally and on R2."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

CHUNK = 1024 * 1024
PART = 16 * CHUNK


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stream_hash(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(CHUNK), b""):
        h.update(block)
    return h.hexdigest()


def sha256(path):
    with path.open("rb") as stream:
        return stream_hash(stream)


def key_path(key):
    path = PurePosixPath(key)
    if (
        not key
        or path.is_absolute()
        or any(p in ("", ".", "..") for p in key.split("/"))
        or "\\" in key
        or "\0" in key
    ):
        raise ValueError("unsafe archive path")
    return path


def files(root):
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink())
        for name in sorted(names):
            p = base / name
            if not p.is_symlink() and p.is_file():
                yield p


class IntegrityError(ValueError):
    pass


class Store:
    def verify(self, key, expected):
        with closing(self.open(key)) as stream:
            if stream_hash(stream) != expected:
                raise IntegrityError("object checksum mismatch: " + key)

    def upload(self, key, path, expected=None):
        expected = expected or sha256(path)
        key_path(key)
        if not self.exists(key):
            self.create(key, path, expected)
        self.verify(key, expected)
        return expected

    def upload_directory(self, prefix, directory):
        return {
            p.relative_to(directory).as_posix(): self.upload(
                prefix + "/" + p.relative_to(directory).as_posix(), p
            )
            for p in files(directory)
        }

    def download(self, key, destination, expected):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ValueError("symlink at restore destination")
        if destination.exists():
            if sha256(destination) != expected:
                raise FileExistsError(destination)
            return
        with NamedTemporaryFile(dir=destination.parent) as temp:
            with closing(self.open(key)) as source:
                for block in iter(lambda: source.read(CHUNK), b""):
                    temp.write(block)
            temp.flush()
            if sha256(Path(temp.name)) != expected:
                raise IntegrityError("download checksum mismatch: " + key)
            try:
                os.link(temp.name, destination)
            except FileExistsError:
                if destination.is_symlink() or sha256(destination) != expected:
                    raise


class LocalStore(Store):
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, key):
        path = self.root / key_path(key)
        if path.resolve() != path:
            raise ValueError("symlink in local object path")
        return path

    def list(self, prefix=""):
        if prefix:
            key_path(prefix.rstrip("/"))
        return (
            p.relative_to(self.root).as_posix()
            for p in files(self.root)
            if p.relative_to(self.root).as_posix().startswith(prefix)
        )

    def open(self, key):
        return self.path(key).open("rb")

    def exists(self, key):
        return self.path(key).is_file()

    def create(self, key, path, expected):
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=dest.parent) as temp, path.open("rb") as src:
            for block in iter(lambda: src.read(CHUNK), b""):
                temp.write(block)
            temp.flush()
            if sha256(Path(temp.name)) != expected:
                raise IntegrityError("file changed before upload")
            try:
                os.link(temp.name, dest)
            except FileExistsError:
                pass


@contextmanager
def object_lock(path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


class R2Store(Store):
    """The client is injected so offline tests can use the same S3 calls."""

    def __init__(self, client, bucket, journal, part_size=PART):
        self.client = client
        self.bucket = bucket
        self.journal = Path(journal) / digest(
            canonical([client.meta.endpoint_url, bucket])
        )
        self.part_size = part_size

    def args(self, key):
        key_path(key)
        return {"Bucket": self.bucket, "Key": key}

    def list(self, prefix=""):
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        )
        return (item["Key"] for page in pages for item in page.get("Contents", []))

    def open(self, key):
        return self.client.get_object(**self.args(key))["Body"]

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(**self.args(key))
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                return False
            raise
        return True

    def create(self, key, path, expected):
        # A source can be rewritten between hashing and upload. Publish only
        # the verified, private snapshot, including when resuming multipart.
        with NamedTemporaryFile() as temp, path.open("rb") as src:
            hasher = hashlib.sha256()
            for block in iter(lambda: src.read(CHUNK), b""):
                hasher.update(block)
                temp.write(block)
            if hasher.hexdigest() != expected:
                raise IntegrityError("file changed before upload")
            temp.flush()
            self._create_snapshot(key, Path(temp.name), expected)

    def _create_snapshot(self, key, path, expected):
        from botocore.exceptions import ClientError

        try:
            if path.stat().st_size > self.part_size:
                self._multipart(key, path, expected)
            else:
                with path.open("rb") as handle:
                    self.client.put_object(
                        **self.args(key), Body=handle, IfNoneMatch="*"
                    )
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise

    def _multipart(self, key, path, expected):
        args = self.args(key)
        size = max(self.part_size, (path.stat().st_size + 9999) // 10000)
        marker = self.journal / digest(canonical([key, expected, size]))
        with object_lock(marker.with_suffix(".lock")):
            if self.exists(key):
                return
            parts = {}
            upload_id = marker.read_text() if marker.exists() else None
            if upload_id:
                from botocore.exceptions import ClientError

                try:
                    pages = self.client.get_paginator("list_parts").paginate(
                        **args, UploadId=upload_id
                    )
                    for page in pages:
                        parts.update(
                            {p["PartNumber"]: p["ETag"] for p in page.get("Parts", [])}
                        )
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "NoSuchUpload":
                        raise
                    upload_id = None
                    parts.clear()
            if not upload_id:
                upload_id = self.client.create_multipart_upload(**args)["UploadId"]
                tmp = marker.with_suffix(".tmp")
                tmp.write_text(upload_id)
                tmp.replace(marker)
            completed = []
            with path.open("rb") as handle:
                for number, block in enumerate(iter(lambda: handle.read(size), b""), 1):
                    etag = parts.get(number)
                    if not etag:
                        etag = self.client.upload_part(
                            **args, UploadId=upload_id, PartNumber=number, Body=block
                        )["ETag"]
                    completed.append({"PartNumber": number, "ETag": etag})
            self.client.complete_multipart_upload(
                **args,
                UploadId=upload_id,
                MultipartUpload={"Parts": completed},
                IfNoneMatch="*",
            )
            marker.unlink()

    def _retry_stream(self, operation, *args):
        from botocore.exceptions import (
            ConnectionError,
            HTTPClientError,
            ResponseStreamingError,
            IncompleteReadError,
        )

        for attempt in range(3):
            try:
                return operation(*args)
            except (
                ConnectionError,
                HTTPClientError,
                ResponseStreamingError,
                IncompleteReadError,
            ):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)

    def verify(self, key, expected):
        self._retry_stream(super().verify, key, expected)

    def download(self, key, destination, expected):
        self._retry_stream(super().download, key, destination, expected)
