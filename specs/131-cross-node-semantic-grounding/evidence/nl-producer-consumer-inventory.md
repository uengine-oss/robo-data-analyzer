# Analyzer 자연어 의미 생산·소비 인벤토리

**Task**: T131-19
**Snapshot**: Analyzer `507ea7f9358e95f501546c624313d14521516183`, Architect
`30e7dd645d27a7d9eb1e41c5e84ea1dd508cff10`, 2026-08-12
**범위**: Framework와 DBMS의 분석 시점 자연어 의미, 그 의미에 영향을 주는 임시 자연어,
그래프·검색·API·MCP·Architect 소비 경로

이 문서는 구현 전 영향표다. 현재 코드를 설명하는 항목과 목표 계약을 섞지 않는다.
`semantic-frame/v1`의 필드 정의는 T131-20에서 별도로 고정한다.

## 1. 조사 원칙과 경계

- 실제 `run_yaml_rule`·`run_structured_agent` 호출, response model, normalizer, validator,
  graph writer와 소비 코드를 역추적했다.
- Framework와 DBMS는 prompt와 호출 단위가 다르지만 step7 이후 동일한 `CodeNode`,
  `Rule`, `Question`, `Table`, `Column`, `Module` 저장 계약으로 합류한다.
- LLM이 만든 모든 문자열을 의미 정본으로 보지 않는다. 구조 발견·링킹·운영 진단을 위한
  문자열은 아래 제외표에 남기되 `semantic-frame/v1` 대상에서는 제외한다.
- DDL·주석·외부 문서·human glossary처럼 사람이 제공한 원문은 권위 근거이지 LLM 생산자가
  아니다. 프레임은 이 근거를 ID로 참조하며 원문을 새 의미처럼 복제하지 않는다.
- 검색 시점 query rewrite/judge는 질의 해석기다. Analyzer 분석 결과를 만들거나 저장하지
  않으므로 이 전환의 생산자 범위에서 제외한다.

## 2. 현재 end-to-end 흐름

```text
parser / graph facts / DDL / external evidence
  -> ScanResult.codebase_notes (Framework만)
  -> module_pre_analysis.intent (임시)
  -> step7 code summary | rules.nl | table usage
  -> normalizer + deterministic quality gates
  -> CodeNode / Rule / Question + table-insight state
  -> step8 Module summary | Component name | Table/Column meaning
  -> Neo4j + embeddings + graph delta
  -> search corpus / API / MCP
  -> Architect provenance / Define / Tactical / Plan
```

현재 결함은 각 자연어 필드가 서로 독립된 생산자라는 점이다. `summary`, `rule.nl`,
`usage_summary`, 최종 `description`, 모듈 요약과 컴포넌트 이름이 동일 구조 사실을 서로 다른
prompt에서 다시 해석한다. 필드별 schema는 출력 형태를 제한하지만, 전체 구조에서 요구되는
의미 slot의 population과 교차 필드 일관성을 보장하는 공통 정본은 없다.

## 3. 자연어 생산자 단일 영향표

