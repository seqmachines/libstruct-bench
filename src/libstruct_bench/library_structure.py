from __future__ import annotations

import json
import re
from typing import Any

from libstruct_bench.matching import levenshtein_distance

PREDICTION_SCHEMA_VERSION = "libstruct.library_structure.v0"
_CANONICAL_SCORE_CHARS = {
    "CELL_BARCODE": "#",
    "BARCODE": "#",
    "CB": "#",
    "GEM_BARCODE": "#",
    "BEAD_BARCODE": "#",
    "UMI": "~",
    "SAMPLE_INDEX": "@",
    "INDEX": "@",
    "I5": "@",
    "I5_INDEX": "@",
    "I7": "@",
    "I7_INDEX": "@",
    "LIGATION": "&",
    "LIGATION_BARCODE": "&",
    "RT": "=",
    "RT_BARCODE": "=",
    "TN5": "%",
    "TN5_INDEX": "%",
    "TN5_BARCODE": "%",
    "FEATURE": "$",
    "FB": "$",
    "FEATURE_BARCODE": "$",
    "CAPTURE": "$",
    "CAPTURE_BARCODE": "$",
    "ANTIBODY": "$",
    "ANTIBODY_BARCODE": "$",
    "RANDOM": "?",
    "VARIABLE": "?",
    "SPACER": "?",
    "LINKER": "?",
    "PB": "?",
    "PHASE_BLOCK": "?",
    "DEGENERATE": "?",
}


class LibraryStructureValidationError(ValueError):
    """Raised when a library-structure prediction cannot be scored."""


def parse_library_entries(
    document: Any,
    *,
    expected_protocol_id: str | None = None,
    require_schema: bool = False,
) -> tuple[str | None, list[dict[str, str]]]:
    """Parse one or more final-library sequences from a v0 document."""

    parsed = _parse_document(document, expected_protocol_id=expected_protocol_id)
    if require_schema:
        schema_version = parsed.get("schema_version")
        if schema_version != PREDICTION_SCHEMA_VERSION:
            raise LibraryStructureValidationError(
                f"prediction.schema_version must be {PREDICTION_SCHEMA_VERSION!r}"
            )
    protocol_id = _document_protocol_id(parsed, expected_protocol_id=expected_protocol_id)
    entries = _library_entries(parsed)
    return protocol_id, entries


