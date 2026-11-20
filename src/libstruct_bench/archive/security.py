"""Stage immutable bytes outside run trees and screen potentially credential-bearing artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re

from .storage import CHUNK, canonical, sha256

SECRET_FIELD = re.compile(
    r"password|passwd|secret|credential|api.?key|authorization|cookie|private.?key|(?:api|access|refresh|oauth|auth|session|identity)[_.-]?token|^(?:token|auth|mongodb_uri|r2_access_key_id|aws_access_key_id)$|(?:^|[_-])token$",
    re.I,
)
SECRET_NAME = re.compile(
    r"(?:^\.env(?:\.|$)|auth\.json$|credentials?(?:\.|$)|token(?:\.|$)|\.pem$|\.key$|^id_(?:rsa|ed25519))",
    re.I,
)
WIRE = re.compile(
    rb"sk-(?:[a-zA-Z0-9_-]{20,})|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[\w]{20,}|AIza[\w-]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|hf_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{12,}\.eyJ|-----BEGIN [A-Z ]*PRIVATE KEY|mongodb(?:\+srv)?://[^/\s]{1,256}@|(?i:bearer\s+[a-z0-9._/+~-]{8,}|(?:api[_-]?(?:key|token)|auth|secret|access[_-]?token|session[_-]?token|refresh[_-]?token|\btoken\b|credential(?:s)?|private[_-]?key|passwd|secret[_-]?key|password|client[_-]?secret|refresh[_-]?token|authorization|set-cookie|cookie|oauth[_-]?token|secret[_-]?access[_-]?key)[\\\"' ]{0,6}[:=][\\\"' ]{0,6}(?!null\b|false\b|\$\{|\[REDACTED\])[^\s,'\"}\\]{3,})"
)
ENCODED = re.compile(rb"[A-Za-z0-9+/]{512,}={0,2}")
TEXT = {
    ".txt",
    ".log",
    ".md",
    ".json",
    ".jsonl",
    ".toml",
    ".csv",
    ".yaml",
    ".yml",
    ".tsv",
    ".py",
    ".sh",
    ".js",
    ".r",
    ".sql",
}
SAFE_CONFIGS = {"config.json", "lock.json", "result.json", "harbor.json"}


def scrub_settings(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if SECRET_FIELD.search(k) and v not in (None, "", [], {})
            else scrub_settings(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub_settings(item) for item in value]
    return value


def environment_secrets():
    return tuple(
        v.encode()
        for k, v in os.environ.items()
        if SECRET_FIELD.search(k) and len(v) >= 8
    )


def screen(path, secrets, scan_encoded=True):
    overlap = max([1024] + [len(s) for s in secrets])
    tail = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            data = tail + block
            if b"PRIVATE KEY-----" in data:
                return "private-key-material"
            if b"\0" in data or WIRE.search(data) or any(s in data for s in secrets):
                return "sensitive-content"
            if scan_encoded and ENCODED.search(data):
                return "opaque-encoded-content"
            tail = data[-overlap:]
    return None


def clean_text(text, secrets):
    raw = text.encode("utf-8")
    if WIRE.search(raw) or any(secret in raw for secret in secrets):
        # A credential can contain spaces and an Authorization header can carry
        # a scheme followed by a value. Redact the entire string/line.
        return "[REDACTED]" + ("\n" if text.endswith("\n") else "")
    return ENCODED.sub(b"[ENCODED PAYLOAD REMOVED]", raw).decode("utf-8")


def clean_value(value, secrets):
    if isinstance(value, dict):
        return {
            clean_text(k, secrets): "[REDACTED]"
            if SECRET_FIELD.search(k)
            else clean_value(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean_value(v, secrets) for v in value]
    return clean_text(value, secrets) if isinstance(value, str) else value


def sanitize_trace(path, suffix, secrets):
    # Whole structured records retain valid JSON even when nested tool output
    # contains escaped shell commands or image data. Oversized records stay local.
    changed = path.with_suffix(".clean")
    try:
        if suffix == ".json":
            if path.stat().st_size > 64 * CHUNK:
                return False
            value = json.loads(path.read_bytes())
            changed.write_bytes(canonical(clean_value(value, secrets)))
        else:
            with path.open("rb") as inp, changed.open("wb") as out:
                while line := inp.readline(64 * CHUNK + 1):
                    if len(line) > 64 * CHUNK or b"\0" in line:
                        return False
                    if suffix == ".jsonl" and line.strip():
                        out.write(
                            canonical(clean_value(json.loads(line), secrets)) + b"\n"
                        )
                    else:
                        out.write(clean_text(line.decode("utf-8"), secrets).encode())
        changed.replace(path)
        return True
    except (ValueError, UnicodeError, RecursionError):
        return False


def prepare(path, dest, role, secrets):
    """Return inventory of original and export bytes. Only staged bytes may be uploaded."""
    before = path.stat()
    original = hashlib.sha256()
    with path.open("rb") as src, dest.open("wb") as out:
        for block in iter(lambda: src.read(CHUNK), b""):
            original.update(block)
            out.write(block)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("source changed while staging")
    record = {"original_sha256": original.hexdigest(), "original_size": before.st_size}
    reason = None
    if any(
        SECRET_NAME.search(part)
        or part
        in {
            ".ssh",
            ".aws",
            ".gnupg",
            ".netrc",
            ".git-credentials",
            ".git",
            "credentials.db",
        }
        for part in path.parts
    ):
        reason = "credential-file"
    elif role == "source":
        # Only byte-for-byte, hash-pinned primary documents reach this role.
        pass
    elif path.suffix.lower() not in TEXT:
        reason = "unrecognized-format"
    else:
        if path.name in SAFE_CONFIGS and before.st_size <= 32 * CHUNK:
            try:
                value = json.loads(dest.read_bytes())
                clean = scrub_settings(value)
                if clean != value:
                    dest.write_bytes(canonical(clean))
            except (ValueError, RecursionError):
                reason = "unreadable-configuration"
        reason = reason or screen(dest, secrets)
        if reason in {"sensitive-content", "opaque-encoded-content"} and role in {
            "trajectory",
            "log",
            "configuration",
            "artifact",
        }:
            if sanitize_trace(dest, path.suffix.lower(), secrets):
                reason = screen(dest, secrets)
    if reason:
        return {**record, "status": "withheld", "reason": reason}
    export_hash = sha256(dest)
    return {
        **record,
        "sha256": export_hash,
        "size": dest.stat().st_size,
        "status": "original" if export_hash == original.hexdigest() else "sanitized",
    }