| # | 생산자 / 전략 | 현재 호출·schema | LLM 자연어 | 코드 소유 사실·검증 | 저장·직접 소비 | 전환 결정 |
|---|---|---|---|---|---|---|
| P01 | Scan agent / Framework | `agents.scan.run_structured_agent` → `ScanResult` | `codebase_notes`; `no_match_reason` | skill contract 주입, regex compile, exclusion 검증, confidence 경계 | `scan_result` state; `codebase_notes`가 Framework step7의 summary/rule/table prompt 공통 prefix | `codebase_notes`는 프레임 근거가 아닌 **run context**로 KEEP/ADAPT. 사실 ID를 참조하는 bounded context로 제한. `no_match_reason`은 운영 진단으로 제외 |
| P02 | Module pre-analysis / 공통 | `module_pre_analysis.yaml` → `ModulePreAnalysisOutput` | `intent` 0~2문장 | 모듈 ID·헤더 주석·imports·exports는 코드가 선택; schema는 빈 intent 허용 | run-local `{module_id: intent}`; 모든 step7 prompt context. 그래프 저장 안 함 | 독립 의미 생산자 제거. 구조·주석 근거에서 만든 module-level frame projection 또는 근거 context로 ADAPT |
| P03 | Code summary / Framework | `code_summary.yaml` → 동적 `summary_response_model(strategy)` → `SummaryOutput` | `summary`, `stereotype`, `questions[].text/reason`, `missing_context[]` | exact target schema; status 조합 validator; summary format normalize; physical identity 누락, callee 복사, 비교 피연산자 변경, write/stereotype 모순 gate | `CodeNode.summary/stereotype`; status/missing JSON; `Question` + `HAS_QUESTION`; caller callee context; parent range 압축; module/component naming; embedding/search/API/MCP/Architect | frame slot meaning/status/missing의 주 생산 경로로 REPLACE. `summary`, `stereotype`, questions는 frame projection. 기존 gates는 KEEP/ADAPT |
| P04 | Code summary batch / DBMS | `code_summary_batch.yaml` → 동적 `batch_summary_response_model` → `BatchSummaryOutput` | P03과 동일, node별 | exact `node_key`·전략 stereotype; P03과 동일한 normalize/gates | P03과 동일한 graph/storage 계약 | P03과 같은 frame assembler/schema를 batch envelope 안에서 사용. batch scheduling은 KEEP |
| P05 | Rule narrative / Framework | `code_rules_examples.yaml` → 동적 `rules_response_model(rows)` → `RulesExamplesOutput` | `RuleItem.nl` | branch table이 exact `R<anchor>` keys와 `cond/then`을 선결정; normalizer가 condition/effects/anchor 복원; grading은 `nl` 제외; Example은 accepted Rule에서 Python으로 파생 | `Rule.nl/cond/then/when`; `Example.given/then`; compact rule/API/MCP/Architect | condition/effects/Example 결정 경로 KEEP. `nl` 독립 생산 제거 후 rule/decision slot projection으로 REPLACE |
| P06 | Rule narrative batch / DBMS | `code_rules_examples_batch.yaml` 또는 gate prompt → `BatchRulesOutput` | `RuleItem.nl` | node/rule 좌표와 cond/then 검증은 P05와 동일 | P05와 동일 | P05와 동일. DBMS batch envelope·gate·priority는 KEEP |
| P07 | Per-code table insight / Framework | `code_table_insights.yaml` → 동적 exact table schema | table별 `usage_summary` | direct READ/WRITE table set과 role은 코드가 고정; columns/source coordinates는 결정론 주입; table identity exact-key validation | run-local table insight collection; 최종 table description prompt 근거 | 별도 문장 생산 제거. READ/WRITE/SELECT/UPDATE frame slot으로 REPLACE하고 최종 TABLE/COLUMN projection의 evidence ref로 사용 |
| P08 | Per-code table insight batch / DBMS | `code_table_insights_batch.yaml` → 동적 batch schema | P07과 동일 | exact node/table keys; direct R/W gate | P07과 동일 | P07과 동일. batch scheduling KEEP |
| P09 | Module summary / 공통 step8 | `module_summary.yaml` → 동적 `module_summary_response_model(strategy)` | `summary`, `stereotype`, `logical_name` | 대상 Module와 전체 분석 멤버를 코드가 고정; 멤버 summary 하나라도 없으면 fail-closed; strategy stereotype literal | `Module.summary/stereotype/logical_name`; graph delta/UI/search 계열 | 멤버 frame의 deduplicated projection으로 ADAPT. 독립 source 재해석 금지, module slot과 coverage/status 추가 |
| P10 | Component naming / 공통 step8 | `component_naming.yaml` → `ComponentNamingOutput` | `name` | 호출그래프/Leiden으로 membership·component ID 결정; 멤버 canonical summary 첫 줄만 전달; 전부 성공해야 publish | 각 FUNCTION의 `component_id/component_name`; graph/UI | deterministic component membership KEEP. 이름은 멤버 frame projection에서 한 번 생성하거나 정본 claim을 재사용하도록 ADAPT |
| P11 | Final Table/Column meaning / 공통 step8 | `table_description.yaml` → table·column 이름을 literal로 고정한 동적 model | table/column `logical_name`, `description`, `missing_context[]` | table/column identity exact; DDL/catalog description, code usage, columns를 코드가 조립; sufficient/partial/insufficient 조합 validator; 이름 population/중복 gate | `Table/Column.logical_name`, `analyzed_description`, semantic status/missing; embeddings/search/API/MCP/Architect | TABLE/COLUMN frame projection으로 REPLACE. DDL/comment/code usage/sample evidence만 허용하고 근거 없는 번역 금지 |

