# Specification Quality Checklist

- [x] No unresolved template placeholders or NEEDS CLARIFICATION markers.
- [x] Every user story is independently testable and has negative/failure scenarios.
- [x] Every FR maps to at least one task and verification source.
- [x] Final target tree is exactly the approved 8-step tree.
- [x] `framework/external` is the only agent/skill naming contract.
- [x] Prohibited playground infrastructure is explicitly excluded.
- [x] Graph persistence and NDJSON stream are one contract.
- [x] Cancellation, retry, failure, timeout, idempotency and WIP preservation are covered.
- [x] Multi-repository Frontend contract and separate SDD gate are included.
- [x] Destructive `git mv` confirmation gate is included without adding Phase-by-Phase approvals.
