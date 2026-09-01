#!/usr/bin/env python3
"""Extract plotting inputs for the LibStructBench baseline figure.

This script reads the preserved Harbor result schema actually present in the
repository.  It deliberately keeps unresolved error observations unresolved
and uses the project's frozen pricing snapshot and telemetry normalizer.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from libstruct_bench.libgen.telemetry import trial_telemetry


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "analysis" / "paper_results"
PRICING_PATH = REPO_ROOT / "benchmarks" / "libgen" / "pricing-2026-08-15.json"

PROTOCOLS = (
    ("10x_chromium_3_gene_expression_v4", "10x 3' GEX v4"),
    ("ddseq_single_cell_3_rna_seq_kit", "ddSEQ 3' RNA"),
    ("dr_seq", "DR-seq"),
    ("indrop_v1", "inDrop v1"),
    ("petri_seq", "PETRI-seq"),
    ("pip_seq_v4", "PIP-seq v4"),
    ("sci_atac_seq", "sci-ATAC-seq"),
    ("scrrbs", "scRRBS"),
    ("share_seq", "SHARE-seq"),
    ("smart_seq", "SMART-seq"),
)

# The native-CLI panel uses the complete first-wave baseline rather than the
# ten-protocol intersection required for cross-harness comparisons.
NATIVE_PROTOCOLS = (
    ("10x_chromium_single_cell_atac_v2", "10x scATAC v2"),
    ("10x_chromium_3_gene_expression_v4", "10x 3' GEX v4"),
    ("10x_chromium_3_feature_barcoding", "10x 3' Feature Barcoding"),
    ("drop_seq", "Drop-seq"),
    ("seq_well_s3", "Seq-Well S3"),
    ("indrop_v1", "inDrop v1"),
    ("s3_atac", "s3-ATAC"),
    ("cel_seq", "CEL-seq"),
    ("split_seq", "SPLiT-seq"),
    ("sci_rna_seq", "sci-RNA-seq"),
    ("sci_atac_seq", "sci-ATAC-seq"),
    ("microwell_seq", "Microwell-seq"),
    ("scrrbs", "scRRBS"),
    ("pip_seq_v4", "PIP-seq v4"),
    ("scdamid", "scDamID"),
    ("ddseq_single_cell_3_rna_seq_kit", "ddSEQ 3' RNA"),
    ("smart_seq", "SMART-seq"),
    ("share_seq", "SHARE-seq"),
    ("dr_seq", "DR-seq"),
    ("petri_seq", "PETRI-seq"),
)

SYSTEMS = (
    {
        "system_id": "gpt_pi",
        "system_label": "GPT + Pi",
        "model": "GPT",
        "harness": "Pi",
        "system_group": "shared",
        "model_key": "gpt_5_6_sol",
        "run_path": "runs/libgen/pi/libgen-pi-gpt-5-6-sol",
        "display_order": 1,
    },
    {
        "system_id": "gpt_mini_swe",
        "system_label": "GPT + mini-SWE",
        "model": "GPT",
        "harness": "mini-SWE",
        "system_group": "shared",
        "model_key": "gpt_5_6_sol",
        "run_path": "runs/libgen/mini-swe-agent/libgen-gpt-5-6-sol",
        "display_order": 2,
    },
    {
        "system_id": "claude_pi",
        "system_label": "Claude + Pi",
        "model": "Claude",
        "harness": "Pi",
        "system_group": "shared",
        "model_key": "claude_opus_5",
        "run_path": "runs/libgen/pi/libgen-pi-claude-opus-5",
        "display_order": 3,
    },
    {
        "system_id": "claude_mini_swe",
        "system_label": "Claude + mini-SWE",
        "model": "Claude",
        "harness": "mini-SWE",
        "system_group": "shared",
        "model_key": "claude_opus_5",
        "run_path": "runs/libgen/mini-swe-agent/libgen-claude-opus5",
        "display_order": 4,
    },
    {
        "system_id": "gemini_pi",
        "system_label": "Gemini + Pi",
        "model": "Gemini",
        "harness": "Pi",
        "system_group": "shared",
        "model_key": "gemini_3_7_flash",
        "run_path": "runs/libgen/pi/libgen-pi-gemini-3-7-flash",
        "display_order": 5,
    },
    {
        "system_id": "gemini_mini_swe",
        "system_label": "Gemini + mini-SWE",
        "model": "Gemini",
        "harness": "mini-SWE",
        "system_group": "shared",
        "model_key": "gemini_3_7_flash",
        "run_path": "runs/libgen/mini-swe-agent/libgen-gemini-3-7-flash",
        "display_order": 6,
    },
    {
        "system_id": "kimi_pi",
        "system_label": "Kimi + Pi",
        "model": "Kimi",
        "harness": "Pi",
        "system_group": "shared",
        "model_key": "kimi_k3",
        "run_path": "runs/libgen/pi/libgen-pi-kimi-k3",
        "display_order": 7,
    },
    {
        "system_id": "kimi_mini_swe",
        "system_label": "Kimi + mini-SWE",
        "model": "Kimi",
        "harness": "mini-SWE",
        "system_group": "shared",
        "model_key": "kimi_k3",
        "run_path": "runs/libgen/mini-swe-agent/libgen-kimi-k3",
        "display_order": 8,
    },
    {
        "system_id": "gpt_codex",
        "system_label": "GPT + Codex",
        "model": "GPT",
        "harness": "Native",
        "system_group": "native",
        "model_key": "gpt_5_6_sol",
        "run_path": "runs/libgen/codex/libgen-gpt-5-6-sol",
        "display_order": 9,
    },
    {
        "system_id": "claude_code",
        "system_label": "Claude + Claude Code",
        "model": "Claude",
        "harness": "Native",
        "system_group": "native",
        "model_key": "claude_opus_5",
        "run_path": "runs/libgen/claude-code/libgen-claude-code-opus-5",
        "display_order": 10,
    },
    {
        "system_id": "gemini_antigravity",
        "system_label": "Gemini + Antigravity CLI",
        "model": "Gemini",
        "harness": "Native",
        "system_group": "native",
        "model_key": "gemini_3_7_flash",
        "run_path": "runs/libgen/antigravity-cli/libgen-gemini-3-7-flash",
        "display_order": 11,
    },
    {
        "system_id": "kimi_code",
        "system_label": "Kimi + Kimi Code",
        "model": "Kimi",
        "harness": "Native",
        "system_group": "native",
        "model_key": "kimi_k3",
        "run_path": "runs/libgen/kimi-code/libgen-kimi-code-kimi-k3",
        "display_order": 12,
    },
)

PUBLIC_METRICS = (
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _protocol_id(trial_dir: Path, result: dict[str, Any]) -> str | None:
    details = _read_json(trial_dir / "verifier" / "details.json") or {}
    protocol_id = details.get("protocol_id")
    if isinstance(protocol_id, str) and protocol_id:
        return protocol_id
    task_name = result.get("task_name")
    if isinstance(task_name, str) and task_name.startswith("sequencing/libgen-"):
        return task_name.removeprefix("sequencing/libgen-")
    return None


def _rewards(trial_dir: Path, result: dict[str, Any]) -> dict[str, float]:
    reward_doc = _read_json(trial_dir / "verifier" / "reward.json")
    if reward_doc is None:
        reward_doc = (result.get("verifier_result") or {}).get("rewards") or {}
    return {
        key: value
        for key in PUBLIC_METRICS
        if (value := _number(reward_doc.get(key))) is not None
    }


def _explicit_root_marker(observation: dict[str, Any]) -> tuple[str | None, bool]:
    root_id = observation.get("root_error_id") or observation.get(
        "consolidated_root_error_id"
    )
    if not isinstance(root_id, str) or not root_id:
        root_id = None
    explicit_flag = observation.get("is_consolidated_root_error") is True or (
        observation.get("consolidated_root_error") is True
    )
    return root_id, bool(root_id or explicit_flag)


def _trial_row(
    system: dict[str, Any],
    protocol_id: str,
    protocol_label: str,
    protocol_order: int,
    trial_dir: Path | None,
    result: dict[str, Any] | None,
    pricing: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = {
        key: system[key]
        for key in (
            "system_id",
            "system_label",
            "model",
            "harness",
            "system_group",
            "model_key",
            "display_order",
        )
    }
    base.update(
        {
            "protocol_id": protocol_id,
            "protocol_label": protocol_label,
            "protocol_order": protocol_order,
            "scheduled_attempt": 1,
            "result_present": int(result is not None),
            "result_path": str(trial_dir / "result.json") if trial_dir else "",
        }
    )
    if result is None or trial_dir is None:
        base.update(
            {
                "status": "missing_scheduled_result",
                "prediction_valid": 0,
                "verifier_completed": 0,
                "valid_completion": 0,
                "error_review_status": "",
            }
        )
        for metric in PUBLIC_METRICS:
            base[metric] = None
            base[f"primary_{metric}"] = 0.0
        return base, []

    details = _read_json(trial_dir / "verifier" / "details.json") or {}
    rewards = _rewards(trial_dir, result)
    prediction_valid_raw = details.get("prediction_valid")
    prediction_valid = (
        prediction_valid_raw if isinstance(prediction_valid_raw, bool) else None
    )
    verifier_completed = all(metric in rewards for metric in PUBLIC_METRICS)
    valid_completion = (
        bool(prediction_valid and verifier_completed)
        if prediction_valid is not None
        else None
    )
    exception_type = (result.get("exception_info") or {}).get("exception_type")
    status = (
        "valid_completion"
        if valid_completion is True
        else "invalid_prediction"
        if prediction_valid is False and verifier_completed
        else "agent_error"
        if exception_type
        else "incomplete"
    )
    error_doc = _read_json(trial_dir / "verifier" / "error_analysis.json") or {}
    base.update(
        {
            "status": status,
            "exception_type": exception_type or "",
            "prediction_valid": (
                int(prediction_valid) if prediction_valid is not None else None
            ),
            "verifier_completed": int(verifier_completed),
            "valid_completion": (
                int(valid_completion) if valid_completion is not None else None
            ),
            "error_review_status": error_doc.get("review_status") or "",
        }
    )
    for metric in PUBLIC_METRICS:
        raw_value = rewards.get(metric)
        base[metric] = raw_value
        # Scheduled-attempt population policy: invalid/incomplete attempts score
        # zero; a missing validity determination remains missing.
        base[f"primary_{metric}"] = (
            raw_value
            if valid_completion is True
            else 0.0
            if valid_completion is False
            else None
        )

    cell = {
        "model_key": system["model_key"],
        "harbor_agent": (result.get("agent_info") or {}).get("name"),
    }
    telemetry = trial_telemetry(trial_dir, result, cell, pricing)
    telemetry_fields = (
        "input_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cache_creation_5m_tokens",
        "cache_creation_1h_tokens",
        "output_tokens",
        "normalized_api_cost_usd",
        "normalized_api_cost_precision",
        "normalized_api_cost_kind",
        "pricing_snapshot_id",
        "pricing_date",
        "pricing_status",
        "token_usage_source",
    )
    base.update({key: telemetry.get(key) for key in telemetry_fields})
    input_tokens = _number(telemetry.get("input_tokens"))
    cache_tokens = _number(telemetry.get("cache_read_tokens"))
    creation_tokens = _number(telemetry.get("cache_creation_tokens"))
    base["uncached_input_tokens"] = (
        max(0.0, input_tokens - (cache_tokens or 0.0) - (creation_tokens or 0.0))
        if input_tokens is not None
        else None
    )

    pricing_model = (pricing.get("models") or {}).get(system["model_key"]) or {}
    rates = pricing_model.get("rates_per_million_tokens") or {}
    for source, target in (
        ("uncached_input", "rate_uncached_input_per_million"),
        ("cached_input", "rate_cached_input_per_million"),
        ("output", "rate_output_per_million"),
        ("cache_creation_5m", "rate_cache_creation_5m_per_million"),
        ("cache_creation_1h", "rate_cache_creation_1h_per_million"),
    ):
        base[target] = rates.get(source)

    observations: list[dict[str, Any]] = []
    for observation in error_doc.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        root_id, is_consolidated = _explicit_root_marker(observation)
        observations.append(
            {
                **{
                    key: base[key]
                    for key in (
                        "system_id",
                        "system_label",
                        "model",
                        "harness",
                        "system_group",
                        "protocol_id",
                        "protocol_label",
                    )
                },
                "trial_review_status": error_doc.get("review_status") or "",
                "error_id": observation.get("error_id"),
                "task": observation.get("task"),
                "category": observation.get("category"),
                "entity_type": observation.get("entity_type"),
                "substantive": observation.get("substantive"),
                "benchmark_validity": observation.get("benchmark_validity"),
                "attribution": observation.get("attribution"),
                "adjudication_status": observation.get("adjudication_status"),
                "process_cause": observation.get("process_cause"),
                "root_error_id": root_id,
                "is_consolidated_root_error": int(is_consolidated),
            }
        )
    return base, observations


def main() -> int:
    pricing = json.loads(PRICING_PATH.read_text())
    target_protocols = {protocol_id for protocol_id, _ in PROTOCOLS}
    trial_rows: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    inventory_rows: list[dict[str, Any]] = []
    native_trial_rows: list[dict[str, Any]] = []

    for system in SYSTEMS:
        run_dir = REPO_ROOT / system["run_path"]
        results_by_protocol: dict[str, tuple[Path, dict[str, Any]]] = {}
        if run_dir.is_dir():
            for result_path in sorted(run_dir.glob("*/result.json")):
                result = _read_json(result_path)
                if result is None:
                    continue
                trial_dir = result_path.parent
                protocol_id = _protocol_id(trial_dir, result)
                if protocol_id in target_protocols:
                    if protocol_id in results_by_protocol:
                        raise RuntimeError(
                            f"duplicate current result for {system['system_id']} / "
                            f"{protocol_id}"
                        )
                    results_by_protocol[protocol_id] = (trial_dir, result)
        run_available = bool(results_by_protocol)
        inventory_rows.append(
            {
                **{
                    key: system[key]
                    for key in (
                        "system_id",
                        "system_label",
                        "model",
                        "harness",
                        "system_group",
                        "model_key",
                        "display_order",
                        "run_path",
                    )
                },
                "run_available": int(run_available),
                "observed_target_protocols": len(results_by_protocol),
                "exclusion_reason": (
                    ""
                    if run_available
                    else "no preserved Harbor result directory for this system"
                ),
            }
        )
        if not run_available:
            continue
        for protocol_order, (protocol_id, protocol_label) in enumerate(
            PROTOCOLS, start=1
        ):
            trial_dir, result = results_by_protocol.get(protocol_id, (None, None))
            trial_row, observations = _trial_row(
                system,
                protocol_id,
                protocol_label,
                protocol_order,
                trial_dir,
                result,
                pricing,
            )
            trial_rows.append(trial_row)
            observation_rows.extend(observations)

    native_target_protocols = {
        protocol_id for protocol_id, _ in NATIVE_PROTOCOLS
    }
    for system in (item for item in SYSTEMS if item["system_group"] == "native"):
        run_dir = REPO_ROOT / system["run_path"]
        results_by_protocol: dict[str, tuple[Path, dict[str, Any]]] = {}
        if run_dir.is_dir():
            for result_path in sorted(run_dir.glob("*/result.json")):
                result = _read_json(result_path)
                if result is None:
                    continue
                trial_dir = result_path.parent
                protocol_id = _protocol_id(trial_dir, result)
                if protocol_id in native_target_protocols:
                    if protocol_id in results_by_protocol:
                        raise RuntimeError(
                            "duplicate native result for "
                            f"{system['system_id']} / {protocol_id}"
                        )
                    results_by_protocol[protocol_id] = (trial_dir, result)
        for protocol_order, (protocol_id, protocol_label) in enumerate(
            NATIVE_PROTOCOLS, start=1
        ):
            trial_dir, result = results_by_protocol.get(protocol_id, (None, None))
            trial_row, _ = _trial_row(
                system,
                protocol_id,
                protocol_label,
                protocol_order,
                trial_dir,
                result,
                pricing,
            )
            native_trial_rows.append(trial_row)

    _write_csv(OUTPUT_ROOT / "fig3_trials_extracted.csv", trial_rows)
    _write_csv(
        OUTPUT_ROOT / "fig3_native_trials_extracted.csv", native_trial_rows
    )
    _write_csv(
        OUTPUT_ROOT / "fig3_error_observations_extracted.csv", observation_rows
    )
    _write_csv(OUTPUT_ROOT / "fig3_system_inventory.csv", inventory_rows)
    print(
        f"Extracted {len(trial_rows)} scheduled system-protocol rows and "
        f"{len(observation_rows)} unresolved/adjudicated discrepancy rows; "
        f"extracted {len(native_trial_rows)} native-CLI rows for Panel A."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