## 4. 의미 필드별 현재 저장과 downstream 소비

| 현재 필드 | 저장 정본 | 주요 downstream | 현재 위험 | frame 전환 후 |
|---|---|---|---|---|
| `CodeNode.summary` | code-node graph property | caller callee summaries, DBMS compact range, Module/Component prompt, embedding/search, node detail/MCP, Architect | 여러 책임·분기·호출·R/W를 자유문 한 필드에 압축하면서 누락을 구조적으로 알 수 없음 | slot meanings의 표시용 projection. 정본 아님 |
| `CodeNode.stereotype` | property + ontology label | UI/filter, Module/Component context, API | summary와 같은 호출이 고르며 write 모순만 부분 검증 | target-level frame classification projection |
| summary status/missing | code-node property/JSON + state | parent/callee context, node detail, 검토 finding | 코드노드 전체 단일 상태라 부분 slot 누락 위치를 알 수 없음 | slot별 status/missing에서 target aggregate를 결정론 계산 |
| `Question.text/reason` | `Question` node + `HAS_QUESTION` | graph delta, search/detail, Architect 감사 가능 | 질문이 어떤 구조 slot과 근거 결손에 대응하는지 ID가 없음 | missing slot ID와 required evidence kind의 projection |
| `Rule.nl` | `Rule` node | compact rules, API/MCP, Architect GWT | 검증 대상 `cond/then`과 별개로 다시 생성되어 의미 드리프트 가능 | accepted decision/rule slot의 한 문장 projection |
| `Example.given/then` | `Example` node | MCP/Architect scenario 근거 | 자연어는 아니며 Rule 구조에서 파생됨 | 현 결정론 경로 KEEP, rule/effect evidence ID 연결 강화 |
| table `usage_summary` | step7 run-local collection | final table description prompt | 코드노드별로 같은 table 의미를 반복 생성 | code operation slot 자체가 정본; table frame이 참조 |
| Table/Column names/descriptions | graph properties | embeddings, ranking, detail/MCP, Architect | 물리명 번역·추정과 실제 DDL/usage 근거의 경계가 필드에 보이지 않음 | evidence refs/status를 가진 TABLE/COLUMN slots의 projection |
| Module summary/name/role | Module graph properties | UI/search/상위 설계 맥락 | member summary의 누락과 중복을 다시 압축 | member frame refs와 module slots의 projection |
| Component name | member code-node properties | UI/component grouping | summary 첫 줄을 다시 해석한 별도 자연어 | deterministic membership + frame-grounded display projection |
| `module_intent` | run-local only | step7 prompts | 헤더 주석 해석이 모든 하위 의미에 보이지 않게 영향 | evidence-ref run context 또는 module frame claim |
| `codebase_notes` | `scan_result` state | Framework step7 prompts | 프로젝트 전역 자유문이 모든 대상 의미에 전파 | 제한된 run context; slot 근거로 자동 승격 금지 |

## 5. 공통 deterministic 사실과 현재 책임처

T131-20의 `StructuralProfile`은 새 추출기를 병렬로 만들지 않고 다음 기존 책임처를 조립한다.

| 구조 사실 | 현재 책임처 | frame 적용 |
|---|---|---|
| target ID/type, owner, file/range | parser `CodeNode`, `AnalysisContext` | target/profile과 stable target evidence ID |
| parent/child와 실행 순서 | parser node hierarchy, range/branch tables | `child_refs`, `ordered_flow`, family 적용 여부 |
| condition/operator/literal/symbol | branch table, source evidence, referenced symbols | decision/loop slot과 exact evidence refs |
| CALL/callee/argument/result | CALL nodes, step5 linking, context callees/outbound edges | call purpose/argument role/result use/side-effect slots |
| direct/effective READ/WRITE | table linker, exact direct table helpers, routine context | SELECT/UPDATE/table role slots; direct와 aggregate 구분 |
| table/column identity와 source coordinates | DDL/catalog graph, table evidence, deterministic column injection | TABLE/COLUMN slots; 물리명 재생성 금지 |
| Rule anchor/cond/effects | branch table + dynamic rule schema normalizer | rule/decision slots; Rule/Example projections |
| transaction/exception 구조 | parser labels/constructs와 source evidence | 해당 근거가 있을 때만 bounded family 생성 |
| callee summary 의존 순서 | existing dependency planner/handoff | 프레임 공개 신호에 재사용; 스케줄러 변경 금지 |

