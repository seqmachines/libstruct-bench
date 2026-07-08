from __future__ import annotations

import re

CANONICAL_PLACEHOLDER_RE = re.compile(r"^\[([A-Z0-9_]+):(\d+)\]$")
_BASES = set("ACGTUNV")
_ROLE_ALIASES = {
    "BARCODE": "CELL_BARCODE",
    "CB": "CELL_BARCODE",
    "GEM_BARCODE": "CELL_BARCODE",
    "BEAD_BARCODE": "CELL_BARCODE",
    "INDEX": "SAMPLE_INDEX",
    "I5": "I5_INDEX",
    "I7": "I7_INDEX",
    "TN5": "TN5_INDEX",
    "TN5_BARCODE": "TN5_INDEX",
    "FEATURE": "FEATURE_BARCODE",
    "FB": "FEATURE_BARCODE",
    "CAPTURE": "FEATURE_BARCODE",
    "CAPTURE_BARCODE": "FEATURE_BARCODE",
    "ANTIBODY": "FEATURE_BARCODE",
    "ANTIBODY_BARCODE": "FEATURE_BARCODE",
    "PB": "PHASE_BLOCK",
    "SPACER": "VARIABLE",
    "LINKER": "VARIABLE",
    "DEGENERATE": "VARIABLE",
    "OVERHANG": "VARIABLE",
}


def normalize_sequence(sequence: str) -> str:
    """Normalize an oligo sequence for benchmark comparison."""

    if sequence is None:
        return ""

    text = _strip_terminal_wrappers(str(sequence))
    parts: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "[":
            end = text.find("]", i + 1)
            if end != -1:
                parts.append(_normalize_bracket_placeholder(text[i + 1 : end]))
                i = end + 1
                continue
        if char == "/":
            end = text.find("/", i + 1)
            if end != -1:
                parts.append(_normalize_modification_tag(text[i : end + 1]))
                i = end + 1
                continue

        next_special = _next_special(text, i)
        parts.append(_normalize_plain_segment(text[i:next_special]))
        i = next_special

    return "".join(part for part in parts if part)


def sequence_tokens(sequence: str, *, already_normalized: bool = False) -> list[str]:
    """Tokenize a sequence, expanding canonical placeholders by declared length."""

    normalized = sequence if already_normalized else normalize_sequence(sequence)
    tokens: list[str] = []
    i = 0
    while i < len(normalized):
        char = normalized[i]
        if char == "[":
            end = normalized.find("]", i + 1)
            if end != -1:
                placeholder = normalized[i : end + 1]
                match = CANONICAL_PLACEHOLDER_RE.match(placeholder)
                if match:
                    role, length_text = match.groups()
                    tokens.extend([f"<{role}>"] * int(length_text))
                else:
                    tokens.append(placeholder)
                i = end + 1
                continue
        if char == "/":
            end = normalized.find("/", i + 1)
            if end != -1:
                tokens.append(normalized[i : end + 1])
                i = end + 1
                continue
        if normalized[i : i + 4].lower() == "(du)":
            tokens.append("(dU)")
            i += 4
            continue
        if char == "r" and i + 1 < len(normalized) and normalized[i + 1].upper() in _BASES:
            tokens.append("r" + normalized[i + 1].upper())
            i += 2
            continue
        if char == "+" and i + 1 < len(normalized) and normalized[i + 1].upper() in _BASES:
            tokens.append("+" + normalized[i + 1].upper())
            i += 2
            continue
        tokens.append(char)
        i += 1
    return tokens


def _strip_terminal_wrappers(sequence: str) -> str:
    text = sequence.strip()
    text = re.sub(r"^\s*5\s*['’′]?\s*-\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*3\s*['’′]?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*bio\s*-\s*", "/5Bio/", text, flags=re.IGNORECASE)
    text = re.sub(r"^(/[^/]+/)\s*-\s*", r"\1", text)
    return text.strip()


def _next_special(text: str, start: int) -> int:
    next_bracket = text.find("[", start + 1)
    next_slash = text.find("/", start + 1)
    candidates = [idx for idx in (next_bracket, next_slash) if idx != -1]
    return min(candidates) if candidates else len(text)


def _normalize_modification_tag(tag: str) -> str:
    inner = tag[1:-1].strip()
    if not inner:
        return tag

    known = {
        "5phos": "5Phos",
        "phos": "5Phos",
        "5bio": "5Bio",
        "bio": "5Bio",
        "5biosg": "5Biosg",
        "5acryd": "5Acryd",
        "acrydite": "5Acryd",
        "6-fam": "6-FAM",
        "56-fam": "56-FAM",
        "nh2": "NH2",
        "5rapp": "5rApp",
        "3spc3": "3SpC3",
        "3ddc": "3ddC",
        "3invdt": "3InvdT",
        "ddc": "ddC",
        "ddu": "ddU",
        "ideoxyu": "ideoxyU",
        "ibiodt": "iBiodT",
        "isppc": "iSpPC",
        "ithiomc6-d": "iThioMC6-D",
    }
    compact = re.sub(r"\s+", "", inner).lower()
    return f"/{known.get(compact, inner)}/"


