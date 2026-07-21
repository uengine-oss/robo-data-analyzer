# Research: External File Linking Merge

## 1. Evidence and authority

읽은 정본:

- `D:/work/robo/_playground/xlink/HANDOFF.md` 전체, 특히 §12.
- `MERGE-PLAN.md`, `MERGE-NOTES.md`, `DEEP-RESEARCH-RESULT.md`, `PRODUCTION-READINESS.md`.
- playground 구현과 16개 suite/25 gate/적대적 검증 자산.
- Analyzer 현재 `main@50275f32cda2`의 실제 source, graph, pipeline, LLM, tests.
- Analyzer constitution 1.2.0과 SpecKit templates.

우선순위는 `현재 코드/실행 → 현행 constitution → HANDOFF §12 → 보조 문서/과거 spec`이다.
spec 058/062가 참조하는 `D:/work/robo/세서비스-최종트리.md`는 현재 디스크에서 발견되지 않았다.
따라서 존재하지 않는 파일을 정본처럼 가정하지 않고, 이미 그 구조를 구현한 현재 소스와 HANDOFF의
8-step 증분 목표를 결합한다.

## 2. Baseline and WIP preservation

| Repository | Baseline | WIP policy |
|---|---|---|
| robo-data-frontend | `main@1f8baa4f9291` | package/graphCanvas/spec057 등 기존 WIP 보존 |
| antlr-code-parser | `main@f0d21ba37626` | listener/CLAUDE/spec009 WIP. 이 기능에서 수정 금지 |
| robo-architect | `feature/legacy-evidence-tags@651ce0fec6c9` | Figma/서브모듈/spec WIP. 이 기능에서 수정 금지 |

외부파일 기능은 번호 충돌 때문에 `075-external-file-linking`을 쓴다. SpecKit의 전역
`.specify/feature.json`은 074 WIP를 가리키므로 변경하지 않는다.

## 3. Final tree decision

현재 7-step 트리를 다음 8-step 트리로 이동한다.

```text
pipeline/steps/
  step1_parse_ddl/          retained
  step2_load_ast/           retained
  step3_load_external/      new
  step4_scan_project/       git mv from step3
  step5_linking/            git mv from step4; add link_external/
  step6_build_context/      git mv from step5; inject linked chunks
  step7_analyze_code/       git mv from step6
  step8_post_processing/    git mv from step7
```

Tests mirror the same numbering. Active imports, README, scripts, runtime event component strings,
artifact classification and structure tests move in the same slices. Historical specs are not rewritten.

## 4. ID namespace

### Decision: add `FILE_NS = "file:"`

`graph/schema/node_ids.py.external_id()` is already consumed by `graph/edges/calls.py` for external
module/package/function stubs and returns `code:external:*`. Reusing it for physical external files would
mix code-world stubs and file-world evidence. A separate namespace is required:

```text
file:<url-encoded-project>/<normalized-relative-path>
file:<url-encoded-project>/<relative-path>#chunk:<url-encoded-key>
file:<url-encoded-project>/<relative-path>#chunk:<key>#part:<n>
unresolved:<sha256-prefix>
candidate:<sha256-prefix>
```

Paths are POSIX-normalized, relative only, and resolved through the existing path boundary. IDs are opaque;
consumers must not parse them to recover ownership.

## 5. Graph model

### Decision: Analyzer-native typed dataclasses

Playground's generic `Edge(rel, props: dict)` is not copied. Analyzer's graph schema uses one dataclass per
relationship and the stream serializer rejects unknown types. Add typed classes for `CallsSql`, `UsesConfig`,
`ReferTo`, `Mentions`, `HasChunk`, `HasPart`, `HasSection` with explicit evidence fields.

`ReadsWrites` is reused for SQL→TABLE and derived code→TABLE because its writer matches the source by owner+ID,
not by label. Its documentation/tests are broadened from CodeNode-only to Analyzer node→TABLE. No duplicate
READS/WRITES writer is created.

Node writers use `graph/node_upsert.py.upsert_fixed_label` or `upsert_by_node_label`; playground `graph_io.py`
is not copied. New stable labels get unique constraints and ownership tests.

## 6. Chunk and document model

Direct key linking uses `ExternalChunk` for SQL/config/artifact/custom anchors and parts. Documents retain
`DocumentNode` with `DOCUMENT`/`DOC_SECTION`/`DOC_CHUNK` labels because their hierarchy and exact `MENTIONS`
semantics differ. `DOC_CHUNK` participates in context injection but not in key-in-code linking.

Artifact class FQNs resolve to Analyzer's existing `Module` entity. Java classes and C/Python files are modeled
as `Module`, while `CodeNode` is a routine/control statement; synthesizing a class `CodeNode` would duplicate
and contradict step2 AST ownership. Direct artifact keys still link from the consuming routine.

`MAX_CHUNK_CHARS=1200` remains the deterministic hard limit. The 512-token research result is compatible:
1,200 Korean/legacy text characters are normally below 512 tokens; a tokenizer-independent character limit
is the fail-closed storage contract.

## 7. Workflow state

New state keys have explicit producers:

- `project_files`, `external_chunks`: step3 initializes them and step4 may sequentially replace them with the full
  collection after an unknown text format becomes validated custom evidence; default replace is intentional.
- `document_nodes`: step3 only, default replace.
- `external_edges`: step3/step4 structural edges and step5 semantic links; `operator.add` reducer.
- `unresolved_refs`: step3 parse/inventory, step4 rule rejection and step5 link failures; `operator.add` reducer.
- `candidate_links`: step8 embedding only, default replace.

Step2 and step3 write disjoint keys and may form a LangGraph parallel tuple only after the state non-collision
contract passes. Step4 runs after that tuple, so its full replacements cannot collide with step3. Step5 consumes the
settled collections; step6 consumes linked chunks and existing code context.

