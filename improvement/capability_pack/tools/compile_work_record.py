#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from work_record import (
    WorkRecordError,
    canonical_json_bytes,
    load_object,
    sha256_bytes,
    validate_work_record,
)


RESULT_SCHEMA_VERSION = "libstruct.libgen_capability_compile_result.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile canonical T2/T3 predictions from a capability work record."
    )
    parser.add_argument("--work-record", required=True)
    parser.add_argument("--t2-out", required=True)
    parser.add_argument("--t3-out", required=True)
    return parser


def _stage(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    with handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _commit_pair(staged: list[Path], outputs: list[Path]) -> None:
    """Commit two staged files as one rollback-safe filesystem transaction."""

    backups: list[Path | None] = []
    committed = 0
    try:
        for output in outputs:
            if output.exists():
                handle = tempfile.NamedTemporaryFile(
                    prefix=f".{output.name}.backup.", dir=output.parent, delete=False
                )
                backup = Path(handle.name)
                handle.close()
                backup.unlink()
                output.replace(backup)
                backups.append(backup)
            else:
                backups.append(None)
        for source, output in zip(staged, outputs):
            source.replace(output)
            committed += 1
    except BaseException:
        for output in outputs[:committed]:
            output.unlink(missing_ok=True)
        for backup, output in zip(backups, outputs):
            if backup is not None and backup.exists():
                backup.replace(output)
        raise
    finally:
        for backup in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)


def _result(status: str, **values: Any) -> dict[str, Any]:
    return {"schema_version": RESULT_SCHEMA_VERSION, "status": status, **values}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    staged: list[Path] = []
    try:
        record = load_object(args.work_record, "work record")
        validate_work_record(record)
        t2_path = Path(args.t2_out)
        t3_path = Path(args.t3_out)
        if t2_path.resolve() == t3_path.resolve():
            raise WorkRecordError("T2 and T3 output paths must differ")
        if t2_path.is_symlink() or t3_path.is_symlink():
            raise WorkRecordError("prediction output paths may not be symlinks")
        t2_bytes = canonical_json_bytes(record["drafts"]["t2"])
        t3_bytes = canonical_json_bytes(record["drafts"]["t3"])
        staged = [_stage(t2_path, t2_bytes), _stage(t3_path, t3_bytes)]
        _commit_pair(staged, [t2_path, t3_path])
        staged = []
        print(
            json.dumps(
                _result(
                    "pass",
                    outputs={
                        "t2": {"path": t2_path.as_posix(), "sha256": sha256_bytes(t2_bytes)},
                        "t3": {"path": t3_path.as_posix(), "sha256": sha256_bytes(t3_bytes)},
                    },
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, WorkRecordError, KeyError, TypeError, ValueError) as error:
        for path in staged:
            path.unlink(missing_ok=True)
        print(
            json.dumps(
                _result("error", code="invalid_work_record", error=str(error)),
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
