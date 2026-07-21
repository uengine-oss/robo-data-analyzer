# Requirement Traceability

| Requirement | Design/Contract | Tasks | Completion evidence |
|---|---|---|---|
| FR-001 final 8-step tree | plan Target Structure, workflow contract | T030–T039 | move history, strategy contracts, active old path 0 |
| FR-002 complete inventory/status | data model, workflow contract | T041–T055 | inventory ground truth and status report |
| FR-003 reuse source owners | research §8 | T042,T044,T045,T051 | path/cache focused tests and prohibited-copy grep |
| FR-004 anchor+parts <=1200 | data model | T043,T046,T053 | length and reassembly hash suite |
| FR-005 exact+immutable constant | graph/workflow contracts | T060,T062,T064,T066,T069 | positive/negative link triples |
| FR-006 unresolved honesty | data model, graph contract | T010,T015,T060,T067 | unresolved reason fixtures and no fact edge |
| FR-007 mention fact/candidate isolation | graph/frontend contracts | T015,T016,T115,T125 | exact mention comparison, candidate edge 0 |
| FR-008 schema/owner single truth | graph contract | T015–T019,T022 | constraints, writer ownership and Neo4j tests |
| FR-009 file namespace | research §4 | T010,T014,T022 | collision/path/hash tests |
| FR-010 graph/stream isomorphism | graph/workflow contracts | T011,T018,T021,T022 | exact persisted/wire fixtures |
| FR-011 framework/external separation | agent contract | T080,T082,T083,T085,T088 | cross-catalog 0 and unchanged scan output |
| FR-012 repair/consensus/concurrency | agent contract | T080,T085,T087 | loop/fanout/cost/cancel tests |
| FR-013 regex crash prevention | research §9 | T040,T084,T120 | timeout/adversarial/no-live-worker evidence |
| FR-014 context injection | workflow contract | T090–T095 | prompt records and token/relevance fixtures |
| FR-015 Frontend progress/visibility | frontend contract | T100–T105,T126 | TS/build/Playwright event and UI evidence |
| FR-016 remove has_xml_mapper | plan P8 | T121,T127 | consumer map and active grep 0 |
| FR-017 prohibited copy 0 | research, plan | T024,T055,T071,T089,T122,T127 | active import/path grep 0 |
| FR-018 git mv/move map | move-map.tsv | T030–T039,T081,T082 | git history, per-slice old path 0 |
| FR-019 per-phase evidence | plan Slice Gate | T025,T039,T055,T071,T089,T095,T105,T117,T123–T129 | verification-evidence rows |
| FR-020 preserve WIP | research §2, frontend contract | T001,T024,T039,T100,T105,T128 | start/end status and diff hashes |
| FR-021 document adapters | research §12 | T110–T117 | license/API notes, Korean PoC, missing-extra tests |

Every success criterion is exercised by the same task/evidence rows; final T129 audits both directions and keeps
unproven items unchecked.