## 8. Parser and source policy

- Text reads reuse run-scoped `source.file_cache.SourceFileCache`; no new cache.
- Path validation reuses `source.path_resolver`; no source-root escape.
- XML uses lxml with entities/network disabled and comment/PI nodes excluded from SQL text.
- JSON/TOML/CSV/INI/YAML use stdlib/PyYAML. Java properties require direct `javaproperties` dependency.
- Encoding policy: BOM → strict UTF-8 → charset-normalizer confidence → CP949/EUC-KR → explicit failure.
  Latin-1 may be a lossless byte fallback only for formats that accept opaque bytes; it cannot be reported as
  decoded Korean success.
- Binary/unsupported/optional dependency absence is an explicit ProjectFile status, not a silent empty parse.

## 9. Regex crash prevention

Analyzer's environment currently has transitive `regex` but requirements do not declare it. RE2 is absent.
Use the `regex` package as an explicit direct dependency and call it with a wall-clock timeout in both skill
validation and application. Keep static nested-quantifier rejection as defense in depth. This removes the
playground daemon-thread leak without requiring an unavailable RE2 wheel. Timeout/compile failures reject the
skill and preserve the file as unresolved.

## 10. Agent and skill layout

```text
agents/skills/
  templates/               retained common templates
  framework/               six existing framework skills, moved with git mv
  external/                learned external format skills
```

`FRAMEWORK_SKILLS_DIR` and `EXTERNAL_SKILLS_DIR` are explicit constants. The catalog builder remains generic and
accepts a directory argument. Existing ScanResult, scan prompt and scan agent logic are not widened to hold
external formats. External Agent uses the shared structured-agent runner, LLM client, fanout warmup, semaphore,
budget and path boundary, but owns a separate prompt and response schema.

The contradictory `filetype` paragraph in HANDOFF §12.13 is rejected in favor of the earlier and user-confirmed
`framework/external` decision.

## 11. Concurrency and cost

- Deterministic file parsing may use bounded `asyncio` concurrency, preserving inventory order in output IDs.
- External consensus n=7 samples run through the run-level `llm_semaphore` and `gather_with_warmup`.
- Self-repair is proposal + at most two repair attempts (`R<=2`).
- Cost budget is checked before fanout and every provider usage is recorded by the existing LLM boundary.
- Cancellation propagates; spawned tasks are cancelled and awaited before step/run termination.

## 12. Document stack

P7 uses adapters so optional heavy dependencies do not become unconditional Analyzer startup imports:

- PDF text/layout: PyMuPDF first; scanned pages route to PaddleOCR-VL adapter when installed.
- XLSX: openpyxl read-only/data-only.
- DOCX: python-docx.
- HWP/HWPX: hwpkit-compatible adapter after license/runtime probe.
- Image OCR: PaddleOCR-VL-1.5 local adapter.

Each adapter reports `available`, `unsupported`, or `failed` with reason. Korean fixture PoC and package license
evidence are required before declaring a format supported.

## 13. Frontend integration

Only `analyzer.ts`, `graphVisibility.ts` and tests are planned. Existing dirty `graphCanvas.ts` and `package.json`
are not overwritten. New nodes/edges are live-streamed, named from explicit props, and hidden by the same single
visibility policy as AST support nodes. `nodeRelationships.ts` consumes that policy automatically.

## 14. Rejected alternatives

- `step2b_load_external`: violates approved final numbering.
- Reuse `code:external:*` IDs: mixes physical files with external call stubs.
- Copy playground graph/LLM/env layers: DRY and ownership violation.
- One combined framework+external agent output: ScanResult cannot represent per-format N skills.
- Generic untyped edge props: weakens fail-closed graph/stream contract.
- Thread timeout for regex: leaks unkillable workers.
- Embedding similarity as fact: proven false-positive risk; candidate only.

## 15. P7 package verification (2026-07-21)

All packages below remain optional and are imported only inside a bounded child process.  Base Analyzer startup
does not import or require them.

| Capability | Selected package/version | License | API/runtime decision | Official evidence |
|---|---|---|---|---|
| PDF text | PyMuPDF 1.28.0 | AGPL-3.0 or Artifex commercial | `import pymupdf`; encrypted input fails closed; keep out of base requirements because deployment must make an explicit license choice | https://pypi.org/project/pymupdf/ |
| XLSX | openpyxl 3.1.5 + defusedxml 0.7.1 | MIT + PSFL | `load_workbook(read_only=True, data_only=True, keep_links=False)` and explicit close; defusedxml is included because openpyxl documents XML expansion risk without it | https://pypi.org/project/openpyxl/ and https://openpyxl.readthedocs.io/en/stable/ |
| DOCX | python-docx 1.2.0 | MIT | `Document(path)` plus `iter_inner_content()` preserves paragraph/table order | https://pypi.org/project/python-docx/ |
| HWP/HWPX | hwpxkit 0.2.1 | MIT | one `parse_file` adapter owns both HWP 5.0 and HWPX; no competing fallback chain | https://pypi.org/project/hwpxkit/ |
| Image/scanned PDF | PaddleOCR 3.7.0 doc-parser with PaddlePaddle 3.2.1 | Apache-2.0 | local full `PaddleOCRVL(pipeline_version="v1.5", use_queues=False)` pipeline; v1.5 is the validated Korean PoC contract even though v1.6 is the current default | https://pypi.org/project/paddleocr/ and https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html |

`requirements-documents.txt` and `requirements-ocr-cpu.txt` are deliberately separate.  OCR accelerator installs
are platform-specific, and PyMuPDF's dual license must never arrive transitively through the base service install.
