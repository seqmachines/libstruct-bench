# Evidence-ledger checklist

Maintain a temporary JSON ledger while solving the target. It is a reasoning
control, not part of the submitted prediction.

```json
{
  "schema_version": "libstruct.evidence_ledger.v1",
  "claims": [
    {
      "claim_id": "claim-001",
      "target": "t3",
      "json_pointer": "/workflows/0/transitions/0/operation",
      "support": "explicit",
      "source_locators": ["source.pdf page 4, Figure 1"]
    }
  ]
}
```

Before finalization, verify:

- Each exact sequence, placeholder length, orientation, modification,
  transition operation, product classification, strand architecture, pairing
  claim, discontinuity, and terminal structure has an exact ledger entry.
- `explicit` means directly stated or drawn by a target source.
- `derivable` records a reproducible transformation from located source facts.
- `ambiguous` and `unsupported` claims are not silently completed.
- Source locators identify the supplied file and a page, section, table, figure,
  sheet, or row where possible.
- Protocol-general knowledge explains reasoning but never upgrades support.