def _normalize_bracket_placeholder(inner: str) -> str:
    raw = inner.strip()
    low = raw.lower().replace("–", "-").replace("—", "-")
    low = re.sub(r"\s+", " ", low).strip()

    modification = _bracket_modification_tag(low)
    if modification:
        return modification

    canonical = re.fullmatch(r"([a-z0-9_]+)\s*:\s*(\d+)", low, flags=re.IGNORECASE)
    if canonical:
        role, length = canonical.groups()
        return f"[{_canonical_role(role)}:{int(length)}]"

    role_length = _placeholder_role_length(low)
    if role_length:
        role, length = role_length
        return f"[{role}:{length}]"

    return f"[{raw}]"


def _bracket_modification_tag(text: str) -> str | None:
    known = {
        "ddc": "/ddC/",
        "3ddc": "/3ddC/",
        "ddu": "/ddU/",
        "ideoxyu": "/ideoxyU/",
    }
    compact = re.sub(r"[\s_-]+", "", text).lower()
    return known.get(compact)


def _placeholder_role_length(text: str) -> tuple[str, int] | None:
    variable_length = _variable_alternative_length(text)
    if variable_length is not None:
        return "VARIABLE", variable_length

    patterns: list[tuple[str, str]] = [
        (r"0\s*-\s*(\d+)\s*-?\s*bp\s+(?:pb|phase\s+block)", "PHASE_BLOCK"),
        (r"\d+\s*-?\s*bp\s+or\s+(\d+)\s*-?\s*bp\s+barcode(?:\s+[a-z])?", "CELL_BARCODE"),
        (r"(\d+)\s*-?\s*bp\s+rt\s+barcode", "RT_BARCODE"),
        (
            r"(\d+)\s*-?\s*bp\s+(?:cell\s+barcode|10x\s+barcode|gem\s+barcode|bead\s+barcode)",
            "CELL_BARCODE",
        ),
        (
            r"(\d+)\s*-?\s*bp\s+(?:barcode\d+|bc#?\d+|cb\d+|round\d+\s+barcode|hy\s+barcode|plate\s+barcode|well\s+barcode|subarray\s+barcode)",
            "CELL_BARCODE",
        ),
        (r"(\d+)\s*-?\s*bp\s+umi\d*", "UMI"),
        (r"(\d+)\s*-?\s*bp\s+(?:sample\s+index|index|rpi)", "SAMPLE_INDEX"),
        (r"(\d+)\s*-?\s*bp\s+i5(?:\s+(?:sample\s+)?index)?", "I5_INDEX"),
        (r"(\d+)\s*-?\s*bp\s+i7(?:\s+(?:sample\s+)?index)?", "I7_INDEX"),
        (r"(\d+)\s*-?\s*bp\s+n[57]\s+barcode", "TN5_INDEX"),
        (r"(\d+)\s*-?\s*bp\s+tn5\s+(?:index|barcode)(?:\s+[ab])?", "TN5_INDEX"),
        (r"(\d+)\s*-?\s*bp\s+(?:fb|feature\s+barcode|antibody\s+barcodes?)", "FEATURE_BARCODE"),
        (r"(\d+)\s*-?\s*bp\s+(?:pb|phase\s+block)", "PHASE_BLOCK"),
    ]
    for pattern, role in patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return role, int(match.group(1))

    random_patterns = [
        r"random\s+(\d+)\s*-\s*mer",
        r"(\d+)\s*-?\s*bp\s+random(?:er)?",
        r"(\d+)\s*nt\s+random(?:er)?",
    ]
    for pattern in random_patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return "RANDOM", int(match.group(1))

    if "random" in text:
        number = re.search(r"(\d+)", text)
        if number:
            return "RANDOM", int(number.group(1))
        n_run = re.search(r"n{2,}", text, flags=re.IGNORECASE)
        if n_run:
            return "RANDOM", len(n_run.group(0))

    return None


def _canonical_role(role: str) -> str:
    role_upper = role.upper()
    return _ROLE_ALIASES.get(role_upper, role_upper)


def _variable_alternative_length(text: str) -> int | None:
    alternatives = [part for part in text.split("/") if part and part != "none"]
    if len(alternatives) <= 1:
        return None
    if all(re.fullmatch(r"[acgtn]+", part, flags=re.IGNORECASE) for part in alternatives):
        return max(len(part) for part in alternatives)
    return None


def _normalize_plain_segment(segment: str) -> str:
    text = re.sub(r"\s+", "", segment)
    if not text:
        return ""
    text = _expand_homopolymer_shorthand(text)

    normalized: list[str] = []
    i = 0
    while i < len(text):
        if text[i : i + 4].lower() == "(du)":
            normalized.append("(dU)")
            i += 4
            continue
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if char in {"r", "R"} and next_char.upper() in _BASES:
            normalized.append("r" + next_char.upper())
            i += 2
            continue
        if char == "+" and next_char.upper() in _BASES:
            normalized.append("+" + next_char.upper())
            i += 2
            continue
        if char == "*":
            i += 1
            continue
        if char.isalpha():
            normalized.append(char.upper())
        else:
            normalized.append(char)
        i += 1

    return "".join(normalized)


def _expand_homopolymer_shorthand(text: str) -> str:
    def parenthesized(match: re.Match[str]) -> str:
        return match.group(1).upper() * int(match.group(2))

    text = re.sub(r"\(([ATat])\)(\d+)", parenthesized, text)
    return re.sub(r"([ATat])(\d{2,})", parenthesized, text)
