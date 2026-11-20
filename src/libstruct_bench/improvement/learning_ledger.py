from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
)

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    validate_digest,
    with_digest,
)


LEARNING_LEDGER_SCHEMA_VERSION = "libstruct.libgen_capability_learning_ledger.v1"
ERROR_ANALYSIS_SCHEMA_VERSION = "libstruct.libgen_error_analysis.v2"
_FINDING_CODE_RE = re.compile(r"[^a-z0-9]+")
_VALID_SUPPORT = frozenset(
    {
        "explicit",
        "derivable",
        "externally_completed",
        "ambiguous",
        "unsupported",
    }
)
_VALID_RECOVERABILITY = frozenset(
    {"recoverable", "neutral", "unresolved", "not_applicable"}
)
_EXCLUSION_REASONS = (
    "infrastructure",
    "evaluator_defect",
    "policy_defect",
    "ground_truth_defect",
)


def build_learning_ledger(
    *,
    batch_id: str,
    protocol_ids: Sequence[str],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Normalize revealed verifier discrepancies into stable, trusted clusters.

    A concealed packet has no verifier-error-analysis artifacts and therefore no
    ledger.  Once any such artifact is present, exactly one is required for each
    protocol so a proposer cannot learn from a selectively staged subset.
    """

    protocols = tuple(protocol_ids)
    active_error_artifacts = _artifacts_by_role(
        artifacts, role="verifier_error_analysis"
    )
    if not active_error_artifacts:
        return None
    active_reward_artifacts = _artifacts_by_role(artifacts, role="verifier_reward")
    c0_error_artifacts = _artifacts_by_role(
        artifacts, role="c0_verifier_error_analysis"
    )
    c0_reward_artifacts = _artifacts_by_role(artifacts, role="c0_verifier_reward")
    _require_protocol_coverage(
        active_error_artifacts,
        protocols=protocols,
        label="verifier error analysis",
    )
    _require_protocol_coverage(
        active_reward_artifacts,
        protocols=protocols,
        label="verifier reward",
    )
    if bool(c0_error_artifacts) != bool(c0_reward_artifacts):
        raise CapabilityImprovementError(
            "prospective learning requires both C0 reward and error summaries"
        )
    if c0_error_artifacts:
        _require_protocol_coverage(
            c0_error_artifacts,
            protocols=protocols,
            label="C0 verifier error analysis",
        )
        _require_protocol_coverage(
            c0_reward_artifacts,
            protocols=protocols,
            label="C0 verifier reward",
        )

    active_errors = {
        protocol_id: _load_error_analysis(
            active_error_artifacts[protocol_id], protocol_id=protocol_id
        )
        for protocol_id in protocols
    }
    active_rewards = {
        protocol_id: _load_reward(active_reward_artifacts[protocol_id])
        for protocol_id in protocols
    }
    c0_errors = {
        protocol_id: _load_error_analysis(
            c0_error_artifacts[protocol_id], protocol_id=protocol_id
        )
        for protocol_id in protocols
        if c0_error_artifacts
    }
    c0_rewards = {
        protocol_id: _load_reward(c0_reward_artifacts[protocol_id])
        for protocol_id in protocols
        if c0_reward_artifacts
    }

    c0_signature_counts: dict[tuple[str, str], int] = defaultdict(int)
    for protocol_id, document in c0_errors.items():
        for observation in document["observations"]:
            if (
                observation.get("substantive") is not True
                or _observation_exclusion_reason(observation) is not None
            ):
                continue
            signature = _root_error_signature(observation)
            c0_signature_counts[(protocol_id, canonical_digest(signature))] += 1

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    signatures: dict[str, dict[str, Any]] = {}
    excluded_by_reason = {reason: 0 for reason in _EXCLUSION_REASONS}
    substantive_observation_count = 0
    admitted_observation_count = 0
    source_artifacts: list[dict[str, str]] = []
    for role, values in (
        ("verifier_error_analysis", active_error_artifacts),
        ("verifier_reward", active_reward_artifacts),
        ("c0_verifier_error_analysis", c0_error_artifacts),
        ("c0_verifier_reward", c0_reward_artifacts),
    ):
        for protocol_id in sorted(values):
            source_artifacts.append(
                {
                    "protocol_id": protocol_id,
                    "role": role,
                    "artifact_sha256": str(values[protocol_id]["sha256"]),
                }
            )
    for protocol_id in sorted(protocols):
        artifact = active_error_artifacts[protocol_id]
        document = active_errors[protocol_id]
        for index, observation in enumerate(document["observations"]):
            if observation.get("substantive") is not True:
                continue
            substantive_observation_count += 1
            exclusion_reason = _observation_exclusion_reason(observation)
            if exclusion_reason is not None:
                excluded_by_reason[exclusion_reason] += 1
                continue
            admitted_observation_count += 1
            normalized = _normalize_observation(
                observation,
                protocol_id=protocol_id,
                artifact_sha256=str(artifact["sha256"]),
                index=index,
                active_reward=active_rewards[protocol_id],
                c0_reward=c0_rewards.get(protocol_id),
                active_reward_sha256=str(
                    active_reward_artifacts[protocol_id]["sha256"]
                ),
                c0_reward_sha256=(
                    str(c0_reward_artifacts[protocol_id]["sha256"])
                    if c0_reward_artifacts
                    else None
                ),
            )
            signature = normalized.pop("signature")
            finding_codes = normalized.pop("finding_codes")
            signature_digest = canonical_digest(signature)
            normalized["evidence"]["c0_matching_observation_count"] = (
                c0_signature_counts[(protocol_id, signature_digest)]
            )
            signatures[signature_digest] = signature
            normalized["finding_codes"] = finding_codes
            grouped[signature_digest].append(normalized)

    clusters: list[dict[str, Any]] = []
    for signature_digest in sorted(grouped):
        values = grouped[signature_digest]
        signature = signatures[signature_digest]
        evidence_refs = sorted(
            (item["evidence"] for item in values),
            key=lambda item: (
                item["protocol_id"],
                item["artifact_sha256"],
                item["json_pointer"],
            ),
        )
        cluster_protocols = sorted({item["protocol_id"] for item in evidence_refs})
        finding_codes = sorted(
            {code for value in values for code in value["finding_codes"]}
        )
        affected_metrics = sorted(
            {metric for item in evidence_refs for metric in item["affected_metrics"]}
        )
        clusters.append(
            {
                "cluster_id": f"cluster_{signature_digest[:24]}",
                "task": signature["task"],
                "category": signature["category"],
                "entity_type": signature["entity_type"],
                "process_cause": signature["process_cause"],
                "finding_codes": finding_codes,
                "affected_metrics": affected_metrics,
                "support_statuses": sorted({item["support"] for item in evidence_refs}),
                "recoverability_statuses": sorted(
                    {item["recoverability"] for item in evidence_refs}
                ),
                "observation_count": len(evidence_refs),
                "protocol_count": len(cluster_protocols),
                "protocol_ids": cluster_protocols,
                "root_error_status": (
                    "recurring_across_protocols"
                    if len(cluster_protocols) >= 2
                    else "singleton"
                ),
                "evidence_refs": evidence_refs,
                "metric_effects": _aggregate_metric_effects(
                    evidence_refs, affected_metrics
                ),
                "c0_observation_count": sum(
                    item["c0_matching_observation_count"] for item in evidence_refs
                ),
            }
        )
    payload = {
        "schema_version": LEARNING_LEDGER_SCHEMA_VERSION,
        "batch_id": batch_id,
        "protocol_ids": list(protocols),
        "source_artifacts": source_artifacts,
        "filter_summary": {
            "substantive_observation_count": substantive_observation_count,
            "admitted_observation_count": admitted_observation_count,
            "excluded_observation_count": sum(excluded_by_reason.values()),
            "admitted_root_event_count": len(clusters),
            "collapsed_observation_count": max(
                0, admitted_observation_count - len(clusters)
            ),
            "excluded_by_reason": excluded_by_reason,
        },
        "clusters": clusters,
    }
    ledger = with_digest(payload, "ledger_digest")
    validate_document(
        ledger,
        improvement_schema_root() / "learning_ledger.schema.json",
        label="capability learning ledger",
    )
    return ledger


def validate_learning_ledger(
    ledger: Mapping[str, Any] | None,
    *,
    batch_id: str,
    protocol_ids: Sequence[str],
    artifacts: Sequence[Mapping[str, Any]],
    require_revealed: bool,
) -> dict[str, Any] | None:
    """Recompute the ledger from pinned artifacts and reject authored variants."""

    expected = build_learning_ledger(
        batch_id=batch_id,
        protocol_ids=protocol_ids,
        artifacts=artifacts,
    )
    if require_revealed and expected is None:
        raise CapabilityImprovementError(
            "revealed capability packet requires a deterministic learning ledger"
        )
    if ledger != expected:
        raise CapabilityImprovementError(
            "capability learning ledger differs from deterministic error-analysis "
            "normalization"
        )
    if ledger is not None:
        validate_digest(ledger, "ledger_digest")
        validate_document(
            dict(ledger),
            improvement_schema_root() / "learning_ledger.schema.json",
            label="capability learning ledger",
        )
        return dict(ledger)
    return None


def _normalize_observation(
    observation: Mapping[str, Any],
    *,
    protocol_id: str,
    artifact_sha256: str,
    index: int,
    active_reward: Mapping[str, float],
    c0_reward: Mapping[str, float] | None,
    active_reward_sha256: str,
    c0_reward_sha256: str | None,
) -> dict[str, Any]:
    detailed_signature = _observation_signature(observation)
    signature = _root_error_signature(observation)
    metrics = detailed_signature["affected_metrics"]
    support = observation.get("source_support_status")
    support = support if support in _VALID_SUPPORT else "unknown"
    recoverability = observation.get("claim_recoverability")
    recoverability = (
        recoverability if recoverability in _VALID_RECOVERABILITY else "unknown"
    )
    return {
        "signature": signature,
        "finding_codes": detailed_signature["finding_codes"],
        "evidence": {
            "protocol_id": protocol_id,
            "artifact_sha256": artifact_sha256,
            "json_pointer": f"/observations/{index}",
            "support": support,
            "recoverability": recoverability,
            "affected_metrics": metrics,
            "active_reward_sha256": active_reward_sha256,
            "c0_reward_sha256": c0_reward_sha256,
            "metric_effects": [
                _metric_effect(
                    metric,
                    active_reward=active_reward,
                    c0_reward=c0_reward,
                )
                for metric in metrics
            ],
            "c0_matching_observation_count": 0,
        },
    }


def _observation_signature(observation: Mapping[str, Any]) -> dict[str, Any]:
    task = _required_text(observation.get("task"), "task")
    category = _required_text(observation.get("category"), "category")
    entity_type = _required_text(observation.get("entity_type"), "entity_type")
    process_cause = _text_or_unknown(observation.get("process_cause"))
    metrics = sorted(
        {
            str(item).strip()
            for item in observation.get("affected_metrics", [])
            if isinstance(item, str) and item.strip()
        }
    )
    # Error-analysis signals often contain comparison scores, for example
    # ``architecture=0.715556``.  Scores describe the current observation; they
    # must not become cluster identity or finding-code vocabulary.  Split
    # compound signals and retain only the semantic label before ``=``.  The
    # stable category code gives every cluster one general code even when a
    # verifier emits no signal details.
    finding_codes = {_finding_code(category)}
    for item in observation.get("signals", []):
        if not isinstance(item, str):
            continue
        for clause in re.split(r"[;,]", item):
            label = clause.split("=", 1)[0].strip()
            if label:
                finding_codes.add(_finding_code(label))
    return {
        "task": task,
        "category": category,
        "entity_type": entity_type,
        "process_cause": process_cause,
        "finding_codes": sorted(finding_codes),
        "affected_metrics": metrics,
    }


def _root_error_signature(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the metric-independent identity of one procedural root error."""

    return {
        "task": _required_text(observation.get("task"), "task"),
        "category": _required_text(observation.get("category"), "category"),
        "entity_type": _required_text(observation.get("entity_type"), "entity_type"),
        "process_cause": _text_or_unknown(observation.get("process_cause")),
    }


def _observation_exclusion_reason(
    observation: Mapping[str, Any],
) -> str | None:
    """Exclude benchmark and runtime defects from capability learning."""

    if observation.get("attribution") == "infrastructure":
        return "infrastructure"
    validity = {
        value
        for value in (
            observation.get("benchmark_validity"),
            observation.get("benchmark_validity_candidate"),
        )
        if isinstance(value, str)
    }
    if "evaluator_defect" in validity:
        return "evaluator_defect"
    if validity & {"policy_ambiguity", "source_scope_mismatch", "source_conflict"}:
        return "policy_defect"
    if "ground_truth_defect" in validity or observation.get("attribution") in {
        "benchmark",
        "mixed",
    }:
        return "ground_truth_defect"
    # Human-adjudicated overlays are stricter than legacy automatic analyses:
    # only confirmed benchmark-valid, agent-attributed root errors may teach the
    # capability pack.  The conditional preserves byte-for-byte recomputation of
    # historical, still-pending ledgers.
    if observation.get("adjudication_status") == "complete":
        if observation.get("benchmark_validity") != "valid":
            return "policy_defect"
        if observation.get("attribution") != "agent":
            return "ground_truth_defect"
    return None


def _metric_effect(
    metric: str,
    *,
    active_reward: Mapping[str, float],
    c0_reward: Mapping[str, float] | None,
) -> dict[str, Any]:
    active = active_reward.get(metric)
    c0 = None if c0_reward is None else c0_reward.get(metric)
    paired = active is not None and c0 is not None
    return {
        "metric": metric,
        "active_score": active,
        "c0_score": c0,
        "paired_delta": active - c0 if paired else None,
        "status": "paired" if paired else "active_only_delta_unavailable",
    }


def _aggregate_metric_effects(
    evidence_refs: Sequence[Mapping[str, Any]],
    metrics: Sequence[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for metric in metrics:
        effects = [
            effect
            for evidence in evidence_refs
            for effect in evidence["metric_effects"]
            if effect["metric"] == metric
        ]
        active = [
            item["active_score"] for item in effects if item["active_score"] is not None
        ]
        c0 = [item["c0_score"] for item in effects if item["c0_score"] is not None]
        deltas = [
            item["paired_delta"] for item in effects if item["paired_delta"] is not None
        ]
        result.append(
            {
                "metric": metric,
                "active_score": sum(active) / len(active) if active else None,
                "c0_score": sum(c0) / len(c0) if c0 else None,
                "paired_delta": sum(deltas) / len(deltas) if deltas else None,
                "status": "paired" if deltas else "active_only_delta_unavailable",
            }
        )
    return result


def _artifacts_by_role(
    artifacts: Sequence[Mapping[str, Any]],
    *,
    role: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for artifact in artifacts:
        if artifact.get("role") != role:
            continue
        protocol_id = artifact.get("protocol_id")
        if not isinstance(protocol_id, str) or protocol_id in result:
            raise CapabilityImprovementError(
                f"learning ledger requires one {role} per protocol"
            )
        result[protocol_id] = artifact
    return result


def _require_protocol_coverage(
    values: Mapping[str, Mapping[str, Any]],
    *,
    protocols: Sequence[str],
    label: str,
) -> None:
    if set(values) != set(protocols) or len(values) != len(protocols):
        raise CapabilityImprovementError(
            f"learning ledger requires one {label} for every batch protocol"
        )


def _load_error_analysis(
    artifact: Mapping[str, Any],
    *,
    protocol_id: str,
) -> dict[str, Any]:
    document = _load_pinned_json(artifact, label="verifier error analysis")
    try:
        validate_document(
            document,
            improvement_schema_root().parent
            / "analysis"
            / "libgen_error_analysis.schema.json",
            label="verifier error analysis",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    if document.get("schema_version") != ERROR_ANALYSIS_SCHEMA_VERSION:
        raise CapabilityImprovementError(
            "learning ledger received an unsupported error-analysis schema"
        )
    if document.get("protocol_id") != protocol_id:
        raise CapabilityImprovementError(
            "learning-ledger source protocol differs from packet artifact"
        )
    return document


def _load_reward(artifact: Mapping[str, Any]) -> dict[str, float]:
    document = _load_pinned_json(artifact, label="verifier reward")
    result: dict[str, float] = {}
    for key, value in document.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            raise CapabilityImprovementError(
                "verifier reward contains a non-finite score"
            )
        result[str(key)] = numeric
    return result


def _load_pinned_json(
    artifact: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    path = Path(str(artifact.get("path", ""))).expanduser()
    if path.is_symlink() or not path.is_file():
        raise CapabilityImprovementError(f"{label} is not a regular file: {path}")
    actual_sha = sha256_file(path)
    if artifact.get("sha256") != actual_sha:
        raise CapabilityImprovementError(f"{label} hash is stale: {path}")
    try:
        return load_json_object(path, label=label)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _finding_code(value: str) -> str:
    base = value.split("=", 1)[0].strip().lower()
    normalized = _FINDING_CODE_RE.sub("_", base).strip("_") or "unspecified"
    if normalized[0].isdigit():
        normalized = "finding_" + normalized
    return normalized[:240]


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityImprovementError(f"learning-ledger observation has no {label}")
    return value.strip()


def _text_or_unknown(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "unknown"
