from __future__ import annotations

import re


CHROMATIN_ACCESSIBILITY = "chromatin accessibility"
FEATURE_BARCODE = "feature barcode"
GENE_EXPRESSION = "gene expression"
GENOMIC_DNA = "genomic DNA"
SGRNA = "sgRNA"

CANONICAL_MODALITIES = frozenset(
    {
        CHROMATIN_ACCESSIBILITY,
        FEATURE_BARCODE,
        GENE_EXPRESSION,
        GENOMIC_DNA,
        SGRNA,
    }
)

_ALIASES = {
    "atac": CHROMATIN_ACCESSIBILITY,
    "scatac": CHROMATIN_ACCESSIBILITY,
    "sc atac": CHROMATIN_ACCESSIBILITY,
    "single cell atac": CHROMATIN_ACCESSIBILITY,
    "chromatin accessibility": CHROMATIN_ACCESSIBILITY,
    "feature barcode": FEATURE_BARCODE,
    "feature barcoding": FEATURE_BARCODE,
    "rna": GENE_EXPRESSION,
    "gene expression": GENE_EXPRESSION,
    "scrna seq": GENE_EXPRESSION,
    "sc rna seq": GENE_EXPRESSION,
    "single cell rna seq": GENE_EXPRESSION,
    "single cell 3 rna seq cdna library": GENE_EXPRESSION,
    "single cell 3 rna seq deconvolution oligo do library": GENE_EXPRESSION,
    "single cell rna seq 3 digital gene expression": GENE_EXPRESSION,
    "gdna": GENOMIC_DNA,
    "genomic dna": GENOMIC_DNA,
    "sgrna": SGRNA,
    "sg rna": SGRNA,
}


def _normalized_modality(value: str) -> str:
    return re.sub(
        r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())
    ).strip()


def modality_key(value: str) -> str:
    """Return a comparison key with reviewed modality aliases collapsed."""

    normalized = _normalized_modality(value)
    return _ALIASES.get(normalized, normalized)


def canonical_modality_label(value: str) -> str:
    """Return the approved stored label for a reviewed modality alias."""

    return _ALIASES.get(_normalized_modality(value), value)