## 6. 자연어처럼 보이지만 프레임 정본이 아닌 LLM 산출물

| 산출물 | 이유 | 처리 |
|---|---|---|
| `ExternalSkill` proposal | table-access regex/transform 구조를 발견하는 제어 출력. 검증 통과 후 구조 추출기에 편입됨 | 의미 프레임 제외; 현 validator KEEP |
| `ExternalCallReview.drops[].reason` | 외부 호출 후보 정화를 위한 운영 판정이며 graph 의미로 저장되지 않음 | 제외; deterministic drop gates KEEP |
| `ResidualPatternResult.no_pattern_reason` 및 제안 설명 | 잔여 table-linking 패턴 탐색/실패 진단 | 제외; 승인된 구조 결과만 StructuralProfile 입력 |
| Scan `no_match_reason`, exclusion verdict text | project scan 운영·감사 데이터 | 제외; 로그/감사 경로 유지 |
| query rewrite/judge 답변 | 분석 후 사용자의 검색 질의를 해석하는 runtime 결과 | 이 spec 범위 제외 |
| processing/error log 문장 | 운영 관측값 | 제외 |
| DDL/comment/external document/human glossary text | LLM 산출물이 아니라 권위 근거 | 프레임 evidence로 참조, 자연어 생산자로 계산하지 않음 |

## 7. Framework/DBMS 합류점과 달라야 하는 부분

- 합류점: target profile, slot family, slot leaf schema, status/missing contract, stable IDs,
  normalizer/validator, graph storage, projections는 공통이어야 한다.
- Framework adapter: per-node 호출, Scan context, early callee-summary release를 유지한다.
- DBMS adapter: parent range, `node_key`, batch envelope, gate batch, token cap을 유지한다.
- 두 전략의 source packet 모양이 달라도 동일한 구조 사실은 동일한 stable evidence/slot ID를
  얻어야 한다.
- priority, rounds, concurrency, semaphore, batch planning은 의미 계약과 독립이며 변경 대상이
  아니다.

## 8. 현재 dirty WIP keep/adapt/remove

### 8.1 Analyzer

| 파일/변경 | 판정 | 이유/후속 |
|---|---|---|
| `.specify/memory/constitution.md`, spec131 `spec.md/plan.md/tasks.md/HANDOFF.md`, `NEW-SESSION-PROMPT.md` | KEEP/ADAPT | 동적 프레임 방향과 불변 조건은 유지. 구현·검증 사실로 현재 상태를 계속 교체 |
| `api/mcp_app.py`의 omitted DB → default 복귀 | KEEP | MCP operator database scoping의 기존 계약 복구 |
| `api/mcp_app.py`의 GWT complete-source 설명 | REMOVE/ADAPT | 정상 packet에 source 본문이 있다는 설명을 semantic-frame projection으로 교체 |
| `search/corpus/evidence_reader.py::gwt_rules` stable evidence ID, condition/effects/when/examples/tables/source anchor | KEEP/ADAPT | rule ID와 구조 필드는 재사용. narrative는 frame projection, source anchor는 좌표 메타 |
| `search/corpus/node_detail.py` symbols/outbound calls/table IDs/flow | KEEP/ADAPT | 중복 제거된 profile/frame projection에 합류 |
| 같은 파일의 `gwt-evidence/v2`, complete source body/hash/source authority | REMOVE | Architect 정상 packet에서 원문 재분석을 유도하고 목표 계약과 충돌 |
| `tests/api/test_mcp_reference_tools.py` DB scoping assertions | KEEP | operator-selected DB 회귀 보호 |
| 같은 테스트의 source body/hash assertions | REMOVE/ADAPT | `semantic-frame/v1`, stable refs, dedupe/no-source assertions로 교체 |

### 8.2 Architect

