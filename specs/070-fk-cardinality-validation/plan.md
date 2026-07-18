# Plan

1. Add red parser contracts for unique-key cardinality, including composite
   and strict-subset opposites.
2. Derive each table's declared unique-key sets during DDL parsing and assign
   explicit FK properties from the complete local FK column set.
3. Upgrade the independent graph diff to compare column-level FK identity and
   `fk_type`/`is_candidate` properties.
4. Add validator unit coverage for property mismatch and critical-exit
   behavior.
5. Run focused, authoritative, and live source-to-graph gates; record evidence
   in the audit ledger.
