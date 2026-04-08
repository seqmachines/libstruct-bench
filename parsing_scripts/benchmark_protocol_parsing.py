#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_TIMEOUT_SECONDS = 360
DEFAULT_DELAY_SECONDS = 1.0

CHAR_REGION_MAP = {
    "B": "barcode",
    "U": "umi",
    "I": "index",
    "L": "ligation",
    "R": "rt_barcode",
    "T": "tn5_barcode",
    "X": "linker",
    "V": "capture",
}

TYPE_REGION_MAP = {
    "known": "known",
    "barcode": "barcode",
    "umi": "umi",
    "index": "index",
    "ligation": "ligation",
    "rt_barcode": "rt_barcode",
    "tn5_barcode": "tn5_barcode",
    "linker": "linker",
    "capture": "capture",
}


@dataclass
class ModelConfig:
    name: str
    model: str
    api: str


@dataclass
class ProtocolRecord:
    protocol_id: str
    protocol_name: str
    json_path: Path
    pdf_path: Path | None
    text_path: Path | None
    protocol_url: str | None
    ground_truth: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark cDNA library-structure extraction across models and "
            "protocol PDFs."
        )
    )
    parser.add_argument(
        "--models",
        type=Path,
        required=True,
        help="Path to models.json containing model entries with api URLs.",
    )
    parser.add_argument(
        "--protocol-dir",
        type=Path,
        default=Path("protocols"),
        help="Directory containing protocol ground-truth JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results"),
        help="Directory where result files are written.",
    )
    parser.add_argument(
        "--input-mode",
        choices=("pdf", "text", "url", "auto"),
        default="pdf",
        help=(
            "How to feed each protocol to cDNA. "
            "'pdf' benchmarks direct PDF parsing; 'auto' falls back to text/url."
        ),
    )
    parser.add_argument(
        "--protocol",
        action="append",
        default=[],
        help="Benchmark only the specified protocol_id. Can be repeated.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Benchmark only the first N selected protocols.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Delay in seconds between requests.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include cDNA raw model output in the JSON results.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_models(path: Path) -> list[ModelConfig]:
    payload = load_json(path)
    entries = payload.get("models") if isinstance(payload, dict) else payload

    if not isinstance(entries, list) or not entries:
        raise SystemExit(
            f"{path}: expected a non-empty list or an object with a 'models' list"
        )

    models: list[ModelConfig] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise SystemExit(f"{path}: model entry #{index} is not an object")

        model_id = entry.get("model") or entry.get("id")
        api = entry.get("api")
        name = entry.get("name") or model_id

        if not isinstance(model_id, str) or not model_id.strip():
            raise SystemExit(f"{path}: model entry #{index} is missing 'model'")
        if not isinstance(api, str) or not api.strip():
            raise SystemExit(f"{path}: model entry #{index} is missing 'api'")
        if not isinstance(name, str) or not name.strip():
            raise SystemExit(f"{path}: model entry #{index} has an invalid 'name'")

        models.append(ModelConfig(name=name.strip(), model=model_id.strip(), api=api.strip()))

    return models


def resolve_pdf_path(root: Path, references: dict[str, Any]) -> Path | None:
    protocol_pdf = references.get("protocol_pdf")
    if not isinstance(protocol_pdf, str) or not protocol_pdf.strip():
        return None

    candidate = Path(protocol_pdf)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def resolve_text_path(root: Path, protocol_id: str, pdf_path: Path | None) -> Path | None:
    candidates: list[Path] = []

    if pdf_path is not None:
        candidates.append(root / "protocol_texts" / f"{pdf_path.stem}.txt")

    candidates.append(root / "protocol_texts" / f"{protocol_id}.txt")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def load_protocols(root: Path, protocol_dir: Path) -> list[ProtocolRecord]:
    base_dir = protocol_dir if protocol_dir.is_absolute() else root / protocol_dir
    if not base_dir.exists():
        raise SystemExit(f"Protocol directory does not exist: {base_dir}")

    records: list[ProtocolRecord] = []
    for path in sorted(base_dir.glob("*.json")):
        data = load_json(path)
        if not isinstance(data, dict):
            continue

        protocol_id = data.get("protocol_id")
        protocol_name = data.get("protocol_name")
        references = data.get("references")

        if not isinstance(protocol_id, str) or not protocol_id.strip():
            protocol_id = path.stem
        if not isinstance(protocol_name, str) or not protocol_name.strip():
            protocol_name = protocol_id
        if not isinstance(references, dict):
            references = {}

        pdf_path = resolve_pdf_path(root, references)
        text_path = resolve_text_path(root, protocol_id, pdf_path)
        protocol_url = references.get("protocol_link")
        if not isinstance(protocol_url, str):
            protocol_url = None

        records.append(
            ProtocolRecord(
                protocol_id=protocol_id,
                protocol_name=protocol_name,
                json_path=path,
                pdf_path=pdf_path,
                text_path=text_path,
                protocol_url=protocol_url,
                ground_truth=data,
            )
        )

    return records


def filter_protocols(
    protocols: list[ProtocolRecord], selected_ids: list[str], limit: int | None
) -> list[ProtocolRecord]:
    wanted = {item.strip() for item in selected_ids if item.strip()}

    if wanted:
        available = {protocol.protocol_id for protocol in protocols}
        missing = sorted(wanted - available)
        if missing:
            raise SystemExit(f"Unknown protocol_id(s): {', '.join(missing)}")
        protocols = [protocol for protocol in protocols if protocol.protocol_id in wanted]

    if limit is not None:
        protocols = protocols[:limit]

    return protocols


def encode_multipart(
    fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    boundary = f"----librarystructdb-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for field_name, filename, content, media_type in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {media_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def build_request_payload(
    protocol: ProtocolRecord, input_mode: str
) -> tuple[list[tuple[str, str]], list[tuple[str, str, bytes, str]], str]:
    fields: list[tuple[str, str]] = []
    files: list[tuple[str, str, bytes, str]] = []

    def from_pdf() -> tuple[list[tuple[str, str]], list[tuple[str, str, bytes, str]], str]:
        if protocol.pdf_path is None or not protocol.pdf_path.exists():
            raise FileNotFoundError("missing protocol PDF")
        content = protocol.pdf_path.read_bytes()
        files_local = [
            ("file", protocol.pdf_path.name, content, "application/pdf"),
        ]
        return [], files_local, str(protocol.pdf_path)

    def from_text() -> tuple[list[tuple[str, str]], list[tuple[str, str, bytes, str]], str]:
        if protocol.text_path is None or not protocol.text_path.exists():
            raise FileNotFoundError("missing protocol text")
        text = protocol.text_path.read_text(encoding="utf-8")
        return [("text", text)], [], str(protocol.text_path)

    def from_url() -> tuple[list[tuple[str, str]], list[tuple[str, str, bytes, str]], str]:
        if not protocol.protocol_url:
            raise FileNotFoundError("missing protocol URL")
        return [("url", protocol.protocol_url)], [], protocol.protocol_url

    if input_mode == "pdf":
        return from_pdf()
    if input_mode == "text":
        return from_text()
    if input_mode == "url":
        return from_url()
    if input_mode == "auto":
        for loader in (from_pdf, from_text, from_url):
            try:
                return loader()
            except FileNotFoundError:
                continue
        raise FileNotFoundError("no usable input source found")

    raise ValueError(f"Unsupported input mode: {input_mode}")


def post_benchmark(
    model: ModelConfig,
    protocol: ProtocolRecord,
    input_mode: str,
    timeout: int,
) -> tuple[dict[str, Any], str]:
    fields, files, source_used = build_request_payload(protocol, input_mode)
    fields = [("model", model.model), *fields]

    body, boundary = encode_multipart(fields, files)
    req = request.Request(
        model.api,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from benchmark API: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Benchmark API returned a non-object JSON response")

    return parsed, source_used


def as_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    if len(left) < len(right):
        left, right = right, left

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    if left == right and left:
        return 1.0
    max_length = max(len(left), len(right))
    if max_length == 0:
        return 1.0
    return 1.0 - (levenshtein_distance(left, right) / max_length)


def canonical_region(segment: dict[str, Any] | None) -> str:
    if not segment:
        return "missing"

    char = as_string(segment.get("char")).strip().upper()
    if char in CHAR_REGION_MAP:
        return CHAR_REGION_MAP[char]

    segment_type = as_string(segment.get("type")).strip().lower()
    if segment_type in TYPE_REGION_MAP:
        return TYPE_REGION_MAP[segment_type]

    return segment_type or "unknown"


def segment_length(segment: dict[str, Any] | None) -> int:
    if not segment:
        return 0

    value = segment.get("length")
    if isinstance(value, int):
        return value

    return len(as_string(segment.get("sequence")))


def score_segment(
    protocol_id: str,
    model_name: str,
    gt_segment: dict[str, Any],
    pred_segment: dict[str, Any] | None,
    index: int,
) -> dict[str, Any]:
    gt_sequence = as_string(gt_segment.get("sequence"))
    pred_sequence = as_string(pred_segment.get("sequence")) if pred_segment else ""
    gt_char = as_string(gt_segment.get("char")).strip().upper()
    pred_char = as_string(pred_segment.get("char")).strip().upper() if pred_segment else ""
    gt_region = canonical_region(gt_segment)
    pred_region = canonical_region(pred_segment)
    variable_region = gt_region != "known"

    return {
        "protocol_id": protocol_id,
        "model_name": model_name,
        "segment_index": index,
        "segment_name": as_string(gt_segment.get("name")),
        "region": gt_region,
        "gt_region": gt_region,
        "pred_region": pred_region,
        "region_exact": gt_region == pred_region,
        "gt_sequence": gt_sequence,
        "pred_sequence": pred_sequence,
        "gt_length": segment_length(gt_segment),
        "pred_length": segment_length(pred_segment),
        "length_exact": segment_length(gt_segment) == segment_length(pred_segment),
        "sequence_similarity": similarity(gt_sequence, pred_sequence),
        "sequence_exact": gt_sequence == pred_sequence,
        "gt_char": gt_char,
        "pred_char": pred_char,
        "char_exact": (gt_char == pred_char) if variable_region else None,
        "pred_present": pred_segment is not None,
    }


def score_prediction(
    protocol: ProtocolRecord,
    model: ModelConfig,
    response_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = as_dict(response_payload.get("result"))
    gt = protocol.ground_truth
    gt_sequence = as_string(gt.get("library_sequence"))
    pred_sequence = as_string(result.get("library_sequence"))
    gt_segments = as_list_of_dicts(gt.get("segments"))
    pred_segments = as_list_of_dicts(result.get("segments"))

    region_rows = [
        score_segment(
            protocol.protocol_id,
            model.name,
            gt_segment,
            pred_segments[index] if index < len(pred_segments) else None,
            index,
        )
        for index, gt_segment in enumerate(gt_segments)
    ]

    region_exact_matches = sum(1 for row in region_rows if row["region_exact"])
    sequence_exact_matches = sum(1 for row in region_rows if row["sequence_exact"])
    length_exact_matches = sum(1 for row in region_rows if row["length_exact"])

    summary = {
        "prediction_protocol_name": as_string(result.get("protocol_name")),
        "library_similarity": similarity(gt_sequence, pred_sequence),
        "library_exact": gt_sequence == pred_sequence,
        "gt_library_length": len(gt_sequence),
        "pred_library_length": len(pred_sequence),
        "gt_segment_count": len(gt_segments),
        "pred_segment_count": len(pred_segments),
        "segment_count_match": len(gt_segments) == len(pred_segments),
        "ordered_region_accuracy": (
            region_exact_matches / len(region_rows) if region_rows else 1.0
        ),
        "ordered_sequence_accuracy": (
            sequence_exact_matches / len(region_rows) if region_rows else 1.0
        ),
        "ordered_length_accuracy": (
            length_exact_matches / len(region_rows) if region_rows else 1.0
        ),
        "placeholder_key_exact": as_dict(gt.get("placeholder_key"))
        == as_dict(result.get("placeholder_key")),
    }

    return summary, region_rows


def aggregate_model_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["model_name"], []).append(result)

    summary: dict[str, dict[str, Any]] = {}
    for model_name, rows in grouped.items():
        successful = [row for row in rows if not row.get("error")]
        summary[model_name] = {
            "runs": len(rows),
            "successes": len(successful),
            "failures": len(rows) - len(successful),
            "avg_library_similarity": mean(
                row["library_similarity"] for row in successful
            ),
            "library_exact_rate": mean(
                1.0 if row["library_exact"] else 0.0 for row in successful
            ),
            "avg_ordered_region_accuracy": mean(
                row["ordered_region_accuracy"] for row in successful
            ),
            "avg_ordered_sequence_accuracy": mean(
                row["ordered_sequence_accuracy"] for row in successful
            ),
            "avg_duration_seconds": mean(
                row["duration_seconds"] for row in rows
            ),
        }

    return summary


def aggregate_region_summary(region_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in region_rows:
        key = f"{row['model_name']}::{row['region']}"
        grouped.setdefault(key, []).append(row)

    summary: dict[str, dict[str, Any]] = {}
    for key, rows in grouped.items():
        model_name, region = key.split("::", 1)
        variable_rows = [row for row in rows if row["char_exact"] is not None]
        summary[key] = {
            "model_name": model_name,
            "region": region,
            "count": len(rows),
            "region_exact_rate": mean(1.0 if row["region_exact"] else 0.0 for row in rows),
            "sequence_exact_rate": mean(
                1.0 if row["sequence_exact"] else 0.0 for row in rows
            ),
            "avg_sequence_similarity": mean(
                row["sequence_similarity"] for row in rows
            ),
            "length_exact_rate": mean(
                1.0 if row["length_exact"] else 0.0 for row in rows
            ),
            "char_exact_rate": mean(
                1.0 if row["char_exact"] else 0.0 for row in variable_rows
            ),
        }

    return summary


def mean(values: Any) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def write_run_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    fieldnames = [
        "protocol_id",
        "protocol_name",
        "model_name",
        "model_id",
        "api",
        "input_mode",
        "source_used",
        "duration_seconds",
        "library_similarity",
        "library_exact",
        "gt_library_length",
        "pred_library_length",
        "gt_segment_count",
        "pred_segment_count",
        "segment_count_match",
        "ordered_region_accuracy",
        "ordered_sequence_accuracy",
        "ordered_length_accuracy",
        "placeholder_key_exact",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_region_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    fieldnames = [
        "protocol_id",
        "model_name",
        "segment_index",
        "segment_name",
        "region",
        "gt_region",
        "pred_region",
        "region_exact",
        "gt_length",
        "pred_length",
        "length_exact",
        "sequence_similarity",
        "sequence_exact",
        "gt_char",
        "pred_char",
        "char_exact",
        "pred_present",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def print_summary(
    model_summary: dict[str, dict[str, Any]],
    region_summary: dict[str, dict[str, Any]],
) -> None:
    print("\nAverage by model:")
    for model_name in sorted(model_summary):
        row = model_summary[model_name]
        print(
            f"  {model_name}: similarity={row['avg_library_similarity']:.3f} "
            f"exact={row['library_exact_rate']:.3f} "
            f"region={row['avg_ordered_region_accuracy']:.3f} "
            f"success={row['successes']}/{row['runs']}"
        )

    print("\nStratified by region:")
    for key in sorted(region_summary):
        row = region_summary[key]
        print(
            f"  {row['model_name']} | {row['region']}: "
            f"seq={row['avg_sequence_similarity']:.3f} "
            f"exact={row['sequence_exact_rate']:.3f} "
            f"len={row['length_exact_rate']:.3f} "
            f"type={row['region_exact_rate']:.3f}"
        )


def main() -> int:
    args = parse_args()
    root = repo_root()
    models = load_models(args.models)
    protocols = filter_protocols(
        load_protocols(root, args.protocol_dir),
        selected_ids=args.protocol,
        limit=args.limit,
    )

    if not protocols:
        raise SystemExit("No protocols selected for benchmarking")

    run_started_at = datetime.now(timezone.utc)
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    total_runs = len(models) * len(protocols)
    print(
        f"Starting benchmark: {len(models)} models x {len(protocols)} protocols = "
        f"{total_runs} runs"
    )
    print(f"Input mode: {args.input_mode}")
    print(f"Models config: {args.models}")

    run_rows: list[dict[str, Any]] = []
    region_rows: list[dict[str, Any]] = []

    run_index = 0
    for model in models:
        for protocol in protocols:
            run_index += 1
            label = (
                f"[{run_index}/{total_runs}] {model.name} | {protocol.protocol_id}"
            )
            print(f"{label} ...", end="", flush=True)
            started_at = time.time()

            row: dict[str, Any] = {
                "protocol_id": protocol.protocol_id,
                "protocol_name": protocol.protocol_name,
                "model_name": model.name,
                "model_id": model.model,
                "api": model.api,
                "input_mode": args.input_mode,
                "source_used": "",
                "error": None,
            }

            try:
                response_payload, source_used = post_benchmark(
                    model=model,
                    protocol=protocol,
                    input_mode=args.input_mode,
                    timeout=args.timeout,
                )
                summary, region_detail = score_prediction(protocol, model, response_payload)
                row.update(summary)
                row["source_used"] = source_used
                row["prediction"] = response_payload.get("result")
                if args.include_raw:
                    row["raw_output"] = response_payload.get("raw")
                region_rows.extend(region_detail)
                print(
                    f" similarity={row['library_similarity']:.3f} "
                    f"region={row['ordered_region_accuracy']:.3f}"
                )
            except Exception as exc:
                row.update(
                    {
                        "prediction_protocol_name": "",
                        "library_similarity": 0.0,
                        "library_exact": False,
                        "gt_library_length": len(
                            as_string(protocol.ground_truth.get("library_sequence"))
                        ),
                        "pred_library_length": 0,
                        "gt_segment_count": len(
                            as_list_of_dicts(protocol.ground_truth.get("segments"))
                        ),
                        "pred_segment_count": 0,
                        "segment_count_match": False,
                        "ordered_region_accuracy": 0.0,
                        "ordered_sequence_accuracy": 0.0,
                        "ordered_length_accuracy": 0.0,
                        "placeholder_key_exact": False,
                        "error": str(exc),
                    }
                )
                print(f" ERROR: {exc}")

            row["duration_seconds"] = round(time.time() - started_at, 3)
            run_rows.append(row)

            if args.delay > 0 and run_index < total_runs:
                time.sleep(args.delay)

    model_summary = aggregate_model_summary(run_rows)
    region_summary = aggregate_region_summary(region_rows)

    result_payload = {
        "run_started_at": run_started_at.isoformat(),
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
        "models_file": str(args.models),
        "input_mode": args.input_mode,
        "protocol_count": len(protocols),
        "model_count": len(models),
        "results": run_rows,
        "region_results": region_rows,
        "summary": {
            "by_model": model_summary,
            "by_model_and_region": region_summary,
        },
    }

    json_path = output_dir / f"benchmark-{run_timestamp}.json"
    runs_csv_path = output_dir / f"benchmark-runs-{run_timestamp}.csv"
    region_csv_path = output_dir / f"benchmark-regions-{run_timestamp}.csv"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(result_payload, handle, indent=2)
        handle.write("\n")

    write_run_csv(runs_csv_path, run_rows)
    write_region_csv(region_csv_path, region_rows)

    print_summary(model_summary, region_summary)
    print(f"\nWrote {json_path}")
    print(f"Wrote {runs_csv_path}")
    print(f"Wrote {region_csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
