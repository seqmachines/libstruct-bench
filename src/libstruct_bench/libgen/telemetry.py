from __future__ import annotations

import json
from pathlib import Path
from typing import Any


Number = int | float


def trial_telemetry(
    trial_dir: Path,
    result: dict[str, Any],
    cell: dict[str, Any],
    pricing_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Extract lossless usage fields and a frozen API-equivalent cost estimate."""
    agent_result = result.get("agent_result") or {}
    trajectory = _load_trajectory(trial_dir)
    final = (trajectory or {}).get("final_metrics") or {}
    final_extra = final.get("extra") or {}
    step_totals, calls = _step_usage(trajectory)
    pi_totals, pi_calls = _pi_usage(trial_dir)
    if not calls and pi_calls:
        calls = pi_calls

    input_tokens = _first_number(
        agent_result.get("n_input_tokens"),
        final.get("total_prompt_tokens"),
        step_totals.get("prompt_tokens"),
        pi_totals.get("prompt_tokens"),
    )
    cache_read_tokens = _first_number(
        final_extra.get("total_cache_read_input_tokens"),
        agent_result.get("n_cache_tokens"),
        final.get("total_cached_tokens"),
        step_totals.get("cached_tokens"),
        pi_totals.get("cache_read_tokens"),
    )
    output_tokens = _first_number(
        agent_result.get("n_output_tokens"),
        final.get("total_completion_tokens"),
        step_totals.get("completion_tokens"),
        pi_totals.get("completion_tokens"),
    )

    reasoning_tokens = _first_number(
        final_extra.get("reasoning_output_tokens"),
        final_extra.get("total_reasoning_tokens"),
        step_totals.get("reasoning_tokens"),
    )
    cache_creation_5m = _first_number(
        final_extra.get("total_cache_creation_5m_input_tokens"),
        step_totals.get("cache_creation_5m_tokens"),
    )
    cache_creation_1h = _first_number(
        final_extra.get("total_cache_creation_1h_input_tokens"),
        step_totals.get("cache_creation_1h_tokens"),
    )
    cache_creation_tokens = _first_number(
        final_extra.get("total_cache_creation_input_tokens"),
        step_totals.get("cache_creation_tokens"),
        pi_totals.get("cache_creation_tokens"),
    )
    if cache_creation_tokens is None and (
        cache_creation_5m is not None or cache_creation_1h is not None
    ):
        cache_creation_tokens = (cache_creation_5m or 0) + (cache_creation_1h or 0)
    tool_tokens = _first_number(
        final_extra.get("total_tool_tokens"), step_totals.get("tool_tokens")
    )

    reported_cost = _first_number(
        agent_result.get("cost_usd"),
        final.get("total_cost_usd"),
        pi_totals.get("cost_usd"),
    )
    cost_kind, cost_source = _reported_cost_provenance(
        cell.get("harbor_agent"), final_extra, reported_cost
    )
    aggregate = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "cached_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_creation_5m_tokens": cache_creation_5m,
        "cache_creation_1h_tokens": cache_creation_1h,
    }
    normalized = normalized_api_cost(
        pricing_snapshot, cell.get("model_key"), calls, aggregate
    )
    pricing_model = (pricing_snapshot.get("models") or {}).get(cell.get("model_key"))
    provider_fields = {
        "final_extra": _token_fields(final_extra),
        "step_totals": step_totals.get("provider_fields", {}),
    }
    if pi_totals:
        provider_fields["pi_totals"] = pi_totals
    provider_fields_present = any(bool(value) for value in provider_fields.values())

    return {
        "input_tokens": input_tokens,
        "cache_tokens": cache_read_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_creation_5m_tokens": cache_creation_5m,
        "cache_creation_1h_tokens": cache_creation_1h,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "tool_tokens": tool_tokens,
        "provider_token_fields_json": _compact_json(provider_fields),
        "provider_token_fields_present": provider_fields_present,
        "token_usage_source": (
            "harbor_agent_result"
            if any(
                _number(agent_result.get(key)) is not None
                for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
            )
            else "atif_trajectory"
            if trajectory
            else "pi_log"
            if pi_totals
            else "unavailable"
        ),
        "reported_cost_usd": reported_cost,
        # Compatibility alias. New analyses should use reported_cost_usd explicitly.
        "cost_usd": reported_cost,
        "reported_cost_kind": cost_kind,
        "reported_cost_source": cost_source,
        "normalized_api_cost_usd": normalized["cost_usd"],
        "normalized_api_cost_kind": (
            "estimated" if normalized["cost_usd"] is not None else "unavailable"
        ),
        "normalized_api_cost_precision": normalized["precision"],
        "pricing_snapshot_id": pricing_snapshot.get("snapshot_id"),
        "pricing_date": pricing_snapshot.get("as_of"),
        "pricing_source_url": (
            pricing_model.get("source_url") if pricing_model else None
        ),
        "pricing_source_retrieved_at": (
            pricing_model.get("source_retrieved_at") if pricing_model else None
        ),
        "pricing_status": (
            pricing_model.get("pricing_status", "per_token_rates_available")
            if pricing_model
            else "pricing_unavailable"
        ),
    }


def normalized_api_cost(
    snapshot: dict[str, Any],
    model_key: str | None,
    calls: list[dict[str, Number | None]],
    aggregate: dict[str, Number | None],
) -> dict[str, Any]:
    model = (snapshot.get("models") or {}).get(model_key)
    if not model:
        return {"cost_usd": None, "precision": "pricing_unavailable"}
    if not model.get("rates_per_million_tokens"):
        return {"cost_usd": None, "precision": "pricing_rate_unavailable"}
    usable_calls = [
        call
        for call in calls
        if _number(call.get("prompt_tokens")) is not None
        or _number(call.get("completion_tokens")) is not None
    ]
    if usable_calls:
        cost = sum(_price_usage(call, model, snapshot) for call in usable_calls)
        return {"cost_usd": cost, "precision": "per_api_call"}
    if not any(_number(value) is not None for value in aggregate.values()):
        return {"cost_usd": None, "precision": "usage_unavailable"}
    cost = _price_usage(aggregate, model, snapshot, apply_long_context=False)
    precision = "aggregate_tokens"
    if model.get("long_context"):
        precision = "aggregate_tokens_long_context_unresolved"
    return {"cost_usd": cost, "precision": precision}


def _price_usage(
    usage: dict[str, Number | None],
    model: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    apply_long_context: bool = True,
) -> float:
    rates = model["rates_per_million_tokens"]
    unit = float(snapshot.get("unit_tokens") or 1_000_000)
    prompt = float(_number(usage.get("prompt_tokens")) or 0)
    output = float(_number(usage.get("completion_tokens")) or 0)
    cached = float(_number(usage.get("cached_tokens")) or 0)
    created_5m = float(_number(usage.get("cache_creation_5m_tokens")) or 0)
    created_1h = float(_number(usage.get("cache_creation_1h_tokens")) or 0)
    created_total = float(_number(usage.get("cache_creation_tokens")) or 0)
    created_unknown = max(0.0, created_total - created_5m - created_1h)
    uncached = max(0.0, prompt - cached - created_total)

    input_multiplier = 1.0
    output_multiplier = 1.0
    long_context = model.get("long_context") or {}
    threshold = _number(long_context.get("threshold_input_tokens_exclusive"))
    if apply_long_context and threshold is not None and prompt > threshold:
        input_multiplier = float(long_context.get("input_multiplier") or 1.0)
        output_multiplier = float(long_context.get("output_multiplier") or 1.0)

    default_creation_rate = rates.get(
        "cache_creation_5m", rates.get("uncached_input", 0.0)
    )
    input_cost = (
        uncached * float(rates.get("uncached_input", 0.0))
        + cached * float(rates.get("cached_input", rates.get("uncached_input", 0.0)))
        + created_5m * float(rates.get("cache_creation_5m", default_creation_rate))
        + created_1h * float(rates.get("cache_creation_1h", default_creation_rate))
        + created_unknown * float(default_creation_rate)
    )
    output_cost = output * float(rates.get("output", 0.0))
    return (input_cost * input_multiplier + output_cost * output_multiplier) / unit


def _load_trajectory(trial_dir: Path) -> dict[str, Any] | None:
    candidates = (
        trial_dir / "agent" / "trajectory.json",
        trial_dir / "artifacts" / "agent_trajectory.json",
    )
    for path in candidates:
        document = _read_json(path)
        if isinstance(document, dict):
            return document
    return None


def _step_usage(
    trajectory: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Number | None]]]:
    totals: dict[str, Number] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_creation_5m_tokens": 0,
        "cache_creation_1h_tokens": 0,
        "tool_tokens": 0,
    }
    seen: set[str] = set()
    provider_fields: dict[str, Number] = {}
    calls: list[dict[str, Number | None]] = []
    for step in (trajectory or {}).get("steps") or []:
        metrics = step.get("metrics") or {}
        if not isinstance(metrics, dict):
            continue
        extra = metrics.get("extra") or {}
        prompt = _number(metrics.get("prompt_tokens"))
        completion = _number(metrics.get("completion_tokens"))
        cached = _number(metrics.get("cached_tokens"))
        reasoning = _sum_known(
            extra, ("reasoning_output_tokens", "reasoning_tokens", "thoughts_tokens")
        )
        cache_5m, cache_1h = _cache_ttl(extra)
        creation = _sum_known(
            extra,
            (
                "cache_creation_input_tokens",
                "cache_write_input_tokens",
                "input_cache_creation",
            ),
        )
        if creation is None and (cache_5m is not None or cache_1h is not None):
            creation = (cache_5m or 0) + (cache_1h or 0)
        tool = _sum_known(extra, ("tool_tokens",))
        values = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "cache_creation_tokens": creation,
            "cache_creation_5m_tokens": cache_5m,
            "cache_creation_1h_tokens": cache_1h,
            "tool_tokens": tool,
        }
        for key, value in values.items():
            if value is not None:
                totals[key] += value
                seen.add(key)
        for key, value in _token_fields(extra).items():
            if _number(value) is not None:
                provider_fields[key] = provider_fields.get(key, 0) + value
        if prompt is not None or completion is not None:
            calls.append(values)
    result = {key: value for key, value in totals.items() if key in seen}
    result["provider_fields"] = provider_fields
    return result, calls


def _pi_usage(
    trial_dir: Path,
) -> tuple[dict[str, Number], list[dict[str, Number | None]]]:
    path = trial_dir / "agent" / "pi.txt"
    if not path.is_file():
        return {}, []
    totals: dict[str, Number] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
    }
    calls: list[dict[str, Number | None]] = []
    seen = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage") or {}
        uncached = _number(usage.get("input")) or 0
        cached = _number(usage.get("cacheRead")) or 0
        created = _number(usage.get("cacheWrite")) or 0
        output = _number(usage.get("output")) or 0
        cost = _number((usage.get("cost") or {}).get("total")) or 0.0
        call = {
            "prompt_tokens": uncached + cached + created,
            "completion_tokens": output,
            "cached_tokens": cached,
            "cache_creation_tokens": created,
            "cache_creation_5m_tokens": None,
            "cache_creation_1h_tokens": None,
        }
        calls.append(call)
        totals["prompt_tokens"] += call["prompt_tokens"] or 0
        totals["completion_tokens"] += output
        totals["cache_read_tokens"] += cached
        totals["cache_creation_tokens"] += created
        totals["cost_usd"] += cost
        seen = True
    return (totals if seen else {}), calls


def _reported_cost_provenance(
    agent_name: str | None, final_extra: dict[str, Any], cost: Number | None
) -> tuple[str, str | None]:
    if cost is None:
        return "unavailable", None
    explicit = final_extra.get("cost_source")
    if explicit == "litellm_estimate":
        return "estimated", "harbor_litellm_estimate"
    if agent_name in {"codex", "gemini-cli", "mini-swe-agent"}:
        return "estimated", "harbor_adapter_estimate"
    if agent_name == "pi":
        return "reported", "pi_cli_usage"
    if agent_name == "claude-code":
        return "reported", "claude_code_total_cost_usd"
    return "unknown", explicit or "harbor_agent_result"


def _cache_ttl(extra: dict[str, Any]) -> tuple[Number | None, Number | None]:
    cache = extra.get("cache_creation") or {}
    if not isinstance(cache, dict):
        cache = {}
    five = _first_number(
        cache.get("ephemeral_5m_input_tokens"),
        extra.get("cache_creation_5m_input_tokens"),
    )
    hour = _first_number(
        cache.get("ephemeral_1h_input_tokens"),
        extra.get("cache_creation_1h_input_tokens"),
    )
    return five, hour


def _token_fields(value: dict[str, Any], prefix: str = "") -> dict[str, Number]:
    fields: dict[str, Number] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            fields.update(_token_fields(item, name))
        elif ("token" in key.lower() or "cache" in key.lower()) and _number(
            item
        ) is not None:
            fields[name] = item
    return fields


def _sum_known(document: dict[str, Any], names: tuple[str, ...]) -> Number | None:
    values = [_number(document.get(name)) for name in names]
    selected = [value for value in values if value is not None]
    return sum(selected) if selected else None


def _first_number(*values: Any) -> Number | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> Number | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
