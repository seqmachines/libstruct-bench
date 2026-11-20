#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


WORK_RECORD_SCHEMA_VERSION = "libstruct.libgen_capability_work_record.v1"
ACTIVE_DISPOSITIONS = {"modeled", "merged", "folded"}
ALL_DISPOSITIONS = ACTIVE_DISPOSITIONS | {"excluded", "unresolved"}
SUPPORT_VALUES = {"explicit", "derivable", "ambiguous", "unsupported"}
USAGE_SCOPES = {
    "physical_library_generation",
    "sequencing_only",
    "control_only",
    "procedural_only",
    "unknown",
}
ITEM_KINDS = {
    "oligo",
    "molecular_event",
    "molecular_state",
    "branch",
    "terminal",
}


class WorkRecordError(ValueError):
    """Raised when a work record cannot be deterministically interpreted."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_object(path: str | Path, label: str) -> dict[str, Any]:
    candidate = Path(path)
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkRecordError(f"cannot read {label} {candidate}: {error}") from error
    if not isinstance(value, dict):
        raise WorkRecordError(f"{label} must be a JSON object")
    return value


def object_list(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise WorkRecordError(f"{label} must be an array of objects")
    return value


def string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WorkRecordError(f"{label} must be an array of strings")
    return value


def validate_work_record(record: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "protocol_id",
        "source_coverage",
        "inventory",
        "claims",
        "state_signatures",
        "event_records",
        "drafts",
    }
    missing = sorted(required - set(record))
    extra = sorted(set(record) - required)
    if missing or extra:
        raise WorkRecordError(
            f"work record fields differ; missing={missing}, extra={extra}"
        )
    if record.get("schema_version") != WORK_RECORD_SCHEMA_VERSION:
        raise WorkRecordError("unsupported work-record schema_version")
    protocol_id = record.get("protocol_id")
    if not isinstance(protocol_id, str) or not protocol_id:
        raise WorkRecordError("work record protocol_id must be a non-empty string")

    coverage = object_list(record.get("source_coverage"), "source_coverage")
    if not coverage:
        raise WorkRecordError("source_coverage must not be empty")
    source_ids: set[str] = set()
    for index, source in enumerate(coverage):
        source_id = _required_string(source, "source_id", f"source_coverage/{index}")
        if source_id in source_ids:
            raise WorkRecordError(f"duplicate source_id {source_id!r}")
        source_ids.add(source_id)
        _required_string(source, "artifact", f"source_coverage/{index}")
        if source.get("coverage_status") not in {
            "inspected",
            "partially_inspected",
            "unavailable",
        }:
            raise WorkRecordError(
                f"source_coverage/{index} has invalid coverage_status"
            )
        string_list(source.get("locators"), f"source_coverage/{index}/locators")
        if not isinstance(source.get("notes"), str):
            raise WorkRecordError(f"source_coverage/{index}/notes must be a string")

    inventory = object_list(record.get("inventory"), "inventory")
    if not inventory:
        raise WorkRecordError("inventory must not be empty")
    inventory_ids: set[str] = set()
    for index, item in enumerate(inventory):
        location = f"inventory/{index}"
        inventory_id = _required_string(item, "inventory_id", location)
        if inventory_id in inventory_ids:
            raise WorkRecordError(f"duplicate inventory_id {inventory_id!r}")
        inventory_ids.add(inventory_id)
        _required_string(item, "name", location)
        if item.get("item_kind") not in ITEM_KINDS:
            raise WorkRecordError(f"{location} has invalid item_kind")
        if item.get("support") not in SUPPORT_VALUES:
            raise WorkRecordError(f"{location} has invalid support")
        derivation = item.get("derivation")
        if item.get("support") == "derivable" and (
            not isinstance(derivation, str) or not derivation.strip()
        ):
            raise WorkRecordError(f"{location} derivable support requires derivation")
        if derivation is not None and not isinstance(derivation, str):
            raise WorkRecordError(f"{location}/derivation must be a string or null")
        locators = object_list(item.get("source_locators"), f"{location}/source_locators")
        if not locators:
            raise WorkRecordError(f"{location}/source_locators must not be empty")
        for li, locator in enumerate(locators):
            source_id = _required_string(locator, "source_id", f"{location}/source_locators/{li}")
            _required_string(locator, "locator", f"{location}/source_locators/{li}")
            if source_id not in source_ids:
                raise WorkRecordError(
                    f"{location}/source_locators/{li} references unknown source {source_id!r}"
                )
        if item.get("usage_scope") not in USAGE_SCOPES:
            raise WorkRecordError(f"{location} has invalid usage_scope")
        disposition = item.get("disposition")
        if disposition not in ALL_DISPOSITIONS:
            raise WorkRecordError(f"{location} has invalid disposition")
        _required_string(item, "disposition_reason", location)
        pointers = object_list(item.get("target_pointers"), f"{location}/target_pointers")
        if disposition in ACTIVE_DISPOSITIONS and not pointers:
            raise WorkRecordError(f"{location} active disposition requires a target pointer")
        if disposition not in ACTIVE_DISPOSITIONS and pointers:
            raise WorkRecordError(f"{location} inactive disposition cannot have target pointers")
        seen_pointers: set[tuple[str, str]] = set()
        for pi, pointer in enumerate(pointers):
            target = pointer.get("target")
            json_pointer = pointer.get("json_pointer")
            if target not in {"t2", "t3"} or not isinstance(json_pointer, str) or not json_pointer.startswith("/"):
                raise WorkRecordError(f"{location}/target_pointers/{pi} is invalid")
            key = (target, json_pointer)
            if key in seen_pointers:
                raise WorkRecordError(f"{location} repeats target pointer {key}")
            seen_pointers.add(key)

    claims = object_list(record.get("claims"), "claims")
    claim_ids: set[str] = set()
    claim_pointers: set[tuple[str, str]] = set()
    for index, claim in enumerate(claims):
        location = f"claims/{index}"
        claim_id = _required_string(claim, "claim_id", location)
        if claim_id in claim_ids:
            raise WorkRecordError(f"duplicate claim_id {claim_id!r}")
        claim_ids.add(claim_id)
        target = claim.get("target")
        pointer = claim.get("json_pointer")
        if target not in {"t2", "t3"} or not isinstance(pointer, str) or not pointer.startswith("/"):
            raise WorkRecordError(f"{location} has invalid target or json_pointer")
        if (target, pointer) in claim_pointers:
            raise WorkRecordError(f"duplicate claim pointer {target}:{pointer}")
        claim_pointers.add((target, pointer))
        if claim.get("support") not in SUPPORT_VALUES:
            raise WorkRecordError(f"{location} has invalid support")
        if claim.get("support") == "derivable" and (
            not isinstance(claim.get("derivation"), str)
            or not claim["derivation"].strip()
        ):
            raise WorkRecordError(f"{location} derivable support requires derivation")
        if not string_list(claim.get("source_locators"), f"{location}/source_locators"):
            raise WorkRecordError(f"{location}/source_locators must not be empty")

    signatures = object_list(record.get("state_signatures"), "state_signatures")
    signature_ids: set[tuple[str, str]] = set()
    for index, signature in enumerate(signatures):
        location = f"state_signatures/{index}"
        key = (
            _required_string(signature, "workflow_id", location),
            _required_string(signature, "state_id", location),
        )
        if key in signature_ids:
            raise WorkRecordError(f"duplicate state signature {key}")
        signature_ids.add(key)
        _validate_inventory_ids(signature, location, inventory_ids)
        _required_string(signature, "strand_architecture", location)
        _required_string(signature, "reference_strand_id", location)
        strands = object_list(signature.get("strands"), f"{location}/strands")
        if not strands:
            raise WorkRecordError(f"{location}/strands must not be empty")
        for si, strand in enumerate(strands):
            strand_location = f"{location}/strands/{si}"
            _required_string(strand, "strand_id", strand_location)
            _required_string(strand, "molecule_type", strand_location)
            if not string_list(strand.get("segment_ids"), f"{strand_location}/segment_ids"):
                raise WorkRecordError(f"{strand_location}/segment_ids must not be empty")
        string_list(signature.get("paired_region_ids"), f"{location}/paired_region_ids")
        string_list(signature.get("discontinuity_ids"), f"{location}/discontinuity_ids")

    events = object_list(record.get("event_records"), "event_records")
    event_ids: set[tuple[str, str]] = set()
    for index, event in enumerate(events):
        location = f"event_records/{index}"
        key = (
            _required_string(event, "workflow_id", location),
            _required_string(event, "transition_id", location),
        )
        if key in event_ids:
            raise WorkRecordError(f"duplicate event record {key}")
        event_ids.add(key)
        _validate_inventory_ids(event, location, inventory_ids)
        _required_string(event, "operation", location)
        for field in (
            "substrate_state_ids",
            "product_state_ids",
            "carried_forward_product_ids",
            "discarded_product_ids",
            "oligo_ids",
        ):
            values = string_list(event.get(field), f"{location}/{field}")
            if len(values) != len(set(values)):
                raise WorkRecordError(f"{location}/{field} contains duplicates")

    drafts = record.get("drafts")
    if not isinstance(drafts, Mapping) or set(drafts) != {"t2", "t3"}:
        raise WorkRecordError("drafts must contain exactly t2 and t3")
    for target, root_key in (("t2", "oligos"), ("t3", "workflows")):
        draft = drafts.get(target)
        if not isinstance(draft, Mapping):
            raise WorkRecordError(f"drafts/{target} must be an object")
        if draft.get("protocol_id") != protocol_id:
            raise WorkRecordError(f"drafts/{target} protocol_id differs from work record")
        if not isinstance(draft.get(root_key), list):
            raise WorkRecordError(f"drafts/{target}/{root_key} must be an array")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise WorkRecordError(f"invalid JSON pointer {pointer!r}")
    value = document
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            if token not in value:
                raise WorkRecordError(f"JSON pointer does not resolve: {pointer}")
            value = value[token]
        elif isinstance(value, list):
            try:
                index = int(token)
            except ValueError as error:
                raise WorkRecordError(f"JSON pointer does not resolve: {pointer}") from error
            if index < 0 or index >= len(value):
                raise WorkRecordError(f"JSON pointer does not resolve: {pointer}")
            value = value[index]
        else:
            raise WorkRecordError(f"JSON pointer does not resolve: {pointer}")
    return value


def _required_string(value: Mapping[str, Any], key: str, location: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise WorkRecordError(f"{location}/{key} must be a non-empty string")
    return result


def _validate_inventory_ids(
    value: Mapping[str, Any],
    location: str,
    known: set[str],
) -> None:
    ids = string_list(value.get("inventory_ids"), f"{location}/inventory_ids")
    if not ids:
        raise WorkRecordError(f"{location}/inventory_ids must not be empty")
    if len(ids) != len(set(ids)):
        raise WorkRecordError(f"{location}/inventory_ids contains duplicates")
    unknown = sorted(set(ids) - known)
    if unknown:
        raise WorkRecordError(
            f"{location}/inventory_ids references unknown inventory: {', '.join(unknown)}"
        )
