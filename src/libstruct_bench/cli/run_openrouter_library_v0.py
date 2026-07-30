from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from libstruct_bench.hf_io import download_hf_dataset_file
from libstruct_bench.library_structure import (
    PREDICTION_SCHEMA_VERSION,
    LibraryStructureValidationError,
    extract_json_document,
)
from libstruct_bench.library_structure_policy import (
    BIOLOGICAL_PAYLOAD_POLICY,
    LIBRARY_DERIVATION_POLICY,
    LIBRARY_DERIVATION_SELF_CHECK_RULE,
    LIBRARY_ENTRY_POLICY,
    OPENROUTER_SOURCE_POLICY,
    PLACEHOLDER_POLICY,
)

DEFAULT_BENCHMARK_DIR = Path("benchmarks/library_structure")
DEFAULT_MODELS = [
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4.6",
    "google/gemini-3.1-pro-preview",
]
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_FILES_URL = "https://openrouter.ai/api/v1/files"


class OpenRouterResponseError(RuntimeError):
    def __init__(self, message: str, *, body: str | None = None) -> None:
        super().__init__(message)
        self.body = body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v0.1 OpenRouter library-structure baseline.")
    parser.add_argument("--protocols", default=str(DEFAULT_BENCHMARK_DIR / "protocols.json"))
    parser.add_argument("--out", required=True, help="Output directory for raw responses and predictions.")
    parser.add_argument("--model", action="append", help="OpenRouter model id. Repeat to run multiple models.")
    parser.add_argument("--protocol-id", action="append", help="Protocol id to include. Repeat for multiple protocols.")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--chat-url", default=OPENROUTER_CHAT_URL)
    parser.add_argument("--files-url", default=OPENROUTER_FILES_URL)
    parser.add_argument(
        "--file-mode",
        choices=["direct", "upload"],
        default="direct",
        help="How to attach protocol files. direct embeds data URLs in chat; upload uses /api/v1/files.",
    )
    parser.add_argument(
        "--pdf-engine",
        default="",
        help=(
            "Optional OpenRouter file-parser PDF engine for direct file inputs. "
            "Leave empty to omit the parser plugin and try native file pass-through."
        ),
    )
    parser.add_argument("--max-completion-tokens", type=int, default=8192)
    parser.add_argument("--temperature", type=float)
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        help="Optional OpenRouter reasoning.effort value. Leave unset to use provider defaults.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write requests without calling OpenRouter.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing prediction directories.")
    parser.add_argument(
        "--retry-existing-errors",
        action="store_true",
        help=(
            "Retry model/protocol directories that already contain error.json. "
            "By default, existing errors are skipped so interrupted resume runs do not repeat known failures."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with later model/protocol requests after an OpenRouter API error.",
    )
    parser.add_argument(
        "--request-retries",
        type=int,
        default=0,
        help="Number of times to retry each OpenRouter chat request after a request/response error.",
    )
    parser.add_argument(
        "--retry-delay-sec",
        type=float,
        default=10.0,
        help="Seconds to wait between OpenRouter chat request retries.",
    )
    args = parser.parse_args(argv)

    config = json.loads(Path(args.protocols).read_text(encoding="utf-8"))
    models = args.model or DEFAULT_MODELS
    selected_protocols = set(args.protocol_id or [])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get(args.api_key_env)
    if not args.dry_run and not api_key:
        raise ValueError(f"{args.api_key_env} is required unless --dry-run is used")

    protocols = [
        protocol
        for protocol in config.get("protocols", [])
        if not selected_protocols or protocol.get("protocol_id") in selected_protocols
    ]
    if selected_protocols and len(protocols) != len(selected_protocols):
        found = {protocol.get("protocol_id") for protocol in protocols}
        missing = sorted(selected_protocols - found)
        raise ValueError(f"unknown protocol id(s): {', '.join(missing)}")

    summary: list[dict[str, Any]] = []
    runs = [(model, protocol) for model in models for protocol in protocols]
    _log(
        f"starting OpenRouter library-structure run: "
        f"{len(protocols)} protocol(s) x {len(models)} model(s) = {len(runs)} request(s)"
    )
    _log(f"output directory: {out_dir}")
    if args.dry_run:
        _log("dry-run enabled: requests will be written but not sent")
    if args.pdf_engine:
        _log(f"pdf parser plugin enabled: {args.pdf_engine}")
    else:
        _log("pdf parser plugin disabled: using native direct file pass-through")
    for index, (model, protocol) in enumerate(runs, start=1):
        protocol_id = protocol.get("protocol_id", "<unknown>")
        _log(f"[{index}/{len(runs)}] {protocol_id} | {model}: start")
        try:
            result = _run_one(
                config=config,
                protocol=protocol,
                model=model,
                out_dir=out_dir,
                api_key=api_key or "",
                chat_url=args.chat_url,
                files_url=args.files_url,
                file_mode=args.file_mode,
                pdf_engine=args.pdf_engine,
                max_completion_tokens=args.max_completion_tokens,
                temperature=args.temperature,
                reasoning_effort=args.reasoning_effort,
                dry_run=args.dry_run,
                force=args.force,
                skip_existing_errors=not args.retry_existing_errors,
                request_retries=args.request_retries,
                retry_delay_sec=args.retry_delay_sec,
            )
            summary.append(result)
        except OpenRouterResponseError as exc:
            _log(f"[{index}/{len(runs)}] {protocol_id} | {model}: failed: {exc}")
            summary.append(
                {
                    "model": model,
                    "protocol_id": protocol_id,
                    "status": "failed",
                    "error": str(exc),
                    "error_path": str(out_dir / _slug(model) / str(protocol_id) / "error.json"),
                }
            )
            if not args.continue_on_error:
                _write_summary(out_dir, summary)
                return 1
            continue
        _log(f"[{index}/{len(runs)}] {protocol_id} | {model}: done")

    _write_summary(out_dir, summary)
    return 0


def _run_one(
    *,
    config: dict[str, Any],
    protocol: dict[str, Any],
    model: str,
    out_dir: Path,
    api_key: str,
    chat_url: str,
    files_url: str,
    file_mode: str,
    pdf_engine: str,
    max_completion_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
    dry_run: bool,
    force: bool,
    skip_existing_errors: bool,
    request_retries: int,
    retry_delay_sec: float,
) -> dict[str, Any]:
    protocol_id = _required(protocol, "protocol_id")
    model_dir = out_dir / _slug(model) / protocol_id
    prediction_path = model_dir / "prediction.json"
    error_path = model_dir / "error.json"
    if prediction_path.exists() and not force:
        _log(f"{protocol_id} | {model}: skipping existing prediction: {prediction_path}")
        return {
            "model": model,
            "protocol_id": protocol_id,
            "status": "skipped",
            "prediction": str(prediction_path),
        }
    if error_path.exists() and skip_existing_errors and not force:
        error = _read_existing_error(error_path)
        _log(f"{protocol_id} | {model}: skipping existing error: {error_path}")
        return {
            "model": model,
            "protocol_id": protocol_id,
            "status": "skipped_error",
            "error": error,
            "error_path": str(error_path),
        }
    model_dir.mkdir(parents=True, exist_ok=True)
    if force:
        _clear_generated_outputs(model_dir)

    input_repo = protocol.get("input_repo") or config.get("input_repo")
    input_revision = protocol.get("input_revision", protocol.get("revision", config.get("input_revision", "main")))
    if not isinstance(input_repo, str) or not input_repo:
        raise ValueError("input_repo is required")
    if not isinstance(input_revision, str) or not input_revision:
        raise ValueError("input_revision is required")
    input_paths = _input_paths(protocol)
    _log(
        f"{protocol_id} | {model}: downloading {len(input_paths)} input file(s) "
        f"from {input_repo}@{input_revision}"
    )
    files = [
        _download_input(repo_id=input_repo, revision=input_revision, input_path=input_path)
        for input_path in input_paths
    ]
    for item in files:
        _log(
            f"{protocol_id} | {model}: downloaded {item['path']} "
            f"({_format_bytes(len(item['data']))}, {item['mime_type']})"
        )
    prompt = _prompt(protocol_id=protocol_id, display_name=protocol.get("display_name", protocol_id), input_paths=input_paths)
    file_refs: list[dict[str, Any]] = []
    if file_mode == "direct":
        _log(f"{protocol_id} | {model}: attaching files directly as data URLs")
        file_refs = [_direct_file_ref(file_item) for file_item in files]
    elif not dry_run:
        _log(f"{protocol_id} | {model}: uploading files through OpenRouter files API")
        for file_item in files:
            _log(f"{protocol_id} | {model}: uploading {file_item['filename']}")
            file_refs.append(_upload_file(files_url=files_url, api_key=api_key, file_item=file_item))
    elif file_mode == "upload":
        _log(f"{protocol_id} | {model}: dry-run upload mode; request will contain prompt only")

    _log(f"{protocol_id} | {model}: building chat payload")
    request_payload = _chat_payload(
        model=model,
        prompt=prompt,
        file_refs=file_refs,
        pdf_engine=pdf_engine if file_mode == "direct" else "",
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )
    _log(f"{protocol_id} | {model}: writing request and input manifest")
    (model_dir / "request.json").write_text(json.dumps(request_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (model_dir / "input_files.json").write_text(
        json.dumps(
            [
                {
                    "path": item["path"],
                    "filename": item["filename"],
                    "mime_type": item["mime_type"],
                    "size_bytes": len(item["data"]),
                }
                for item in files
            ],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if dry_run:
        _log(f"{protocol_id} | {model}: dry-run complete")
        return {
            "model": model,
            "protocol_id": protocol_id,
            "status": "dry_run",
            "request": str(model_dir / "request.json"),
        }

    max_attempts = max(1, request_retries + 1)
    parsed: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            suffix = f" (attempt {attempt}/{max_attempts})" if max_attempts > 1 else ""
            _log(f"{protocol_id} | {model}: sending request to OpenRouter{suffix}")
            raw_response = _post_json(chat_url, api_key, request_payload)
            _log(f"{protocol_id} | {model}: received response; writing response.json")
            (model_dir / "response.json").write_text(
                json.dumps(raw_response, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if isinstance(raw_response, dict) and isinstance(raw_response.get("error"), dict):
                error = raw_response["error"]
                raise OpenRouterResponseError(
                    f"OpenRouter returned error {error.get('code')}: {error.get('message')}"
                )
            _log(f"{protocol_id} | {model}: extracting JSON from response text")
            text = _response_text(raw_response)
            (model_dir / "raw_response.txt").write_text(text, encoding="utf-8")
            parsed = extract_json_document(text)
            break
        except (OpenRouterResponseError, LibraryStructureValidationError) as exc:
            if attempt < max_attempts:
                _log(
                    f"{protocol_id} | {model}: request/response failed: {exc}; "
                    f"retrying in {retry_delay_sec:g}s"
                )
                time.sleep(max(0.0, retry_delay_sec))
                continue
            _log(f"{protocol_id} | {model}: OpenRouter request/response failed")
            if isinstance(exc, OpenRouterResponseError) and exc.body is not None:
                (model_dir / "failed_response.txt").write_text(exc.body, encoding="utf-8")
            error = str(exc)
            _write_error(model_dir=model_dir, model=model, protocol_id=protocol_id, error=error)
            if isinstance(exc, OpenRouterResponseError):
                raise
            raise OpenRouterResponseError(error) from exc
    if parsed is None:
        raise OpenRouterResponseError("OpenRouter response parsing failed without an error")
    prediction = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "protocol_id": protocol_id,
    }
    if isinstance(parsed.get("libraries"), list):
        prediction["libraries"] = parsed["libraries"]
    else:
        prediction["library_sequence"] = parsed.get("library_sequence")
        if isinstance(parsed.get("annotated_library_sequence"), str):
            prediction["annotated_library_sequence"] = parsed["annotated_library_sequence"]
    prediction_path.write_text(json.dumps(prediction, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log(f"{protocol_id} | {model}: wrote prediction: {prediction_path}")
    return {
        "model": model,
        "protocol_id": protocol_id,
        "status": "completed",
        "prediction": str(prediction_path),
        "response": str(model_dir / "response.json"),
    }


def _clear_generated_outputs(model_dir: Path) -> None:
    for filename in [
        "prediction.json",
        "raw_response.txt",
        "response.json",
        "error.json",
        "failed_response.txt",
    ]:
        path = model_dir / filename
        if path.exists():
            path.unlink()


def _write_summary(out_dir: Path, summary: list[dict[str, Any]]) -> None:
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _log(f"wrote summary: {out_dir / 'summary.json'}")


def _write_error(*, model_dir: Path, model: str, protocol_id: str, error: str) -> None:
    (model_dir / "error.json").write_text(
        json.dumps(
            {
                "model": model,
                "protocol_id": protocol_id,
                "error": error,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_existing_error(error_path: Path) -> str:
    try:
        data = json.loads(error_path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return "existing error"
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, str) and error else "existing error"


def _log(message: str) -> None:
    print(f"[openrouter] {message}", flush=True)


def _format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _download_input(*, repo_id: str, revision: str, input_path: str) -> dict[str, Any]:
    data = download_hf_dataset_file(repo_id=repo_id, revision=revision, path=input_path, timeout_sec=180.0)
    filename = Path(input_path).name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {
        "path": input_path,
        "filename": filename,
        "mime_type": mime_type,
        "data": data,
    }


def _upload_file(*, files_url: str, api_key: str, file_item: dict[str, Any]) -> dict[str, Any]:
    boundary = f"libstruct{int(time.time() * 1000)}"
    filename = file_item["filename"]
    mime_type = file_item["mime_type"]
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_item["data"],
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        files_url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        uploaded = json.loads(response.read().decode("utf-8"))
    if not isinstance(uploaded, dict) or not uploaded.get("id"):
        raise ValueError(f"OpenRouter file upload did not return a file id for {filename}")
    return {
        "type": "file",
        "file": {
            "file_id": uploaded["id"],
            "filename": filename,
        },
    }


def _direct_file_ref(file_item: dict[str, Any]) -> dict[str, Any]:
    mime_type = file_item["mime_type"]
    encoded = base64.b64encode(file_item["data"]).decode("ascii")
    return {
        "type": "file",
        "file": {
            "filename": file_item["filename"],
            "file_data": f"data:{mime_type};base64,{encoded}",
        },
    }


def _chat_payload(
    *,
    model: str,
    prompt: str,
    file_refs: list[dict[str, Any]],
    pdf_engine: str,
    max_completion_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(file_refs)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a sequencing library-structure expert. Return only JSON. "
                    "The scoring targets are the final libraries[].library_sequence strings."
                ),
            },
            {
                "role": "user",
                "content": content,
            },
        ],
        "max_tokens": max_completion_tokens,
        "response_format": {"type": "json_object"},
    }
    if pdf_engine:
        payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": pdf_engine}}]
    if temperature is not None:
        payload["temperature"] = temperature
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
    return payload


def _post_json(url: str, api_key: str, payload: dict[str, Any]) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/seqmachines/libstruct-bench",
            "X-Title": "libstruct-bench library_structure",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except JSONDecodeError as exc:
                snippet = _response_snippet(body)
                raise OpenRouterResponseError(
                    f"OpenRouter returned non-JSON response: {snippet}",
                    body=body,
                ) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenRouterResponseError(f"OpenRouter request failed with HTTP {exc.code}: {detail}") from exc


def _response_text(response: Any) -> str:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices:
        raise OpenRouterResponseError("OpenRouter response did not include choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        text = "\n".join(part for part in text_parts if part.strip())
        if text.strip():
            return text
    finish_reason = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
    usage = response.get("usage") if isinstance(response, dict) else None
    reasoning_tokens = None
    completion_tokens = None
    if isinstance(usage, dict):
        completion_tokens = usage.get("completion_tokens")
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            reasoning_tokens = details.get("reasoning_tokens")
    detail = f"finish_reason={finish_reason!r}"
    if completion_tokens is not None:
        detail += f", completion_tokens={completion_tokens}"
    if reasoning_tokens is not None:
        detail += f", reasoning_tokens={reasoning_tokens}"
    raise OpenRouterResponseError(f"OpenRouter response did not include text content ({detail})")


def _response_snippet(body: str) -> str:
    if not body:
        return "<empty body>"
    if not body.strip():
        return f"<whitespace-only body, {len(body)} bytes>"
    collapsed = re.sub(r"\s+", " ", body.strip())
    return collapsed[:1000]


def _prompt(*, protocol_id: str, display_name: str, input_paths: list[str]) -> str:
    input_list = "\n".join(f"- {path}" for path in input_paths)
    return f"""Protocol id: {protocol_id}
Protocol name: {display_name}

Input files:
{input_list}

Parse the attached sequencing protocol inputs and return the final sequencing
library structure as one 5'->3' sequence entry per final library.

Return only JSON with this shape. The values in the block below are dummy schema
examples only. Do not copy the example `library_id`, `modality`,
`library_sequence`, `annotated_library_sequence`, bases, placeholder lengths, or
segment order unless the attached source files support them.
{{
  "schema_version": "{PREDICTION_SCHEMA_VERSION}",
  "protocol_id": "{protocol_id}",
  "libraries": [
    {{
      "library_id": "example_library",
      "modality": "example",
      "library_sequence": "ACGT####~~~~TGCA",
      "annotated_library_sequence": "ACGT[CELL_BARCODE:4][UMI:4]TGCA"
    }}
  ]
}}

{LIBRARY_DERIVATION_POLICY}

Rules:
{OPENROUTER_SOURCE_POLICY}
- The schema block above is only a JSON shape example, not a partial answer and
  not source evidence. If any source-derived sequence conflicts with the
  example, the source wins.
{LIBRARY_ENTRY_POLICY}
{BIOLOGICAL_PAYLOAD_POLICY}
{PLACEHOLDER_POLICY}
{LIBRARY_DERIVATION_SELF_CHECK_RULE}
- Do not include Markdown, comments, prose, or extra keys.
"""


def _required(protocol: dict[str, Any], key: str) -> str:
    value = protocol.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"protocol entry is missing required string field {key!r}")
    return value


def _input_paths(protocol: dict[str, Any]) -> list[str]:
    paths = protocol.get("input_paths")
    if paths is None:
        return [_required(protocol, "input_path")]
    if not isinstance(paths, list) or not paths:
        raise ValueError("protocol entry input_paths must be a non-empty list when present")
    return [str(path).strip() for path in paths if str(path).strip()]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return slug or "model"


if __name__ == "__main__":
    raise SystemExit(main())