| 파일/변경 | 판정 | 이유/후속 |
|---|---|---|
| operator-selected Analyzer DB 주입 (`legacy_stage_capture.py` 등) | KEEP | 격리 DB를 끝까지 보존하는 필수 계약 |
| session-limit/terminal failure 분류 (`skill_runner.py`, 관련 tests) | KEEP | 의미 계약과 직교하는 정상 오류 가시성 |
| exact evidence index/ref validation, scenario `evidenceRefs`, cited scalar gate | KEEP/ADAPT | stable slot/claim/evidence ID로 일반화 |
| `legacy_evidence.py`의 SHA/source validation과 source-first authority | REMOVE/ADAPT | `semantic-frame/v1` validation과 frame-first authority로 교체 |
| `legacy_provenance.py`의 GWT shape·frame 배열 보존 | KEEP/ADAPT | source body/hash 없이 semantic frame projection을 provenance에 저장 |
| `legacy_element_refs.py`의 evidence/rule ID matching | KEEP/ADAPT | claim/slot/evidence ID matching으로 확대. narrative text fallback은 제거 목표 |
| `plan_runner.py`, Define/Tactical prompt의 complete-source 우선권 | REMOVE/ADAPT | Analyzer frame/status/missing을 우선하며 source reread 요구 금지 |
| source/hash 중심 tests와 `gwt-evidence/v2` fixtures | REMOVE/ADAPT | no-source packet, dedupe, exact refs, missing-context fail-closed tests로 교체 |
| spec055 문서의 frame 방향 | KEEP/ADAPT | 실제 구현·두 경로 검증 결과로 상태 교체 |

## 9. T131-20 이후 반드시 닫을 소비 계약

1. `semantic-frame/v1`은 CodeNode 하나의 summary 대체물이 아니라 target별 구조 profile과
   applicable slot 집합이다.
2. 모든 자연어 meaning은 `slot_id`, `slot_type`, `target_ref`, `evidence_refs`,
   `status`, `missing_context`에 묶인다.
3. `summary`, `description`, `logical_name`, `rule.nl`, 질문은 같은 frame에서 결정론적으로
   선택·조립되는 projection이어야 하며 별도 source-reading producer가 될 수 없다.
4. graph는 frame 정본을 저장하고 legacy properties는 호환 projection으로만 저장한다.
5. MCP normal packet은 complete source body와 새 source excerpt를 포함하지 않는다. 좌표·ID는
   감사 경로에서 원문을 찾기 위한 포인터이지 원문 복제 허가가 아니다.
6. Architect는 동일 callee/table/claim을 한 번만 받고 scenario에서 stable ID로 참조한다.
7. Analyzer가 `partial|insufficient`를 낸 slot은 Architect가 정상 생성 경로에서 source를 다시
   읽어 보완하지 않고 필요한 evidence kind를 사용자/감사 경로에 노출한다.

## 10. 현재 기준선과 관측 제약

- Analyzer worktree에는 조사 시작 시 modified 9개, untracked 1개가 있었다. 모두 기존 WIP로
  보존하며 reset/checkout/clean하지 않는다.
- Architect worktree에도 spec055와 complete-source 실험을 포함한 다수 WIP가 있다. 위 표의
  판정에 따라 같은 diff 안에서 적응하며 사용자의 다른 변경을 되돌리지 않는다.
- Neo4j `7687`, Analyzer MCP/API `15502`, Architect API `8501`은 실제 listener 기준으로 살아
  있었고 workspace 상태 파일은 일부 stale이었다. 실행 전에는 상태 파일이 아니라 health와
  listener를 다시 확인한다.
- 현재 SGLang endpoint의 `/metrics`는 404였으므로 cache hit을 측정했다고 주장할 수 없다.
  T131-21은 prompt bytes/schema size/token/repetition을 직접 기록하고, cache metric은 서버가
  명시적으로 metrics를 켠 경우에만 부가 근거로 쓴다.

## 11. 완료 판정

이 인벤토리는 다음을 모두 포함하므로 T131-19의 재사용 기준선이다.

- Framework/DBMS 전체 분석 LLM 호출의 자연어 필드와 response schema
- normalizer/validator/quality gate와 deterministic fact owner
- state/graph/storage 및 검색·API·MCP·Architect 소비
- 임시 upstream 자연어와 비의미 운영 출력의 경계
- Analyzer/Architect 현재 WIP의 keep/adapt/remove 판정
- `semantic-frame/v1` 설계가 닫아야 할 projection·dedupe·no-source 계약
