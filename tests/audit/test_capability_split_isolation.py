from __future__ import annotations

import copy
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    with_digest,
)
from libstruct_bench.improvement.governance import (
    build_transfer_access_policy,
    validate_transfer_policy_panel_binding,
)
from libstruct_bench.improvement.split_design import (
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_TRANSFER_PANEL,
)
from libstruct_bench.improvement.split_freeze import (
    build_test_isolation_audit,
    validate_test_isolation_audit,
)


NOW = "2026-08-22T12:00:00Z"


def test_isolation_audit_detects_protocol_identifier_in_path_only(
    tmp_path: Path,
) -> None:
    artifact = (
        tmp_path
        / "rounds"
        / "B1"
        / "autonomous"
        / "proposer-workspace"
        / "cel_seq2"
        / "artifact.txt"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("neutral content\n", encoding="utf-8")

    audit = build_test_isolation_audit(
        experiment_root=tmp_path,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
    )

    proposal = _check(audit, "proposal_contribution")
    assert proposal["status"] == "fail"
    assert proposal["findings"][0]["protocol_id"] == "cel_seq2"
    assert audit["learning_isolation"] == "fail"


def test_isolation_audit_fails_closed_for_generic_round_artifact(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "rounds" / "B1" / "autonomous" / "cel_seq2" / "notes.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"opaque content\n")

    audit = build_test_isolation_audit(
        experiment_root=tmp_path,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
    )

    contribution = _check(audit, "capability_update_contribution")
    assert contribution["status"] == "fail"
    assert contribution["findings"] == [
        {
            "protocol_id": "cel_seq2",
            "path": "rounds/B1/autonomous/cel_seq2/notes.bin",
            "sha256": contribution["findings"][0]["sha256"],
        }
    ]
    assert audit["learning_isolation"] == "fail"


def test_isolation_audit_scans_historical_learning_trees_not_design(
    tmp_path: Path,
) -> None:
    archived = tmp_path / "history" / "superseded" / "split"
    design = archived / "design" / "experiment_manifest.json"
    design.parent.mkdir(parents=True)
    design.write_text('{"protocol_ids":["cel_seq2"]}\n', encoding="utf-8")
    artifact = archived / "rounds" / "B1" / "autonomous" / "cel_seq2" / "notes.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"opaque content\n")

    audit = build_test_isolation_audit(
        experiment_root=tmp_path,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
    )

    contribution = _check(audit, "capability_update_contribution")
    assert contribution["status"] == "fail"
    assert [item["path"] for item in contribution["findings"]] == [
        "history/superseded/split/rounds/B1/autonomous/cel_seq2/notes.bin"
    ]
    assert all(
        item["path"] != "history/superseded/split/design/experiment_manifest.json"
        for check in audit["checks"]
        for item in check["findings"]
    )
    assert audit["learning_isolation"] == "fail"


def test_isolation_audit_detects_exact_blocked_test_file_hash(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "private" / "opaque-source" / "source.bin"
    blocked.parent.mkdir(parents=True)
    blocked.write_bytes(b"opaque-test-evidence\n")
    copied = (
        tmp_path
        / "experiment"
        / "rounds"
        / "B1"
        / "autonomous"
        / "proposer-workspace"
        / "candidate.txt"
    )
    copied.parent.mkdir(parents=True)
    copied.write_bytes(blocked.read_bytes())
    policy = {
        "blocked_protocol_ids": list(FINAL_TRANSFER_PANEL),
        "blocked_trees": [
            {
                "protocol_id": "cel_seq2",
                "role": "target_source",
                "path": blocked.parent.as_posix(),
            }
        ],
    }

    audit = build_test_isolation_audit(
        experiment_root=tmp_path / "experiment",
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
        transfer_access_policy=policy,
    )

    proposal = _check(audit, "proposal_contribution")
    assert proposal["status"] == "fail"
    assert proposal["findings"][0]["protocol_id"] == "cel_seq2"


def test_isolation_validator_rejects_status_that_contradicts_findings(
    tmp_path: Path,
) -> None:
    audit = build_test_isolation_audit(
        experiment_root=tmp_path,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
    )
    invalid = copy.deepcopy(audit)
    invalid.pop("audit_digest")
    invalid["checks"][0]["status"] = "fail"
    invalid = with_digest(invalid, "audit_digest")
    path = tmp_path / "audit.json"
    write_json_atomic(path, invalid)

    with pytest.raises(
        CapabilityImprovementError,
        match="status contradicts findings",
    ):
        validate_test_isolation_audit(path)


def test_isolation_validator_requires_exact_categories_and_panel(
    tmp_path: Path,
) -> None:
    audit = build_test_isolation_audit(
        experiment_root=tmp_path,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=NOW,
    )
    repeated = copy.deepcopy(audit)
    repeated.pop("audit_digest")
    repeated["checks"][1]["category"] = repeated["checks"][0]["category"]
    repeated = with_digest(repeated, "audit_digest")
    repeated_path = tmp_path / "repeated.json"
    write_json_atomic(repeated_path, repeated)
    with pytest.raises(CapabilityImprovementError, match="exact eight"):
        validate_test_isolation_audit(repeated_path)

    reordered = copy.deepcopy(audit)
    reordered.pop("audit_digest")
    reordered["protocol_ids"] = list(reversed(reordered["protocol_ids"]))
    reordered = with_digest(reordered, "audit_digest")
    reordered_path = tmp_path / "reordered.json"
    write_json_atomic(reordered_path, reordered)
    with pytest.raises(CapabilityImprovementError, match="another frozen panel"):
        validate_test_isolation_audit(reordered_path)


def test_transfer_policy_binding_requires_exact_panel_and_source_truth_trees(
    tmp_path: Path,
) -> None:
    valid, _, _ = _transfer_policy(tmp_path)
    validate_transfer_policy_panel_binding(
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        policy=valid,
    )

    missing_protocol = copy.deepcopy(valid)
    missing_protocol["blocked_protocol_ids"].pop()
    with pytest.raises(CapabilityImprovementError, match="blocked_protocol_ids"):
        validate_transfer_policy_panel_binding(
            panel_protocol_ids=FINAL_TRANSFER_PANEL,
            policy=missing_protocol,
        )

    missing_tree = copy.deepcopy(valid)
    missing_tree["blocked_trees"] = [
        item
        for item in missing_tree["blocked_trees"]
        if not (
            item["protocol_id"] == "cel_seq2" and item["role"] == "approved_groundtruth"
        )
    ]
    with pytest.raises(CapabilityImprovementError, match="exactly one"):
        validate_transfer_policy_panel_binding(
            panel_protocol_ids=FINAL_TRANSFER_PANEL,
            policy=missing_tree,
        )


@pytest.mark.parametrize(
    ("role", "root_name", "mutation_kind"),
    (
        ("target_source", "sources", "content"),
        ("approved_groundtruth", "truth", "file_count"),
    ),
)
def test_transfer_policy_binding_rejects_changed_blocked_tree_fingerprint(
    tmp_path: Path,
    role: str,
    root_name: str,
    mutation_kind: str,
) -> None:
    policy, sources, truth = _transfer_policy(tmp_path)
    root = sources if root_name == "sources" else truth
    filename = "artifact.txt" if mutation_kind == "content" else "added.txt"
    (root / FINAL_TRANSFER_PANEL[0] / filename).write_text(
        f"changed {role}\n",
        encoding="utf-8",
    )

    with pytest.raises(CapabilityImprovementError, match="fingerprint changed"):
        validate_transfer_policy_panel_binding(
            panel_protocol_ids=FINAL_TRANSFER_PANEL,
            policy=policy,
        )


def _check(audit: dict, category: str) -> dict:
    return next(item for item in audit["checks"] if item["category"] == category)


def _transfer_policy(tmp_path: Path) -> tuple[dict, Path, Path]:
    sources = tmp_path / "sources"
    truth = tmp_path / "truth"
    for protocol_id in FINAL_TRANSFER_PANEL:
        for root, value in ((sources, "source"), (truth, "truth")):
            artifact = root / protocol_id / "artifact.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"{value} for {protocol_id}\n", encoding="utf-8")
    policy = build_transfer_access_policy(
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        panel_commitment_sha256="1" * 64,
        source_root=sources,
        groundtruth_root=truth,
        baseline_run_roots=(),
    )
    return policy, sources, truth
