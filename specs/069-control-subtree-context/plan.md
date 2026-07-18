# Plan

1. Add a red contract for source-relative nested control subtrees and step5
   context wiring.
2. Expose the ontology-derived control-label set from the language policy.
3. Build `SubtreeNode` values in a step5 helper from the already persisted
   `CodeNode.parent_id/start/end` contract.
4. Attach only routine-owned trees to `AnalysisContext.extra`.
5. Prove the fallback leaves the convergence path and runs fragment analysis,
   then execute the authoritative regression gates.