def parse_prediction_document(
    document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[str, str]:
    """Parse the minimal v0 prediction document."""

    protocol_id, entries = parse_library_entries(
        document,
        expected_protocol_id=expected_protocol_id,
        require_schema=True,
    )
    if protocol_id is None:
        raise LibraryStructureValidationError("document.protocol_id must be a non-empty string")
    return protocol_id, entries[0]["library_sequence"]


def parse_ground_truth_document(
    document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[str | None, str]:
    """Parse a curated v0 ground-truth document."""

    protocol_id, entries = parse_library_entries(document, expected_protocol_id=expected_protocol_id)
    return protocol_id, entries[0]["library_sequence"]


def normalize_library_sequence(sequence: str) -> str:
    """Normalize a final library sequence for v0 string similarity."""

    text = str(sequence)
    parts: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "[":
            end = text.find("]", i + 1)
            if end != -1:
                parts.append(_expand_scored_placeholder(text[i + 1 : end]))
                i = end + 1
                continue
        parts.append(text[i])
        i += 1
    return re.sub(r"\s+", "", "".join(parts)).upper()


def grade_library_prediction(
    prediction_document: Any,
    ground_truth_document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Grade one predicted final-library sequence against curated ground truth."""

    protocol_id, predicted_entries = parse_library_entries(
        prediction_document,
        expected_protocol_id=expected_protocol_id,
        require_schema=True,
    )
    ground_truth_protocol_id, ground_truth_entries = parse_library_entries(
        ground_truth_document,
        expected_protocol_id=expected_protocol_id,
    )
    if expected_protocol_id is not None and ground_truth_protocol_id not in (None, expected_protocol_id):
        raise ValueError(
            f"ground truth protocol_id={ground_truth_protocol_id!r} does not match expected {expected_protocol_id!r}"
        )

    matches = _match_library_entries(predicted_entries, ground_truth_entries)
    similarities = [match["sequence_similarity"] for match in matches]
    similarity = sum(similarities) / len(similarities) if similarities else 0.0
    matched_similarities = [
        match["sequence_similarity"]
        for match in matches
        if match["predicted_index"] is not None
    ]
    matched_similarity = (
        sum(matched_similarities) / len(matched_similarities)
        if matched_similarities
        else 0.0
    )
    matched_library_count = len(matched_similarities)
    ground_truth_library_count = len(ground_truth_entries)
    predicted_library_count = len(predicted_entries)
    library_recall = (
        matched_library_count / ground_truth_library_count
        if ground_truth_library_count
        else 0.0
    )
    library_precision = (
        matched_library_count / predicted_library_count
        if predicted_library_count
        else 0.0
    )
    library_f1 = (
        2 * library_precision * library_recall / (library_precision + library_recall)
        if library_precision + library_recall
        else 0.0
    )
    total_distance = sum(match["edit_distance"] for match in matches)
    predicted_length = sum(len(match["predicted_sequence"]) for match in matches)
    ground_truth_length = sum(len(match["ground_truth_sequence"]) for match in matches)

    metrics = {
        "reward": similarity,
        "sequence_similarity": similarity,
        "matched_sequence_similarity": matched_similarity,
        "library_recall": library_recall,
        "library_precision": library_precision,
        "library_f1": library_f1,
        "edit_distance": float(total_distance),
        "prediction_parse_valid": 1.0,
        "predicted_length": float(predicted_length),
        "ground_truth_length": float(ground_truth_length),
        "library_count": float(ground_truth_library_count),
        "ground_truth_library_count": float(ground_truth_library_count),
        "predicted_library_count": float(predicted_library_count),
        "matched_library_count": float(matched_library_count),
    }
    audit = {
        "protocol_id": protocol_id,
        "ground_truth_protocol_id": ground_truth_protocol_id,
        "sequence_similarity": similarity,
        "matched_sequence_similarity": matched_similarity,
        "library_recall": library_recall,
        "library_precision": library_precision,
        "library_f1": library_f1,
        "library_matches": matches,
    }
    return metrics, audit


def zero_metrics() -> dict[str, float]:
    return {
        "reward": 0.0,
        "sequence_similarity": 0.0,
        "matched_sequence_similarity": 0.0,
        "library_recall": 0.0,
        "library_precision": 0.0,
        "library_f1": 0.0,
        "edit_distance": 0.0,
        "prediction_parse_valid": 0.0,
        "predicted_length": 0.0,
        "ground_truth_length": 0.0,
        "library_count": 0.0,
        "ground_truth_library_count": 0.0,
        "predicted_library_count": 0.0,
        "matched_library_count": 0.0,
    }


def extract_json_document(text: str) -> dict[str, Any]:
    """Extract a JSON object from a raw model response."""

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise LibraryStructureValidationError(
                f"model response contained malformed JSON: {exc.msg}"
            ) from exc
    raise LibraryStructureValidationError("model response did not contain a JSON object")


def _parse_document(document: Any, *, expected_protocol_id: str | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise LibraryStructureValidationError("document must be a JSON object")
    protocol_id = document.get("protocol_id")
    if protocol_id is not None and not isinstance(protocol_id, str):
        raise LibraryStructureValidationError("document.protocol_id must be a string when present")
    if expected_protocol_id is not None and protocol_id not in (None, expected_protocol_id):
        raise LibraryStructureValidationError(
            f"document.protocol_id={protocol_id!r} does not match expected {expected_protocol_id!r}"
        )
    return document


def _document_protocol_id(
    document: dict[str, Any],
    *,
    expected_protocol_id: str | None,
) -> str | None:
    protocol_id = document.get("protocol_id") or expected_protocol_id
    if protocol_id is None:
        return None
    if not isinstance(protocol_id, str) or not protocol_id.strip():
        raise LibraryStructureValidationError("document.protocol_id must be a non-empty string when present")
    return protocol_id.strip()


def _protocol_and_sequence(
    document: dict[str, Any],
    *,
    expected_protocol_id: str | None,
) -> tuple[str, str]:
    protocol_id = _document_protocol_id(document, expected_protocol_id=expected_protocol_id)
    if protocol_id is None:
        raise LibraryStructureValidationError("document.protocol_id must be a non-empty string")
    sequence = document.get("library_sequence")
    if not isinstance(sequence, str) or not sequence.strip():
        raise LibraryStructureValidationError("document.library_sequence must be a non-empty string")
    return protocol_id, sequence


def _library_entries(document: dict[str, Any]) -> list[dict[str, str]]:
    raw_entries = document.get("libraries")
    if raw_entries is None:
        sequence = document.get("library_sequence")
        if not isinstance(sequence, str) or not sequence.strip():
            raise LibraryStructureValidationError("document.library_sequence must be a non-empty string")
        return [
            {
                "library_id": str(document.get("library_id") or document.get("modality") or "library_1"),
                "modality": str(document.get("modality") or "library"),
                "library_sequence": sequence,
            }
        ]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise LibraryStructureValidationError("document.libraries must be a non-empty array when present")
    entries: list[dict[str, str]] = []
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise LibraryStructureValidationError(f"document.libraries[{index - 1}] must be an object")
        sequence = raw.get("library_sequence")
        if not isinstance(sequence, str) or not sequence.strip():
            raise LibraryStructureValidationError(
                f"document.libraries[{index - 1}].library_sequence must be a non-empty string"
            )
        library_id = raw.get("library_id") or raw.get("modality") or f"library_{index}"
        modality = raw.get("modality") or library_id
        entries.append(
            {
                "library_id": str(library_id).strip() or f"library_{index}",
                "modality": str(modality).strip() or str(library_id).strip() or f"library_{index}",
                "library_sequence": sequence,
            }
        )
    return entries


def _match_library_entries(
    predicted_entries: list[dict[str, str]],
    ground_truth_entries: list[dict[str, str]],
) -> list[dict[str, Any]]:
    unused = set(range(len(predicted_entries)))
    matches: list[dict[str, Any]] = []
    for gt_index, ground_truth_entry in enumerate(ground_truth_entries):
        candidate_indexes = [
            index
            for index in unused
            if _entry_key(predicted_entries[index]) == _entry_key(ground_truth_entry)
        ]
        if not candidate_indexes:
            candidate_indexes = [
                index
                for index in unused
                if predicted_entries[index].get("modality") == ground_truth_entry.get("modality")
            ]
        if not candidate_indexes:
            candidate_indexes = list(unused)

        best: tuple[float, int, int, str] | None = None
        ground_truth = normalize_library_sequence(ground_truth_entry["library_sequence"])
        for predicted_index in candidate_indexes:
            predicted = normalize_library_sequence(predicted_entries[predicted_index]["library_sequence"])
            distance = levenshtein_distance(predicted, ground_truth)
            denominator = max(len(predicted), len(ground_truth))
            candidate_similarity = 1.0 if denominator == 0 else max(0.0, 1.0 - distance / denominator)
            candidate = (candidate_similarity, -distance, predicted_index, predicted)
            if best is None or candidate > best:
                best = candidate

        if best is None:
            predicted_index = None
            predicted = ""
            distance = len(ground_truth)
            denominator = len(ground_truth)
            candidate_similarity = 1.0 if denominator == 0 else 0.0
        else:
            candidate_similarity, neg_distance, predicted_index, predicted = best
            distance = -neg_distance
            unused.discard(predicted_index)

        matches.append(
            {
                "library_id": ground_truth_entry["library_id"],
                "modality": ground_truth_entry["modality"],
                "ground_truth_index": gt_index,
                "predicted_index": predicted_index,
                "predicted_library_id": predicted_entries[predicted_index]["library_id"]
                if predicted_index is not None
                else None,
                "predicted_sequence": predicted,
                "ground_truth_sequence": ground_truth,
                "edit_distance": distance,
                "sequence_similarity": candidate_similarity,
            }
        )
    return matches


def _entry_key(entry: dict[str, str]) -> str:
    return entry.get("library_id") or entry.get("modality") or ""


def _expand_scored_placeholder(inner: str) -> str:
    raw = inner.strip()
    low = raw.lower().replace("–", "-").replace("—", "-")
    low = re.sub(r"\s+", " ", low).strip()

    canonical = re.fullmatch(r"([a-z0-9_]+)\s*:\s*(\d+)", low, flags=re.IGNORECASE)
    if canonical:
        role, length_text = canonical.groups()
        score_char = _CANONICAL_SCORE_CHARS.get(role.upper())
        if score_char:
            return score_char * int(length_text)

    score_char = _score_char_for_placeholder_text(low)
    length = _length_for_placeholder_text(low)
    if score_char and length is not None:
        return score_char * length

    return f"[{raw}]"


def _score_char_for_placeholder_text(text: str) -> str | None:
    if re.search(r"\bumi\d*\b", text):
        return "~"
    if any(term in text for term in ("feature", "capture", "antibody")) or re.search(r"\bfb\b", text):
        return "$"
    if "tn5" in text or re.search(r"\bn[57]\s+barcode\b", text):
        return "%"
    if "ligation" in text:
        return "&"
    if "rt barcode" in text:
        return "="
    if any(term in text for term in ("sample index", " i5", " i7", "i5 ", "i7 ", "rpi")):
        return "@"
    if re.search(r"\b(?:i5|i7)\b", text) or re.fullmatch(r"(?:i5|i7)", text):
        return "@"
    if re.search(r"\bindex\b", text):
        return "@"
    if any(
        term in text
        for term in (
            "phase",
            " pb",
            "spacer",
            "linker",
            "random",
            "degenerate",
            "variable",
            "overhang",
            "none/",
        )
    ):
        return "?"
    if any(
        term in text
        for term in (
            "cell barcode",
            "10x barcode",
            "gem barcode",
            "bead barcode",
            "barcode",
            "bc#",
            "cb",
            "cls",
            "vb",
            "subarray",
            "well",
            "plate",
            "round",
            "hy",
        )
    ):
        return "#"
    return None


def _length_for_placeholder_text(text: str) -> int | None:
    range_match = re.search(r"\b\d+\s*-\s*(\d+)\s*-?\s*bp\b", text)
    if range_match:
        return int(range_match.group(1))

    bp_match = re.search(r"\b(\d+)\s*-?\s*bp\b", text)
    if bp_match:
        return int(bp_match.group(1))

    mer_match = re.search(r"\b(\d+)\s*-\s*mer\b", text)
    if mer_match:
        return int(mer_match.group(1))

    nt_match = re.search(r"\b(\d+)\s*nt\b", text)
    if nt_match:
        return int(nt_match.group(1))

    alternatives = [part for part in re.split(r"/", text) if part and part != "none"]
    if len(alternatives) > 1 and all(re.fullmatch(r"[acgtn]+", part) for part in alternatives):
        return max(len(part) for part in alternatives)

    n_run = re.search(r"n{2,}", text)
    if n_run:
        return len(n_run.group(0))

    return None
