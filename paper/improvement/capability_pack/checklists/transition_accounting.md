# Transition-accounting checklist

For each transition:

- Substrate IDs resolve to states available at that chronological point.
- Product IDs resolve to states created by the operation.
- Every product appears exactly once in carried-forward or discarded products.
- Discarded states are never used as later substrates.
- Carried states are later consumed or listed as terminal outputs.
- The normalized operation matches the physical event; procedural handling is
  kept in operation detail rather than invented as a molecular edge.
- `oligo_ids` name physically used or incorporated T2 families.
- A branch exists only when multiple molecular products continue downstream.
- No PCR cycle or repeated handling step is represented as a graph cycle.
