# Final graph-audit checklist

- Each workflow is one weakly connected molecular process.
- Initial states have no required upstream producer.
- Final outputs are reachable through carried-product edges.
- Shared upstream chemistry appears once before modality-specific branches.
- State IDs, strand IDs, segment IDs, transition IDs, and oligo IDs are unique
  in their required scopes and all references resolve.
- Strand architecture agrees with strand count and paired-region declarations.
- Paired and unpaired boundaries, overhangs, nicks, gaps, and breaks remain
  explicit.
- Terminal reference strands preserve their physical 5′→3′ direction.
- No unspecified repair, fill-in, ligation, amplification, index addition, or
  sequencing-ready completion was added for convenience.
- Run the benchmark validator and every checker in `tools/`.
