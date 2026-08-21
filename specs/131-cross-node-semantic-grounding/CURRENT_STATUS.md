# Analyzer 의미 무손실 감사 현재상태

**Updated**: 2026-08-21
**Status**: Strategy A 실제 데이터 4함수 progressive pilot 우선; C-080 IR 2.0 구조개편은 TODO로 봉인해 후속 재개
**Analyzer C-066 product commit**: `15fcec8eb77c16a282f30e90df396a09c7fa7eff`
**Analyzer C-062 product commit**: `cd7110d3e7103ff264d6ce041136ab01e96c412f`
**Analyzer C-062 evidence commit**: `f44bc530fad96fd2314b27dea3dc98a33a20ff3e`
**Parser C-062 product commit**: `e28be6aa026ff005b5d4adb447459409cdecf34f`
**Parser C-067 product commit**: `5dd64106540674a4aebfdb37ad897a375950d335`

이 문서는 현재 진행 위치만 소유한다. 영구 규칙은 `ABSOLUTE_RULES.md`, 상세 역사와 기존 수치는 `HANDOFF.md`와 `evidence/`가 소유한다.

## 1. 현재 범위

- 2026-08-20 사용자가 전체 목표와 방향을 승인해 피드백 대기 단계는 종료됐다.
- 제품 수정 전 Analyzer 전체의 상한·절단·표본·skip/fallback·중간 LLM·summary-of-summary·
  timeout/retry/cache/batch/concurrency 경로를 전수조사하고 실제 배선·값·원인·영향도를 먼저 확정한다.
- Analyzer 전체 의미 생산·검증·저장 경로
- 직접 upstream인 Parser·Catalog·Fabric 계약
- Analyzer Neo4j 저장과 Analyzer API/MCP 출력 경계
- Architect는 사용자 지시로 보류: 코드·서비스·GWT 실행 없음

## 2. 현재 환경·WIP

- Neo4j `127.0.0.1:7687`: 현재 세션에서 인증과 `SHOW DATABASES` 재확인 성공.
  `neo4j|test|framework|dbms|system`만 존재하고 모두 online이다. 허용 목록 밖 DB는 0개다.
- Analyzer main HEAD `174f47f0cf46ce249bae22f9f4a11c050bd710b5`, `origin/main`과 일치한다.
  `pipeline/steps/step2_load_ast/evidence_ir.py`에는 IR 1.3 consumer 시도의 보존된 미완성 WIP가 있고,
  spec 131 문서는 현재 결정/검증 슬라이스로 수정 중이다. decoder WIP는 이 문서 커밋에 stage하지 않는다.
- Parser main HEAD `5dd64106540674a4aebfdb37ad897a375950d335`, `origin/main`과 일치하며 clean이다.
  IR 1.3은 역사 evidence로 보존하고 IR 2.0 forward 교정 전까지 제품 binding authority로 확대하지 않는다.
- Catalog HEAD `1f1aaa608a3d7ef8b01ce15c3df962cf0f1cfefb`, 기존 dirty 12개가 있다.
- Fabric HEAD `83c9604e2d6ec3f1ececa47d2e2264a74257843d`, 기존 dirty 7개가 있다.
- Architect HEAD `7b771ac2e7338d03ac8238db5c307d97080232c1`, 기존 dirty 4개가 있다.
  Catalog·Fabric·Architect의 기존 WIP는 읽기만 하고 수정·stage하지 않는다.
- 현재 세션에서 DB 생성·삭제·Analyzer/Architect 서비스 실행은 0회다. 기존 `framework` DB에서
  `ensure_schema`를 확인하고 APAS generation graph transaction을 반복 실패분까지 매번 전부 rollback했다.
  최종 residue 0, active pointer 불변이며 공개 generation 변경은 없다. Architect DB/코드/서비스는
  건드리지 않았다.

### 문서 메모리 무결성

- `manual.md`, `speckit.md`, `principles.md`를 UTF-8 bytes와 Unicode 코드포인트로 재검사했고
  U+FFFD 대체문자는 각각 0개다. 세 파일 원문은 정상이다.
- 첫 PowerShell 출력의 한글 깨짐은 출력 인코딩 문제였으며 파일 손상 가설은 반증됐다.
- Codex 재개 진입점은 `specs/131-cross-node-semantic-grounding/README.md`, 문서 역할 지도는
  `context/DOCUMENT_MAP.md`가 소유한다.
- canonical handoff 13개 파일은 `history/handovers/`로 Git 이력을 보존해 이동했다. 기존 raw 실험
  원자료 164개 파일·9,563,736 bytes는 `_artifacts/history-experiments/`로, 워크스페이스 루트
  `_runs` 2,533개 파일·143,988,505 bytes는 이 spec의 `_runs/`로 바이트 수 그대로 이동했다.
- 워크스페이스 `docs/핸드오프/`에는 README 링크 1개만 남았고 기존 Junction은 링크 자체만 제거했다.
  오래된 시작 프롬프트에는 현재 사용 금지 표지만 추가했고 본문을 보존했다.
- 변경·신규 Markdown의 UTF-8 검사, 상대 링크 확인, `git diff --check`는 오류 0이다.
- 루트 `docs/핸드오프/README.md`와 `docs/robo-작업세트/ROBO-계약.md`의 진입점·DB 계약도
  정정했지만, 두 파일은 워크스페이스 루트 `.gitignore` 정책상 Git 추적 대상이 아니다.

## 3. 확정된 결함

### F-001 — 의미 leaf 600자 상한

- 코드: `pipeline/models/semantic_frame.py`의 `SufficientSlotValue.meaning`, `PartialSlotValue.meaning`
- 유입: `cd9d63b`(2026-08-12)
- 실제 현상: 복잡한 APAS summary가 정확히 600자에서 끝나며 일부는 `partial`, 일부는 `sufficient`로 통과할 수 있다.
- 판정: 표시 제한이 아니라 provider schema의 실제 의미 상한이므로 무손실 계약 위반.
- 상태: 해결됨. 숫자 증설이 아니라 canonical diagnostic/meaning 상한 제거와 obligation-derived 분할·
  exact-once merge 계약으로 교체했고 최초 red 2건과 집중 `231 passed, 134 subtests`를 통과했다.

### C-080 — Parser listener가 final call binding/type/linkage를 소유

- 코드: Parser `CAstListener.callableEvidenceExtraction/bindCall/effectiveTargetScope/
  functionCompatibility/canonicalBuiltinType`
- 유입: Parser `5dd6410`(2026-08-21)
- 실제 현상: grammar listener가 syntax fact를 넘어 call별 final binding status와 compatibility/scope를 결정한다.
  별도 language adapter는 없고 `CLanguageModule`이 listener 결과를 sealer에 직결한다.
- actual 한계: r14 bound 57건은 전부 same translation unit이며 cross-TU positive 0이다.
- 판정: Parser structural authority와 Analyzer semantic authority 위반. IR 1.3을 Analyzer canonical consumer로
  완성하지 않고 IR 2.0 structural fact + Analyzer internal adapter/core로 forward 교체한다.

## 4. 최초 결함 후보와 전수조사 종결 판정

| ID | 위치 | 현재 동작 | 위험 |
|---|---|---|---|
| C-001 | `semantic_frame_projection.py` | `missing_context[:8]`, `questions[:15]` | 진단 의무가 조용히 일부 사라질 수 있음 |
| C-002 | `semantic_frame.py` | rejection reason `[:8]` | 실제 실패 원인 일부가 모든 slot에서 사라질 수 있음 |
| C-003 | `external_evidence.py` | 외부 근거 8개/6000자 초과를 skip | 로그만 남고 semantic sufficiency에 누락이 연결되지 않을 수 있음 |
| C-004 | `discover_datasource_step.py` | Framework 실DB table 최대 1000개 후 계속 | 1001번째 이후 Table/Column/샘플이 그래프에서 빠질 수 있음 |
| C-005 | `range_body.py` | DBMS 큰 부모의 child source를 child LLM summary로 치환 | summary-of-summary와 cross-child 흐름 손실 가능 |
| C-006 | `branch_inventory.py` | exit 보조 표기만 160자로 절단 | Rule/Example 정본은 full AST effect를 사용하므로 Framework에서는 preview; DBMS parent compaction 결합만 남음 |
| C-007 | `llm/embedder.py` | 긴 canonical 검색 text를 문자 상한에서 절단 | graph truth는 남아도 검색 recall/의미가 열화될 수 있음 |
| C-008 | semantic summary role 최대 5 | 다섯 역할에 대형 함수 전체 의미를 집약 | 역할 수 자체는 닫힌 taxonomy지만 사실 의무가 과밀·누락될 수 있음 |
| C-009 | parent/module context와 장문 source 경로 | child/기존 summary 또는 손실 압축이 다음 LLM 의미 입력이 될 수 있음 | summary-of-summary 누적 열화, cross-boundary 조건·순서·효과 누락 가능 |

전수조사에서 추가로 확정한 범주는 다음과 같다. 상세 수량·배선·유입 commit은 evidence 원장이 소유한다.

- C-010: module pre가 최대 80개 signature에서 만든 prose intent를 모든 member LLM에 재주입한다.
- C-016: Agent middleware가 tool evidence를 삭제하고 대화 요약을 최종 구조 결과의 입력으로 쓰며,
  Scan Agent 자유문 `codebase_notes`도 residual/step7 LLM의 권위 맥락으로 재사용했다. 두 배선 모두 제거 완료.
- C-017/C-018: unknown external/scan 경로는 관측 표본 밖의 미발견 구조 completeness를 회계하지 않는다.
- C-019~C-021: view depth 3, wrapper promotion 2 hop, external value 240자가 canonical link를 조용히 끊는다.
- C-022/C-023: model-visible source literal projection은 raw와 일대일이 아니며 source decode는 invalid UTF-8을
  `ignore`한다.
- C-024~C-027: inheritance depth 3, malformed AST의 빈 결과 계속 진행, unknown AST type, source read failure가
  구조/관계/근거 incomplete 상태를 semantic 결과와 충분히 연결하지 않는다.
- C-028/C-029: DBMS column tokenizer 실패가 빈 projection이 되고, source↔AST↔diagnostic hash/status 전수
  closure가 run invariant가 아니다.
- C-030: call-cycle SCC에서 suppressed callee obligation이 prompt/missing-context에 나타나지 않는다.
- C-031: DBMS Catalog lineage의 virtual-source fallback은 graph에 저장되지 않는 edge를 state에 반환한다.
- C-032: run 시작 시 기존 graph를 삭제한 뒤 공개 DB에 단계별로 직접 쓰며 graph completion/epoch 표식과
  consumer guard가 없어 진행 중·중단·실패 partial graph가 API/MCP의 정상 결과처럼 노출될 수 있다.

기준 Framework 실행에는 F-001/C-001~002와 module-intent 재주입이 실제 배선됐다. external/view/inheritance/
malformed-source 계열은 실행 자료·로그·graph상 미발동이다. 설계 결함과 기준 실행 영향은 분리한다.

각 후보는 `안전한 실행 분할 / 표시 전용 / 명시적 degraded / 실제 semantic loss` 중 하나로 실제 caller·consumer·run을 따라 판정한다. 후보라는 이유만으로 즉시 삭제하거나 숫자만 늘리지 않는다.

상세 배선·실측·분류·유입 commit 원장은 `evidence/loss-path-inventory-20260820.md`에 누적했다.
F-001과 C-001~C-032를 모두 종결 분류했으며 C-011/C-012는 C-017로 통합했다. 검증된 책임
슬라이스는 제품 코드에 반영했고, DBMS actual run과 새 sealed 직접 의미 판정은 미검증으로 남겼다.

장문 context의 목표 해법은 원문 요약이 아니라 `전체 의무 inventory → 의무별 exact source/evidence
closure → 필요 시 의무 분할 → canonical ID exact-once merge`다. 아직 최종 설계로 확정하지 않았으며,
가장 큰 봉인 표본에서 exact-source baseline 대비 의미 열화와 속도를 함께 재야 한다.

## 5. Architect 보류 발견

- `plan_runner.py`와 `stage_runners/tactical.py`에 Command당 GWT `1~4개` 상한이 있다.
- 유입 commit은 `8f25345`(2026-08-19)다.
- 실제 Rule/UserStory 의무 수에서 계산되지 않은 고정 상한이므로 결함 가능성이 높다.
- 최신 사용자 지시로 Architect는 보류한다. 현재 작업에서는 증거만 보존하고 파일·테스트·서비스를 변경하지 않는다.

## 6. 새 모델 현재 사실

- 서버 alias와 접속 정보는 그대로이고 내부 모델은 `Qwen/Qwen3.8-27B-FP8`로 교체됐다.
- 제한 replay는 기존 100초 이상에서 새 모델 7~8초로 빨라졌다.
- 새 모델에서도 복잡한 역할이 정확히 600자 경계에 닿았으므로 F-001은 모델 문제가 아니라 제품 계약 결함이다.
- 새 모델 기준 APAS 전체 3층 재검증은 아직 수행하지 않았다.

## 7. 문서 충돌 원장

| 충돌 | 낡은/모호한 문구 | 현재 판정 |
|---|---|---|
| `bounded` 의미 | constitution/spec의 bounded frame이 문자열/결과 상한처럼 구현됨 | 구조와 호출은 유한하게 분할하되 canonical 의미·의무는 자르지 않는다 |
| summary 최대 5 | spec131 요구 12·24와 contract §6.1 | taxonomy 5와 실제 사실 의무를 분리해 재설계 전까지 미승인 |
| DB 이름 | 검증 정본 04의 framework=`test`, DBMS=`neo4j`; 최신 spec의 `framework|dbms` | 허용 DB는 네 개뿐이며 실제 재분석은 `framework|dbms` 재사용. 역사 정본의 수치는 역사 실행 조건으로만 읽음 |
| 재실행 횟수 | 04·11의 framework 2/DBMS 4 상한 | 과거 실행 예산 기록. 제품 결과 cap이 아니며 현재 실행은 preflight 후 필요한 최소 횟수만 수행 |
| Architect 후속 | spec131 plan의 Architect GWT 단계 | 현재 사용자 지시로 보류 |
| 과거 완료 | 2026-08-12 HANDOFF 완료 문구 | 현재 600자 및 추가 후보가 발견돼 완료 주장 폐기, 역사 snapshot으로만 보존 |
| 원문 완전성 | bounded frame이 문자/개수 상한을 허용하는 듯 읽힐 수 있음 | 원문 bytes/hash 불변, AST 분할 coverage·중복·순서 잔차 0. 100%는 목표이고 실측 미달은 숨기지 않음 |
| 공통 memory 표시 | PowerShell 기본 출력에서 `manual.md`, `speckit.md` 한글이 깨져 파일 손상으로 오인 | 코드포인트 검사 결과 대체문자 0. UTF-8 출력 설정 후 읽고 bytes로 반증 |
| 문서 진입점 | 00/NEW-SESSION/HANDOFF/README가 서로 현재 진입점이라 주장 | `specs/131-cross-node-semantic-grounding/README.md`만 진입점. 규칙은 ABSOLUTE, 현재상태는 CURRENT, 나머지는 역사/증거 |
| 직접 의미 판정 모집단 | 헌법 IX·04는 코드노드 20+모든 TABLE, principles §12.4는 종류 혼합 30~40, 기존 evidence는 서로 다른 봉인40 | 실행 전에 단일 봉인 계약으로 통합해야 함. 현재 어느 과거 표본도 새 모델·새 무손실 계약 합격 근거가 아니며 사용자 피드백 전 미확정 |

## 8. Git 비교 축

1. 구조화 프레임 전 기준
2. `cd9d63b` 2026-08-12 프레임 도입 직후
3. `d47f172`~`7c91721` 2026-08-18~19 배치·성능·summary 변경
4. 현재 HEAD

각 후보마다 `git blame/log/show`로 유입 commit과 당시 테스트·근거를 확인하고, 새 결함/기존 결함/기존 결함 통과를 분리한다.

## 9. 현재 실행 순서

1. 완료: Analyzer 전체 상한·절단·표본·skip/fallback·중간 LLM·summary-of-summary·
   timeout/retry/cache/batch/concurrency inventory와 producer→consumer 추적·유입 commit closure.
2. 완료: 확정 결과를 사용자에게 한 번 보고했다.
3. 완료: 계약을 고정했고 첫 실패·경계·과대 입력 red test 2건을 red→green으로 전환했다.
   F-001/C-001/C-002와 동일 canonical diagnostic 상한을 제거했으며 집중 회귀는
   `231 passed, 134 subtests passed`다.
4. 완료(Framework): FUNCTION taxonomy를 evidence-anchor obligation slot으로 분리하고, Framework
   call별 exact profile/source closure, token-derived 분할, source-order/canonical ID exact-once merge를
   red→green으로 고정했다. 파생 module/callee/codebase prose도 Framework 권위 packet에서 제거했다.
5. 완료(DBMS): effective AST CodeNode별 exact source/evidence fragment runner로 교체했고 CALL을 포함한
   non-routine owner와 full-source comparison validator를 고정했다. A131-04b는 완료했다.
6. 완료(C-003): linked external chunk의 8건/6000자 canonical 선택 상한을 제거했다. 모든 chunk를
   해시 검증 후 보존하고 chunk별 FUNCTION processing obligation과 exact prompt payload로 연결했으며,
   외부 payload도 token-derived 실행 분할 계산에 포함했다. step6 23 tests와 step7 214 tests가 통과했다.
7. 완료(C-010): 잘린 signature에서 intent를 만들던 Framework module pre-analysis producer와 prompt,
   23회 호출 및 `module_intents` 배선을 제거했다. 전체 `1049 passed, 5 skipped`,
   prompt byte baseline 23/23이 통과했다.
8. 완료(C-022): quote delimiter·escape를 설명 marker로 바꾸던 비가역 source projection producer와
   production/replay 호출을 제거했다. Framework/DBMS prompt가 번호 외 source line text를 그대로
   전달한다는 계약과 반대 fixture를 고정했다. 전체 `1049 passed, 5 skipped`, prompt 23/23이다.
9. 완료(C-030): Framework/DBMS 공통 dependency planner가 suppressed `(caller, callee)`를 context의
   explicit unavailable dependency로 물질화한다. planner는 이를 CALL fact와 별도 semantic obligation으로
   실제 prompt에 넣고 normalizer는 canonical callee ID를 보존한 `partial/insufficient`만 허용한다.
   전체 `1051 passed, 5 skipped`, prompt 23/23이다.
10. 완료(C-009c): module/component가 member summary prose를 다음 LLM에 보내던 두 prompt와 response
    model을 제거했다. canonical member slot은 source-order로 정확히 한 번 투영하며 중복 slot 또는 필수
    role 없는 member는 즉시 실패한다. source-owned display/stereotype은 추측하지 않고 partial/unverified로
    남기며 Neo4j의 이전 실행 파생 속성도 null map으로 제거한다. 전체 `1057 passed, 5 skipped`, prompt
    hash `17/17 byte-identical`이다.
11. 완료(C-016): 모든 structured agent의 50,000-token tool-result clear와 75,000-token 대화 LLM 요약을
    제거했다. 과거 transcript/tool evidence는 exact하게 유지하고 context 초과는 명시적 실패로 드러낸다.
    `ScanResult.codebase_notes`도 residual 및 Framework/DBMS step7 prompt와 호출 시그니처에서 제거해
    한 LLM의 자유문이 다음 LLM의 의미 근거가 되는 경로를 닫았다. C-017/C-018의 큰 탐색 exact partition은
    별도 미완 의무다. 변경 전 red 2건, focused 회귀 118건, 전체 `1058 passed, 5 skipped`, prompt hash
    `17/17 byte-identical`을 확인했다.
12. 완료(C-023/C-025/C-026/C-027/C-029): parser-supported source 전건을 AST `FILE.filePath`와
    parser diagnostic에 source-order exact-once로 대응시키고 source/AST/diagnostic SHA-256, diagnostic
    `EXACT`, `knownAndComplete`, missing declaration 0, lexer/parser error와 recovery 0을 Neo4j mutation 전에
    검증한다. invalid UTF-8, missing/orphan/duplicate AST, stale/missing/non-EXACT diagnostic, malformed AST,
    ontology unknown wrapper는 run 전체를 fail-closed한다. 같은 source byte cache와 검증된 AST hash/language를
    step2가 재사용하고 `source-ast-closure.json`을 atomic write한다. 현재 corpus는 23→23→23, mismatch 0이며
    변경 전 red 6건→green, 전체 `1067 passed, 5 skipped`다.
13. 완료(C-032): node identity를 `(owner, generation, canonical id)`로 격리하고 모든 relationship에도
    generation을 기록한다. run 시작 wipe를 제거했으며 입력 source/AST/diagnostic expected closure와
    graph node/edge/semantic-slot actual identity count/hash ledger를 닫은 뒤 한 transaction에서
    `completed|completed_with_findings` manifest, active pointer 교체, 검증된 legacy/이전 generation 정리를
    commit한다. 실패·중단은 staging만 terminal 처리·삭제하고 직전 completed pointer는 유지한다.
    relationship의 generation 누락·불일치와 동일 canonical edge 중복은 publish 전에 거부하며, pinned
    reader의 generation override도 거부한다. REST/MCP/search와 검증·reembed 스크립트는 active completed
    generation만 읽는다. red 5/5는 green, failure injection·transaction 테스트 포함 전체
    `1083 passed, 6 skipped`, prompt `17/17`, 실제 Neo4j
    rollback publish 1/1과 composite constraint `EXPLAIN`을 통과했다. 영속 DB 변경은 0이다.
14. 완료(C-024): Parser listener가 내는 exact `extendsType/implementsTypes`를 Analyzer Module·Neo4j에
    보존하고 원문 선언 정규식과 depth 3을 제거했다. generic comma를 보존하는 AST field parser, Java
    visited-set closure, Python exact C3 MRO로 4-hop/cycle/multiple-interface를 닫았고, invalid C3는 fail-closed한다.
    `REFERENCES → prompt JSON → SYMBOL evidence`까지 검증했다. 최초 red 2건과 C3 오순서 red 1건은 green이며
    Analyzer `1089 passed, 6 skipped`, prompt `17/17`, Parser current report `154 passed, 8 skipped`다.
15. 완료(C-028/C-033): Parser `2e33720` evidence v2 listener가 owner별 unqualified SQL identifier를
    exact-once 생산하고 Analyzer는 v2 명시 empty와 누락을 구분해 DDL/query-scope unique owner만
    투영한다. sqlglot source tokenizer/alias heuristic/silent-empty fallback을 제거했다. DDL quoted bit도
    `Column`→Neo4j→step7→MCP detail까지 보존해 `"Foo"`/`"foo"`를 exact case로 분리한다. red 5건은
    green이며 Parser `157/0/0/8`, Analyzer `1093 passed, 6 skipped`, prompt `17/17`, Neo4j rollback
    저장·조회/잔여 0을 통과했다. `_runs/dbms`가 없어 actual DBMS raw/parsed 표본은 미검증이다.
    후속 추적에서 DDL PK/UNIQUE/FK/comment가 별도로 lower하는 C-034와 step8 source-line focus가
    quoted sibling을 다시 casefold하는 C-035를 새 pending으로 분리했다. C-034 실제 최소 반례는
    `"Foo"` PK/comment가 `"foo"`에 복제되고 non-unique FK가 `one_to_one`이 되는 것을 확인했다.
    이어 Catalog/Fabric confirmed 응답 재결합이 `(db/schema/name).upper()`이고 legacy discovery는
    `name.upper()`뿐인 C-036도 발견해 별도 pending으로 기록했다.
    COMMENT literal 경계 추적에서는 Oracle 물리옵션 전역 regex가 quoted identifier/DEFAULT literal까지
    재작성하는 C-037을 추가 발견했고 red를 고정했다.
    같은 원문 regex의 dialect 감지가 string marker를 실제 방언 구문으로 오인하는 C-038도 red로 분리했다.
16. 완료(C-034/C-035/C-037/C-038): DDL PK/UNIQUE/FK/COMMENT와 step8 source focus가 공통
    `Q:exact|U:uppercase` identity를 사용한다. COMMENT identity/literal은 exact source token 경계의
    sqlglot AST가 소유하며 regex 재해석을 제거했다. Oracle physical option/VARCHAR unit/SQL*Plus `/`와
    dialect 감지도 syntax token 위치만 사용해 DEFAULT·quoted identifier를 rewrite하지 않는다.
    최초 red 9건은 green, 집중 `36 tests`, Analyzer 전체 `1102 tests, 6 skipped`, prompt `17/17`을
    통과했다. Neo4j `test`
    rollback 저장·조회에서 quoted sibling PK/comment/FK를 확인했고 node/relationship residue는 0이다.
    `_runs/dbms`가 없어 actual DBMS raw/parsed 표본은 계속 미검증이다.
    잔여 quoted `SET search_path` schema drop은 C-039 pending으로 분리했다.
    후속 source→Parser AST→step5 추적에서 exact AST 사용을 DDL Table과 다시 결합할 때
    `name/schema.upper()`를 쓰는 C-040과, 현재 unresolved 경로에서 quoted `"Foo"`/`"foo"`의
    `ObjectReference` ID·edge·node를 하나로 합치는 C-041을 추가 발견했다. 둘 다 LLM 이전 실제
    의미 손실이며 유입은 `d4986087`이다. C-041은 현재 Fabric의 quoted unresolved 계약에서도
    즉시 발동하고 C-040은 DDL Table quoted identity가 model에 없는 상태와 함께 닫아야 한다.
17. 완료(C-041): unresolved physical object ID가 공통 `Q:exact|U:uppercase` schema/name key를
    사용하고 `ObjectReference`→Neo4j/live graph에 quoted provenance를 보존한다. 최초 red 2건은
    green, 집중 `78 passed, 1 skipped`, Analyzer 전체 `1103 tests, 6 skipped`, prompt `17/17`이다.
    Neo4j `test` rollback 저장·조회에서 quoted sibling 두 node/두 READS와 residue 0을 확인했다.
    같은 역추적에서 Oracle database link는 grammar상 quoted component를 허용하지만 Parser AST가
    quote identity를 내지 않는 C-042를 발견했다.
18. 완료(C-042): Parser `de4e792` evidence v3 listener가 database link의 database/domain/
    connection-qualifier를 exact token·quoted bit·grammar 순서로 생산한다. Analyzer는 이 구조만 canonical
    identity와 prompt/ObjectReference/Neo4j에 사용하고 link 문자열을 재파싱하지 않는다. component가
    없는 v1/v2/v3 link AST는 좌표·version과 함께 fail-closed한다. Parser `158/0/0/8`, Analyzer 집중
    `99 passed, 1 skipped`, 전체 `1103 tests, 6 skipped`, prompt `17/17`, Neo4j rollback 두 quoted
    link node/두 READS/잔여 0을 통과했다. `_runs/dbms` 부재로 actual corpus 발동은 미검증이다.
19. 완료(C-036/C-039/C-040): exact source token의 quoted `SET search_path` identity를
    DDL Schema/Table/Column/FK ID와 writer까지 보존한다. unquoted ID는 uppercase, quoted segment는
    `Q~exact` canonical ID다. Catalog bulk merge는 schema+table `Q/U` key를 쓰고, scoped provider가
    quoted proof 없이 confirmed를 반환하면 fail-closed한다. step5 Parser AST linker도 schema/table
    quoted key를 사용하며 legacy bare-name scanner는 schema 동명 또는 quoted table을 추측하지 않는다.
    최초 red 6건 green, 집중 `34 passed`, Analyzer 전체 `1109 tests, 6 skipped`, prompt `17/17`이다.
    Neo4j `test` rollback에서 quoted schema sibling 두 Schema/Table/Column과 BELONGS_TO/HAS_COLUMN,
    quoted properties, 서로 다른 canonical ID, residue 0을 확인했다. Catalog·Fabric WIP는 무변경이다.
20. 완료(C-019/C-020/C-021/C-043): CREATE VIEW의 sqlglot AST scope가 CTE/derived alias를 제외한
    physical Table dependency를 schema/name quoted canonical ID로 exact-once 생산하고 writer까지 보존한다.
    view expansion은 이 ID만 따라 visited-set finite closure하며 숫자 depth나 normalized SQL regex를 쓰지
    않는다. external pass-through wrapper도 유한 routine 이름의 monotone fixpoint까지 승격하고, unknown
    skill key/direct literal/referenced Constant/TargetIndex의 임의 240자 gate를 제거했다. 최초 red 7건은
    green, 집중 `34 passed, 13 subtests`, Analyzer 전체 `1115 tests, 6 skipped`, prompt `17/17`이다.
    Neo4j `test` rollback에서 actual Table/READS writer의 dependency property와 기저 READS, residue 0을
    확인했다. 기준 Framework graph에는 view/REFER_TO가 없어 actual 기준선 영향은 원래 미발동이다.
21. 완료(C-044/C-045): PostgreSQL Parser `e5fbf6b` evidence v4 listener가 grammar `qualified_name`의
    catalog/schema/table과 component별 quote bit를 분리 생산한다. Analyzer는 v4를 fail-closed 소비하고
    datasource namespace와 SQL catalog를 분리한 Schema/Table/Column/ObjectReference canonical ID,
    COMMENT/FK/view dependency, exact linker, prompt/live graph/Neo4j property까지 보존한다. provider에
    catalog proof가 없으므로 catalog-qualified confirmed 응답은 정상 승격하지 않는다. unqualified FK
    target은 source table의 catalog/schema를 상속한다. 최초 Parser red 1 + Analyzer red 3 + FK red 1은
    green, Parser 전체 `159 tests, failures 0, errors 0, skipped 8`, Analyzer 집중 `50 passed` 후 전체
    `1120 tests, 6 skipped`, prompt `17/17`이다. Neo4j `test` rollback에서 catalog sibling Table/
    ObjectReference 4개와 READS 4개, `dbms` rollback에서 동명 Schema 2개, 각 residue 0을 확인했다.
    `test`의 legacy `(db,name)` Schema constraint actual failure도 `ensure_schema` 제거 목록에 반영했으며
    검증 중 영구 DB schema 변경은 하지 않았다.
22. 완료(C-004/C-017/C-031): Catalog discovery가 `truncated>0`인 incomplete prefix를 merge하기 전에
    fail-closed하고, unknown external skill은 positive source의 모든 non-whitespace span을 overlap 없이
    exact-once 덮어야 승인한다. Catalog lineage 단계는 독립 producer가 아니라 Analyzer graph의
    `nodes/edges` view를 존재하지 않는 `flows`로 재수입하던 순환 dead wiring이라 DBMS pipeline과 구현을
    제거했다. formal red 3건을 확인한 뒤 집중 `37 tests`, Analyzer 전체 `1122 tests, 6 skipped`가
    green이다. APAS table 7개, external chunk 0,
    Catalog lineage flow 0이라 세 결함 모두 기준 actual 영향은 0이다. Catalog·Fabric·Architect WIP는
    무변경이며 Neo4j write도 수행하지 않았다.
23. 완료(조사 C-018 및 신규 C-046~C-051): Analyzer 전역의 언어별 source 재파싱과 중간 LLM 경로를
    `evidence/analyzer-syntax-ownership-audit-20260821.md`에 생산자·소비자·actual 값·유입 계열까지 누적했다.
    Framework 의미 owner=routine, DBMS owner=effective AST CodeNode 계약은 공통화하지 않는다. 구문 차이는
    Parser grammar/listener가 versioned exact evidence IR을 생산하고 Analyzer는 언어 중립 resolution만 하는
    경계로 확정 중이다. Scan/Deep Agent는 제거하지 않고 normalized fact pattern 제안자로 유지한다.
    기준 actual에서 call-review LLM은 reason에 “확신 부족이라 드롭하지 않음”이라고 쓴 4개 이름을 schema
    위치만으로 승인해 21 routine의 CALLS 46개를 실제 삭제했다(C-047). 따라서 C-014의 종전 안전 판정을
    철회했다. table residual 193개가 이후 의미/API에 전혀 소비되지 않는 C-048, REFERENCES 8,113/INIT_BY 97과
    Rule grounding/table/external/column prompt를 Analyzer host lexer가 생산하는 C-049, DBMS dual source fallback과
    Oracle 전용 column evidence C-050, call occurrence 12,112개의 arg/range/identity 축소 C-051도 분리했다.
    당시 Parser 호출 WIP는 PL/pgSQL regex 잔존, Python trailer range, version/source hash/import/symbol/literal/
    embedded evidence 공백 때문에 계약으로 승인하지 않았고, 이후 25의 common IR로 교체했다.
24. 완료(C-047): 이름/빈도만 받은 중간 LLM이 canonical CALLS를 삭제하던 생산자·prompt·schema·validator·
    설정·배선을 전부 제거했다. red `5 failed, 2 passed`에서 집중 `16 passed, 43 subtests`, 전체
    `1178 passed, 6 skipped, 367 subtests`, prompt `16/16 byte-identical`로 전환했다. actual APAS를 현재
    reader+실제 skill 계약으로 read-only 전수 재분류해 `12,112 = 958 + 6,935 + 4,143 + 76`, incomplete/
    wrapper unresolved 0을 확인했다. 모순 대상 46건은 6파일/21 routine의 source↔Parser AST↔external edge가
    모두 일치했고 exact-once 보존됐다. 봉인 판정은 전부 `occurrence=true, semantics=unknown, external 보존`이다.
    Neo4j write/replay는 하지 않아 `framework` DB의 과거 삭제 상태는 아직 교정되지 않았다.
25. 완료(common evidence IR 조사·구현): Parser WIP 재검토에서 C-052/C-053을 추가 발견했다. C frontend는 build
    macro set 없이 조건부 컴파일 첫 branch를 무조건 active로 남겨 기준 APAS의 `#if 0` 구간 최소 69 AST
    node를 거짓 active로 만들고, 두 `#if 0 ... #else`의 실제 else statement를 AST 0개로 삭제했다.
    WIP token offset은 Unicode code point와 Java UTF-16을 혼용하고 position encoding/source hash/column/
    recovery offset rebase가 없으며 Python trailer range는 full callee를 포함하지 않는다. 따라서 단순
    call text/offset 필드가 아니라 versioned envelope·half-open exact range·canonical occurrence ID·
    conditional provenance·언어별 coverage matrix를 red로 고정한다. 그 뒤 Parser WIP를 계약에 맞게
    교체하고 Analyzer의 C-046/C-049/C-051 source 재파싱 소비자를 순차 제거한다.
25a. Parser `d8dca38`: versioned source envelope, source-once + Unicode code-point half-open range,
    canonical fact ID, grammar/presence table, C/Java/Python call boundary와 C conditional region을 listener→공통
    sealer 경계로 구현했다. 첫/대체 branch 추측 recovery를 제거했다. Java 평탄화 constructor, Python 이름
    없는 callable, parser recovery의 거짓 complete, workspace 밖 full-file evidence 생략, nested conditional의
    effective-parent 손실을 각각 red로 추가 발견·교정했다(C-054). Oracle/PostgreSQL call kind는 명시
    `unsupported`다. Parser 전체 `48 suites, 168 tests, failures 0, errors 0, skipped 10`이다.
25b. actual APAS 23파일은 `12,113 call facts = 12,080 active + 33 inactive`, conditional region 17,
    diagnostics/recoveries 0, 두 replay byte-identical이다. fact/call ledger hash가 닫혔고 봉인 합집합 526/526,
    conditional 내부 81/81을 직접 대조했다. legacy의 거짓 active 33개를 inactive evidence로 보존하고 삭제됐던
    active else call 1개를 복구했다. Parser `48de7f8`에서는 exact callee range만 보고 Analyzer가 terminal을
    재파싱하지 않도록 grammar listener가 `calleeKind=named|constructor|expression`과 nullable
    `terminalName`을 발행한다(C-056). APAS는 named 12,113 / constructor 0 / expression 0으로 전수 분할됐다.
    like-for-like 기준선 대비 output +288.17%, wall +46.44%, peak heap +5.73%이며 성능 우월은 주장하지
    않는다. 반복 text 제거로 최초 시제품보다 줄였고 cap/sample은 없다.
    세부 원장은 `evidence/common-evidence-ir-validation-20260821.md`다.
25c. 완료(Analyzer call consumer, C-046/C-051/C-057~C-059): v1 envelope/source/hash/strict Parser decode/
    Unicode range/canonical ID/grammar·presence table/completeness/callee syntax를 step2에서 fail-closed한다.
    compatibility AST call population/order/line/terminal과 canonical fact를 exact-once 대조하고 Framework
    routine owner를 별도 mapping으로 더했다. wrapper 한 줄 source regex와 Analyzer 선행 UTF-8 decode를
    제거했으며, evidence 없는 wrapper target은 발명하지 않고 explicit unresolved다. 분류 뒤에도 canonical
    call ID 전건에 outcome ledger를 남겨 aggregate count만 맞는 C-059를 닫았다.
25d. actual APAS 23파일은 call 12,113/unique 12,113/owner resolved 12,113, duplicate 0이다. resolution
    12,113행은 edge 869, excluded 11,126, inactive preserved 33, active wrapper unresolved 85이며 replay ledger
    SHA-256 `2ba765b1eebd16de73890ef496f423c0a9103884333d6d2679e5c028c9521c5e`로 동일하다. Parser 봉인 526/526을
    Analyzer fact/range/presence/owner boundary와 대조했고, owner 40건을 주 에이전트가 직접 판독해 40/40
    enclosing routine과 일치했다. 집중 `28 passed, 10 subtests`, actual opt-in `1 passed`, 권위 unittest
    `1121 tests OK, skipped 6`, 전체 pytest `1194 passed, 7 skipped, 367 subtests`다. 상세는
    `evidence/analyzer-call-consumer-validation-20260821.md`다.
25e. 다음 책임 슬라이스는 C-055 Parser macro evidence와 wrapper target에 필요한 literal/symbol argument
    evidence다. 그 뒤 DBMS effective CodeNode owner 및 Neo4j/API/MCP actual replay를 별도 승인한다. 따라서
    현재 결과를 전체 파이프라인 정확도 합격으로 확대하지 않는다.
25f. C-055 착수 조사에서 pinned C grammar가 preprocessing-file 문법을 비워 두고 directive 전체를 hidden
    token 하나로 만드는 것이 확인됐다. Parser listener와 Analyzer가 각각 regex로 `#define`을 재파싱하는
    이중 owner다. WG14 N3096 §6.10과 ANTLR 공식 lexer/parser 계약을 대조해 별도 pinned C-preprocessor
    grammar + logical/physical range mapping을 채택했다. macro 6종 formal red는 `expected 6, actual 0`으로
    확인됐다. 근거와 rollback 경계는 `evidence/c-preprocessor-grammar-research-20260821.md`다.
25g. 완료(Parser C macro producer, C-055/C-060): 별도 pinned ANTLR lexer/parser, phase-2 logical↔physical
    code-point mapping, block-comment lexer mode, malformed/unclosed explicit unresolved, object/function·variadic·
    digraph 계약을 구현했다. `CAstListener`의 `#define` regex는 제거하고 legacy DEFINE은 grammar fact에서만
    투영한다. Parser 전체 48 suites/170 tests(실패 0, error 0, skipped 10), APAS 23파일 deterministic replay는
    macro 183 = object 182 + function 1 = active 182 + conditional 1, unresolved/duplicate 0이다. macro ledger는
    `b1f51f924272511d019adc6ff3bf831aa1445c66e113da1742fc278ee41ae42a`, 봉인 직접 판정은 34/34 일치다.
    상세는 `evidence/parser-c-macro-evidence-validation-20260821.md`다. Analyzer consumer와 `_FUNCTION_MACRO_RE`
    제거는 아직 pending이며 Parser 성공을 E2E 성공으로 확대하지 않는다.
25h. 같은 조사에서 C include/conditional 문자열 재파싱 C-061, project type-name regex prepass C-062,
    UCN/dialect canonical preprocessing identifier C-063을 새로 분리했다. 모두 범용 syntax/identity owner 원칙
    위반 가능성이 있어 숨기지 않되, 근거 없는 삭제로 parser 정확도를 낮추지 않고 각 red와 actual을 고정해
    후속 grammar-owned 책임으로 교체한다.
25i. 완료(Analyzer C macro consumer, C-055): v1 macro payload의 exact name/range/kind/parameter/variadic/
    replacement/presence를 strict 검증하고 step2 state에 canonical ID exact-once로 보존한다. Analyzer의
    `_FUNCTION_MACRO_RE`, source line read, C identifier 재파싱을 제거했다. declaration만으로 call 시점
    expansion을 단정하는 것도 금지했다. define/undef/redefinition/include/build define의 ordered effective
    state가 아직 없으므로 active declaration은 `insufficient_macro_expansion_state`, conditional declaration은
    `insufficient_conditional_macro_definition`으로 macro fact ID와 함께 명시 unresolved다.
25j. actual APAS 두 replay는 call 12,113 + macro 183, duplicate 0, macro ledger
    `b1f51f924272511d019adc6ff3bf831aa1445c66e113da1742fc278ee41ae42a`로 동일하다. 종전 regex가 제외한
    `zordmfax00001.c`의 `PFM_DBG` 76건은 유일한 조건부 function macro fact와 exact-once 결합해 unresolved로
    보존됐다. outcome은 edge 869 / excluded 11,050 / preserved 33 / unresolved 161, resolution ledger는
    `61f6c05cf1efe59a76e5a693b52b791e9534a30a2aacb06437f0a8d702d74429`다. Parser 봉인 call 526/526 + macro
    34/34를 Analyzer payload와 대조했다. 집중 `210 passed, 1 skipped, 19 subtests`, actual `1 passed`, 권위
    unittest `1122 tests OK, skipped 6`, 전체 pytest `1200 passed, 7 skipped, 367 subtests`다. 상세는
    `evidence/analyzer-c-macro-consumer-validation-20260821.md`다.
25k. 신규 C-064: macro declaration completeness는 invocation resolution completeness가 아니다. ordered
    `#define/#undef` lifetime, include closure, build predefined macro와 call 위치를 Parser가 canonical evidence로
    생산해야만 `function_macro` definitive exclusion을 재승인할 수 있다. 현재 actual 76건의 explicit
    unresolved는 이 생산자 의무를 숨기지 않는 degraded 결과이며 최종 해결로 오인하지 않는다.
25l. C-064 배선·입력·1차 근거 조사: Parser에는 compile database/compiler frontend 계약이 없고 Java 17 +
    ANTLR raw-source parse만 있다. actual APAS는 23 C source/header 0, compile command 0, include 450회/고유
    284개가 전부 미해결이며 host/WSL compiler와 Docker CLI도 없다. 따라서 현재 자료로 configuration-specific
    expansion을 증명할 수 없다. Clang JSON compilation DB/PPCallbacks/SourceManager/TokenBuffer, GCC·MSVC
    preprocessor output 정본을 교차 검토했다. 특정 frontend를 모든 compiler의 정답으로 승격하지 않고,
    actual-toolchain trace=`authoritative`, 대체/text-only replay=`compatible_replay`, 입력·capability 결손=
    `unresolved`의 provider 계약을 채택한다. ANTLR은 expanded token AST/call syntax owner로 유지한다.
    v2 identity는 compile action/configuration/include instance/expansion instance/expanded semantic occurrence를
    포함하며 source range만으로 합치지 않는다. 상세와 red/rollback 경계는
    `evidence/c-preprocessor-expansion-closure-research-20260821.md`가 소유한다. actual authoritative 판정은
    build command와 284 header closure가 공급되기 전까지 명시 미검증이다.
25m. 완료(C-064 build-input 계약 1차): Parser `a531060`은 JSON compilation database를 shell 실행/분해 없이
    exact argv와 literal/resolved path로 보존한다. 동일 TU 다중 configuration을 합치지 않고 shell-only,
    malformed field/source/database 결손을 typed unresolved로 회계하며 canonical object ID + duplicate ordinal로
    exact-once를 닫는다. 최초 symbol 부재 red와 `PARTIAL`/`UNRESOLVED` 경계 실패를 확인한 뒤 집중 7/7,
    Parser 전체 49 suites/177 tests(failure 0, error 0, skipped 10)를 통과했다. entry 순서와 object field 순서를
    바꾼 replay ID 원장이 동일하고 봉인 fixture 7종을 직접 대조했다. 이 커밋은 trace producer/expanded-token
    ANTLR/Analyzer v2 배선을 포함하지 않으므로 actual 의미 정확도 승격 근거가 아니다. 상세는
    `evidence/parser-compilation-database-contract-validation-20260821.md`다.
25n. 완료(C-064 preprocessing closure producer): Parser `00fdf2a`는 workspace 아래 모든
    `compile_commands.json`을 cap 없이 재귀 발견하고, source별 build/trace population과 canonical evidence
    ID를 `configuredPreprocessing/v1`으로 발행한다. literal/resolved source가 exact하게 일치하는 command만
    결합하며 basename/content 추측은 금지한다. 실제 APAS 23파일은 build command/header/toolchain trace
    결손 때문에 23/23 `unresolved|unresolved`이고 preprocessing ID 46개가 exact-once다. Parser 전체
    50 suites/182 tests, independent r1/r2 ledger hash 동일, 봉인 call 526/526·macro 34/34를 통과했다.
25o. 완료(C-064 Analyzer fail-closed consumer): Analyzer `2d5f4c4`는 C source의 preprocessing context를
    strict 검증하고 v1 raw-source call을 authoritative semantic occurrence로 승격하지 않는다. actual APAS
    `12,113 = unresolved_preprocessing 12,113`, CALLS edge 0, 입력/회계 잔차 0이며 ledger hash
    `01590fb9e31b8e934907f1a994f8261d92216e10c086778baf1b5678dcec850a`가 replay에서 동일하다. 봉인 직접
    preprocessing 판정 6/6과 Analyzer 전체 `1206 passed, 7 skipped, 367 subtests`를 통과했다.
25p. 완료(C-065): `classify_call_sites`는 canonical call ID 전건 outcome ledger를 만들지만
    `run_executor`가 LangGraph update payload를 버리고, generation publish는 node/edge/semantic slot만
    닫는다. quality status도 `analysis-findings.jsonl`만 세므로 actual처럼 12,113건 전부 unresolved인 run이
    일반 `completed`, CALLS 0인 graph로 공개될 수 있다. REST/MCP/search는 generation quality와 call
    occurrence ledger를 읽지 않아 `호출 없음`과 `호출 12,113건 전부 미해결`을 구별하지 못한다.
    유입 경계는 ledger=`b914a28`, generation publish=`bdaf7ae`, update discard=`4d0d25b` 계열이다.
    해결 계약은 Framework routine 의미 owner를 유지하면서 각 canonical call을 `CALL_OCCURRENCE` 증거
    노드로 exact-once 저장하고 `HAS_CALL_OCCURRENCE`로 owner에 연결하는 것이다. aggregate CALLS는 계속
    파생 dependency view다. generation은 occurrence count/hash/outcome/reason closure를 닫고 unresolved가
    하나라도 있으면 `completed_with_findings`로 공개한다. REST/MCP search meta와 node detail/GWT packet은
    generation summary와 owner별 occurrence를 명시하되 source prose나 별도 의미 claim으로 승격하지 않는다.
    구현은 Parser camelCase range를 strict canonical 내부형으로 결합하고, workflow update의 유일 producer를
    캡처해 `CALL_OCCURRENCE`/`HAS_CALL_OCCURRENCE`, generation closure v2, quality, search meta, owner
    detail/GWT까지 연결했다. actual APAS는 23 source, 12,113 unique occurrence, routine owner 412,
    occurrence/owner edge 12,113/12,113이며 Neo4j/API/MCP 세 경계 SHA가 모두
    `2ab333f8c9e38e67a7c84d9c8d3bf8153c40b0b1bd1f1495040789aa62966d23`이다. 봉인 6/6,
    `completed_with_findings`, rollback residue 0, active pointer 불변을 확인했다. 최종 actual 전수 wall time은
    201.58초다. 이 검증에서 실제 Neo4j aggregation 정렬 오류, 구형 열린 AST label 단일-ID constraint,
    generation이 저장 정본 `slots` 대신 MCP `claims`를 읽던 결함도 함께 찾아 근본 수정했다. 상세 배선,
    최종 Analyzer 전체 1,134 tests(failure 0, skipped 6)와 prompt 16/16 byte identity도 통과했다. 상세 배선,
    formal red/green, 반대 설계, actual rollback 원장은
    `evidence/framework-call-occurrence-publish-audit-20260821.md`가 소유한다.
25q. 조사 중(C-062): Parser `CLanguageModule.prepareProjectContext()`가 source tree의 모든 C/H를 먼저 읽고
    5개 declaration regex와 5개 usage-shape regex 결과를 project-global predefined type으로 C parser semantic
    predicate에 등록한다. 유입은 declaration 계열 `99fdc5f`, usage 추론 `4633f5f`, 수동 오탐 제외
    `c68f31e`다. actual APAS 23 C source/header 0에서 Parser log는 428개를 type으로 등록했다. 현 구현과 같은
    패턴을 독립 재계산한 결과 declaration 후보 11, usage 후보 421, usage-only 416, 합집합 427이었다
    (Java/.NET regex의 한 후보 차이는 아직 미분해). 따라서 대부분은 include/header/compilation evidence가
    아니라 사용 모양에서 추측한 이름이며, 이 집합이 `IsTypedefName`/`IsCast` 등 grammar semantic predicate와
    최종 AST를 바꾼 뒤 parse/evidence를 `exact`로 봉인한다. 단순 보조 preview가 아니라 실제 syntax truth
    producer다. 하지만 regex만 제거하면 현재 결손인 450 include와 284 unique header/build closure를 숨긴 채
    parse population을 줄일 수 있으므로 아직 구현하지 않았다. grammar-owned declaration, include/build별
    visible type environment, 증거 없는 이름의 explicit unresolved 경계를 formal red와 actual 비교로 고정한
    뒤 독립 Parser slice로 교체한다. 상세는 `evidence/parser-c-type-name-ownership-audit-20260821.md`다.
25r. Parser producer 완료(C-061, commit/push `d690e51`): pinned C preprocessing grammar가
    include/if/ifdef/ifndef/elif/else/endif/undef와 constant-expression precedence, digraph, phase-2 line splice를
    소유한다. 단일 grammar/listener extraction에서 canonical import/conditional evidence와 legacy INCLUDE를
    파생하고, main C parser에는 같은 directive range를 줄 수 보존 공백으로 투영했다.
    `CAstListener.extractIncludes()`의 `startsWith/replaceFirst`와 conditional 자체 directive lexer/parser는
    제거했으며 fallback으로 남기지 않았다. formal red의 comment-separated/digraph/spliced include와 conditional은
    green이고, malformed `#include`가 조용히 complete가 되던 추가 red도 explicit unresolved로 green이다.
    actual APAS 23 source는 import fact 450 = legacy INCLUDE 450이며 target text와 시작/끝 line 전건 일치,
    conditional region 17, call 12,113, macro 183, diagnostics/recoveries 0이다. 450 import와 17 conditional region
    전부를 봉인 직접 판정 집합에 넣었다. import ledger SHA는
    `2e1a6bef42fa6d2954a516126d71473f101fc6da0f29c50cb27e4816dc2c8a0c`, 전체 fact ledger SHA는
    기존과 동일한 `0829b440ec777845e9a413a7292ad22c31083ba9fdd997a4b8eb3bf65dc9da34`다. Parser 전체
    184 tests(failure/error 0, skipped 10)를 통과했다. actual 보고서는
    `_runs/framework/parser-directive-grammar-20260821-r3.json`, SHA-256
    `7d4dd20b852f1662694805d8a42ebcee43e50a9588261269ee12402274293b36`이다. Analyzer import strict consumer,
    build-configured macro expansion/type environment(C-062/C-064), UCN identity(C-063)는 별도 미완료 slice다.
    상세는 `evidence/parser-c-directive-grammar-audit-20260821.md`다.
25s. Analyzer strict consumer 완료(C-061, commit/push 직전): `validate_common_evidence()`가 language-neutral
    `ImportEvidenceFact`로 canonical ID, directive/target exact range, directive/target kind, grammar rule, presence를
    strict validation한다. Parser 소유 targetKind를 Analyzer regex로 재해석하지 않는다. `framework_reader`는
    canonical fact와 legacy projection의 population/order/target/start-end physical line을 exact-once 결합하고
    mismatch/duplicate는 import scope 전에 fail closed한다. Parser가 import evidence를 `unsupported`/
    `not_applicable`로 명시한 다른 언어는 빈 canonical ID와 degraded reason을 남겨 canonical로 위장하지 않는다.
    formal fixture는 angle/quoted/phase-2-spliced/Unicode와 corrupt payload/projection을 포함하며 red의
    `import_facts` 부재를 green으로 전환했다. actual 23 source에서 import 450, unique 450, ledger SHA
    `2e1a6bef42fa6d2954a516126d71473f101fc6da0f29c50cb27e4816dc2c8a0c`가 Parser와 byte-identical하고,
    target kind angle 450, presence active 449/inactive 1, evidence status complete 450, sealed direct judgment
    450/450이다. inactive 1건은 `zapamcom10110.c`의 `<zngmmcap20020.h>`로 `#if 0` presence를 보존한다.
    Analyzer actual 보고서는 `_runs/framework/analyzer-directive-import-consumer-20260821-r1.json`, SHA-256
    `b44da9a357854b65c59ed83d8c6df0aaa1792ddb734791180eba072ab1728337`이다. 전체 회귀는
    1,228 passed, 7 skipped, 367 subtests다. DB/Neo4j write는 0회다. 다음 감사는 canonical import가
    `import_scopes -> REFERENCES -> graph/API/MCP`에서 어떻게 소비·소실되는지이며 아직 E2E 공개를 주장하지 않는다.
25t. 조사 중(C-066): `framework_reader`가 보존한 canonical import ID/range/target kind/presence/completeness를
    `import_scopes._imports_by_file()`가 `{file:[name]}`으로 축약한 뒤 C/Python regex와 Java 문자열 분해로
    다시 해석한다. `unsupported` Java/Python과 inactive/degraded occurrence도 scope authority에서 구분되지
    않으며 `References`/Neo4j/API/MCP에는 Parser fact ID와 presence가 남지 않는다. 유입은 최초 문자열
    resolver `6956d02c`, ontology profile 이동 `bc699ee2`다. actual APAS 450 import는 모두 angle이고 header
    target 0이라 현 C quoted-only regex가 450/450을 배제하되 actual edge 영향은 우연히 0이다. inactive 1건도
    angle이라 비발동했을 뿐 quoted 반례에서는 거짓 scope가 된다. Parser grammar/listener의 구조화
    module/path/member/alias/wildcard/local-system-relative fact, Analyzer의 language-neutral resolution outcome
    ledger, import occurrence provenance를 가진 derived REFERENCES 계약과 formal red를 고정한다. Java
    classpath/module path·Python import environment·C build include resolution이 없으면 basename/path 추측 없이
    explicit unresolved다. 상세는 `evidence/import-scope-provenance-audit-20260821.md`가 소유한다.
25u. 완료(C-066, Analyzer `15fcec8`, Parser `df78583`, 양쪽 push): Parser IR 1.1 structured import entry를 Analyzer가 strict 소비하고,
    `(import_id,entry_ordinal)` exact-once outcome ledger로 닫는다. Analyzer의 C/Python regex, Java 문자열 분해,
    ontology language profile, basename/suffix target 추측을 제거했다. authoritative environment binder가 없는
    active import는 `insufficient_import_binding_environment`, inactive는 `excluded/inactive_import`다.
    `IMPORT_OCCURRENCE` generation evidence node, closure v3, run quality, search/REST/MCP generation summary와
    source-file detail projection까지 배선했다. actual APAS는 fact/entry 450/450, resolved/unresolved/excluded
    0/449/1, sealed direct judgment 450/450, deterministic replay identical다. Neo4j `framework` rollback에서
    12,113 call+450 import를 함께 publish해 graph/API/MCP import population 450과 SHA-256
    `6f02336ced795b64178a97febf1f75665cd9ff86a49753a0bfc7a2ea1a67e81d` 일치, residue 0, active pointer
    불변을 확인했다. Analyzer pytest 1,247 passed/7 skipped/367 subtests, unittest 1,140 OK/6 skipped,
    Parser Maven 185 tests/failure·error 0/skipped 10이다. positive cross-module edge는 authoritative binder와
    target corpus가 없어 미검증이며 추측 edge를 만들지 않는다. 상세는 같은 C-066 evidence 원장과
    `_runs/framework/parser-structured-import-evidence-20260821-r2.json`,
    `_runs/framework/analyzer-structured-import-consumer-20260821-r3.json`(report SHA-256
    `e9b7e4fad9efbef396199520ce95782d28bc0169dd55a0b3dbcfbe86d5c439cc`)이 소유한다.
25v. 조사 갱신(C-062): project-global declaration/usage regex 10개뿐 아니라 `SymbolTable`의 POSIX/Windows/
    header 이름과 `NULL` predefined type도 source/config evidence 없이 같은 grammar predicate를 바꾼다.
    따라서 regex만 삭제하는 수정은 결함을 남긴다. WG14 scope/translation-unit 규칙, Clang compilation DB와
    preprocessor/parser 책임, 현행 ANTLR C semantic predicate를 대조해 `grammar-local declaration+lexical
    scope/order`, `configuration-specific authoritative trace`, `exact unresolved syntax decision`의 세 경계로
    계약을 확장했다. formal red는 cross-TU leak, comment/ordinary declarator, platform/NULL seed, local typedef/
    shadowing, unresolved coordinate exact-once를 고정한다. 상세는
    `evidence/parser-c-type-name-ownership-audit-20260821.md`다. Architect·DB write는 없다.
25w. C-062 formal red 고정: Parser `CTypeNameEnvironmentContractTest` 4건은 구현 전 4/4 실패했다.
    기대 symbol definition/lookup은 0건이고 IR은 1.1이었다. 같은 실행에서 project-global seed가 2→3→7개로
    변하며 `size_t`/`HANDLE`, 다른 TU typedef, multiline comment/ordinary declarator 이름까지 실제 등록됐다.
    red는 regex 삭제뿐 아니라 evidence-free `SymbolTable` seed 제거, grammar scope/order, unresolved lookup
    exact-once가 함께 필요함을 고정한다. 제품 구현·DB write는 아직 0회다.
25x. C-062 Parser producer 구현 중간 상태: project-global regex와 evidence-free platform/header/`NULL` seed를
    제거하고 grammar declarator·parameter·enumerator scope/order에서 IR 1.2 definition/lookup fact를 생산한다.
    focused는 4 failures → 2 → 1 → 0으로 전환됐고 declaration point, function parameter body shadow,
    prototype scope 종료, enum block shadow를 더한 8/8이 통과했다. 마지막 불일치는
    `size_t count_29ac;`의 `count_29ac`을 ANTLR recovery가 실제 type-name predicate로 질의한 fact였다.
    `parseStatus=partial`, ordinary+unresolved인 서로 다른 token decision을 숨기지 않는 것으로 계약을 정정했다.
    이어 유효한 grammar type specifier 뒤 ordinary declarator 반례를 red로 추가해 `grammar_context`와
    source definition을 분리했고 최신 focused 9/9가 통과했다. Parser 전체 회귀·actual APAS·Analyzer IR 1.2
    consumer는 아직 미검증이고 commit/push 전이다. symbol 진단의 graph/API/MCP projection은 비범위다.
    Architect·Catalog·Fabric 수정과 DB write는 0회다.
25y. 사용자 정정(부분 업로드 정상성): call/tag처럼 grammar role이 확정된 사용은 분석 대상 definition 유무에
    따라 내부 target 또는 제품 external로 결정론 분류한다. typedef 여부가 syntax를 바꾸는 경우는 target
    분류가 아니라 Parser AST JSON의 exact parse/evidence 진단이다. 다른 `.c`의 함수는 visible declaration+
    external linkage로 header 없이도 연결될 수 있지만 다른 `.c`의 typedef는 현재 translation unit에 자동
    유입되지 않는다. 추가 red의 유효한 `int member;` 과다 unresolved는 수정 후 9/9 green이다.
25z. 사용자 정정(C linkage): C의 external linkage는 Analyzer의 external target이 아니다. `a.c` definition과
    `a.h`/직접 declaration을 쓰는 `b.c`가 모두 분석 대상이고 linkage/type이 compatible하면 내부 CALL로
    연결해야 한다. header 형식은 필수가 아니다. static/typedef/duplicate/incompatible/declaration 부재는
    project-global 이름으로 결합하면 안 된다. 현 cross-TU call binder의 declaration→linkage→definition
    실제 배선은 C-067 후보로 추가 조사하며 Architect는 계속 보류한다.
25aa. 사용자 최종 정정(단순 소유권): C type-name 판정 결손은 Parser AST JSON의 exact fact와 parse/
    completeness 진단으로만 유지한다. Analyzer는 IR을 strict validation하고 syntax-dependent AST/owner가
    닫히지 않으면 좌표가 있는 run finding으로 격리하거나 graph mutation 전에 fail-closed한다. Neo4j의 별도
    label/node/relationship은 물론 기존 owner의 symbol-binding property ledger와 API/MCP projection도 만들지
    않는다. LLM은 AST/type/linkage/CALL/REFERENCE를 보정하지 않고 별도 natural-language semantic frame만
    채운다. 25y의 owner property 설계는 이 정정으로 폐기한다.
25ab. C-062 actual 반증: 현재 Parser working tree는 focused 10/10, 관련 계약 23/23, 전체 195 tests가
    통과했지만 accepted export와 APAS 23개 AST 본문을 비교하면 동일 0/23이다. FUNCTION 416→883,
    FUNCTION_CALL 12,113→11,680, ASSIGNMENT 2,038→1,941, IF 1,922→1,833, RETURN 1,444→1,383이다.
    가장 큰 `zapamcom10200.c`는 929→271 node이며 unknown parameter type이 있는 file-scope prototype에서
    function name 대신 parameter `itf`를 FUNCTION으로 만들고 뒤 실제 body를 연쇄 손실했다. 따라서
    project-global seed 삭제-only 구현은 미승인이고 commit하지 않는다. actual symbol facts는 5,949
    (definition 1,339 / lookup 4,610 / resolved 1,718 / unresolved 2,892), partial 22/exact 1,
    diagnostics 1,938/recoveries 5,414이며 replay ledger는 동일하다. 봉인 40은 전부 unresolved로 편향돼
    층화 직접 판정 미완이다. declaration-only grammar context의 구조 보존, expression 확정 내부 predicate
    제거, true block ambiguity exact 진단, 뒤 statement 비연쇄 손실을 새 red로 고정한 뒤 설계를 교체한다.
    blanket exact-parse unresolved filter는 진짜 missing-type fact 3건도 지워 폐기했다. Tree-sitter GLR은
    독립 반증 후보일 뿐 ANTLR 대체로 채택하지 않았다. DB/Architect 변경은 0회다.
25ac. 완료(C-062 Parser, commit/push `e28be6a`): block declaration/statement와 cast/type-expression을 ANTLR
    speculative parse로 비교하되 이름/regex가 아니라 candidate identifier 한 좌표만 type-name으로 시험한다.
    speculative stream의 token 객체 재사용이 원본 token index를 바꾸는 원인도 확인해 token copy로 교체했다.
    parameter/member/file-scope declaration-only, 진짜 ambiguity, expression negative predicate, cast, unknown
    return type, 반환형 생략 구형 C를 포함한 formal 18/18과 IR 포함 30/30, Parser 전체 203 tests
    (failure/error 0, skipped 10)가 통과했다. actual r5는 APAS 23 source에서 FUNCTION 416, FUNCTION_CALL
    12,113, ASSIGNMENT 2,038, IF 1,922, ELSE 763, RETURN 1,444로 accepted와 일치하고 diagnostics/recovery는
    0/0이다. accepted AST `children`은 20/23 exact, 나머지 3파일의 유일 delta는 원문 직접 대조한 TYPEDEF
    4개다. symbol 4,659 = definition 2,624 + lookup 2,035(resolved 755/unresolved 1,280), ledger
    `5d8b1f5ec3b77a01e705cc5b73e4be80cecf116a46027e2b945e0734d002b793`다. 층화 봉인 직접 판정과 Analyzer
    IR 1.2 consumer는 이 시점에 아직 미검증이었다. 최종 actual r7의 상설 baseline 비교는 23/23,
    비-TYPEDEF delta 0/typedef +4이고, 결과 열람 전 층화 봉인 symbol 40건은 주 에이전트 직접 판정
    40/40 exact였다. Parser 전체 변경은 `origin/main`에 push됐다. DB/Architect 변경은 0회다.
25ad. 완료(C-062 Analyzer product, commit `cd7110d`): IR 1.2 symbol definition/lookup의 canonical ID/range,
    exact payload, lexical definition reference/decision, predicate context, configured trace provenance와
    completeness unresolved 전수 집합을 graph mutation 전에 strict validation한다. declaration-only unresolved는
    AST JSON 진단으로만 소비 후 폐기하고, unresolved ordinary 또는 `ambiguous_*` syntax decision은 좌표와 함께
    fail-closed한다. NodeBuildResult/CodeNode symbol 진단 projection과 Neo4j/API/MCP 생산 배선은 0이다.
    actual r6 fixture 23파일은 symbol 4,659 = definition 2,624 + lookup 2,035(resolved 755/unresolved 1,280),
    syntax ambiguity 0, Parser/Analyzer ledger SHA 동일, 봉인 40/40 exact다. IR 1.2가 기존 import 450건을
    거부하던 downstream version 고정도 1.1/1.2 structured import 계약으로 교정했다. focused 107 passed/
    2 subtests, actual 1 passed, 권위 unittest 1,141 OK/6 skipped, 전체 pytest 1,258 passed/7 skipped/
    367 subtests, prompt 16/16이다. actual report는
    `_runs/framework/analyzer-c-type-name-consumer-20260821-r2.json`, SHA-256
    `e22050b73d773559e50dbc40748b1ae2d1e6e462a08fe10357c61eb46fbee1f2`다. Neo4j write와
    Architect/Catalog/Fabric 수정은 0회다. configured authoritative trace, 다른 언어, C-067 cross-TU binder,
    전체 LLM/graph 재분석 정확도는 미검증이다.
25ae. C-067 전수 감사 확정: Analyzer `call_classifier.py`는 모든 언어의 call target을
    `name -> CodeNode IDs`와 같은 file 우선/정렬상 첫 ID만으로 고른다. C call-site visible declaration,
    linkage, compatible function type, unique definition은 입력에 없고 Java receiver/overload와 Python lexical/
    dynamic binding도 없다. Parser C listener는 FUNCTION definition의 `static`을 내지만 Analyzer CodeNode가
    버리고, file-scope function prototype은 grammar로 감지한 직후 버린다. actual APAS 23 C source의
    definition 416개 중 static 380/non-static 36, call 12,113개다. name-only counterfactual의 internal
    637개는 same-file 543/cross-file non-static 94이며, cross-file 94는 3개 target에 51/34/9로 집중된다.
    uploaded header/build trace가 없어 이 94건의 visible declaration/type compatibility는 현재 증명되지 않았다.
    현재 C-064 blanket gate는 반대로 12,113건 전부를 `unresolved_preprocessing`으로 덮어 CALLS 0을 만든다.
    따라서 C-068로 source-view grammar occurrence/binding과 configuration-specific preprocessing authority를
    분리한다. stdlib JSON과 scan-agent glob이 origin proof 없이 canonical edge를 제외하는 C-069, Python
    대문자/initializer regex constructor 추측 C-070, C prototype/header definition 손실 C-071, Java
    receiver/overload/declaration identity 손실 C-072도 분리했다. 목표는 언어 adapter가 versioned exact binding
    evidence를 생산하고 Analyzer가 language/name 규칙 없이 canonical ID를 strict join하는 구조다. named call에
    proven corpus target이 없으면 제품 external이며 reason은 occurrence에 남기고, unresolved graph entity는
    만들지 않는다. 상세 배선·공식 근거·소비자·미검증은
    `evidence/cross-tu-call-binding-audit-20260821.md`가 소유한다. Architect/DB write는 0회다.
25af. C-067 계약/formal red 고정: Common Evidence IR 1.3은 type-name `symbol` 진단과 분리된
    `callable` declaration/definition fact, call별 binding 판정, 별도 `call_binding` completeness를 소유한다.
    Analyzer는 adapter/configuration/scope/opaque binding+compatibility key equality와 target 0/1/N만 처리하고
    언어·확장자·name list를 해석하지 않는다. Parser red 6건은 구현 전 6/6 실패했다: IR 1.2, callable 0,
    call binding 부재, header FUNCTION 폐기다. Analyzer 최초 red 6건은 `callable_facts` 계약 부재로
    6/6 error였고, 최종 Parser wire와 fail-closed 경계를 반영한 9건도 같은 원인으로 9/9 error다.
    red는 direct prototype→cross-TU definition, TU-scoped static, declaration 부재 external, incompatible type,
    supplied header definition, function-like macro, opaque key, duplicate ambiguous, source-file scope,
    unsupported adapter no-fallback을 고정한다. 제품 구현·DB/Architect 변경은 아직 0회다.
25ag. C-067 Parser producer 1차 구현/검증 후 diff 재감사 중(commit/push 전): C grammar/listener가 prototype·definition·call
    visibility/linkage를 생산하고 sealer가 IR 1.3 callable/call-binding/completeness와 opaque key를 봉인한다.
    header definition 확장자 폐기, member/named function pointer name-target 추측을 제거했고 macro는 exact macro
    fact를 가진 configuration-dependent로 보존한다. diff 감사에서 같은 typedef/tag 토큰을 cross-TU compatible
    type으로 과대 증명하는 C-073과 non-static inline external visibility가 dialect/configuration에 의존하는
    C-074를 추가 발견했다. builtin function type만 corpus compatibility, file-scope typedef/tag/unspecified
    parameter는 source identity를 포함한 source-file compatibility, block-local/지원 밖 declarator는 unavailable,
    non-static inline definition은 configuration-dependent로 고쳤다. formal 13/13, 관련 43/43, Parser 전체
    216 tests(failure/error 0, skipped 10), golden 5개 중 C만 의도 변경을 확인했다. actual r7은 23 source,
    FUNCTION/callable-definition 416/416, FUNCTION_CALL/call 12,113/12,113, binding 545 declaration-bound +
    11,492 external + 76 configuration-dependent이고 target join은 545 unique/0 zero/0 ambiguous, 전부 same-source다.
    callable 796의 compatibility scope는 source-file 780/corpus 16/unavailable 0이다. accepted AST 23/23,
    diagnostics/recovery 0/0, 봉인 40/40 exact다. 정본 report SHA-256은
    `d382ba59686ec5c7e50dd55dedffbc394a998ae9069fb9125be80bacc264d434`다. full suite의 Surefire native-stream
    warning은 Spring test logging을 dumpstream으로 기록한 기존 실행 노이즈이고 XML 회계 216/0/0/10과
    BUILD SUCCESS를 바꾸지 않는다. DB/Architect/Catalog/Fabric 변경은 0회다.
25ah. C-067 commit 전 자기반증에서 C-075/C-076을 추가 발견했다. 파라미터 이름만 뺀 token sequence는
    WG14 function type compatibility의 builtin specifier 동치, parameter qualifier 제거, array/function adjustment를
    canonicalize하지 못하며, inactive/conditional callable도 binder가 visible로 사용했다. formal을 17건으로
    확장·기존 기대를 엄격화한 구현 전 실행은 6 failures/0 errors다. 목표 계약은 grammar로 완전히 canonicalize한
    제한 builtin scalar만 exact compatibility, named/unspecified/complex type은 explicit unavailable, visible exact
    definition은 fact-direct, conditional callable은 candidate fact를 가진 configuration-dependent다. 따라서 25ag의
    216/full·actual r7·봉인 40은 1차 구현 증거로 보존하되 최종 합격이나 commit 근거로 사용하지 않는다.
25ai. C-075/C-076 교체 후 formal 19/19와 관련 49/49가 green이고 actual r10은 source 23, call 12,113,
    callable 796, binding은 declaration-bound 57(`direct_definition` 17 + `compatible_definition` 40),
    external 11,493, configuration-dependent 76, unsupported 487이다. join은 57 unique/zero 0/ambiguous 0,
    diagnostics/recovery 0/0, accepted AST 23/23이다. 그러나 봉인 직접 판정 착수 중 C-077을 발견했다.
    visible prototype의 named/지원 밖 parameter type 때문에 `unsupported`가 된 487 occurrence가 reason은
    보존하지만 그 판정 근거 callable fact ID를 `candidateFactIds`에서 버린다. formal assertion 추가 후
    `19 tests, 1 failure, 0 errors`로 red를 재현했다(expected 1, actual 0). unresolved graph entity를 만드는
    문제가 아니라 이미 존재하는 Parser callable fact와 call occurrence 사이 exact evidence closure 누락이다.
    r10 report SHA-256은 `0c613168db0f0c05633bf661dbdd371f5586ed286cf088c86959835d2bc399e4`이며
    C-077 교정 전 자료이므로 최종 합격/commit 근거로 사용하지 않는다. DB/Architect/Catalog/Fabric 변경은 0회다.
25aj. C-077은 새 graph entity 없이 기존 call occurrence→callable fact 참조만 복구했다. Parser C adapter는
    `unsupported/insufficient_callable_type_compatibility`에 visible declaration `nameRange`를 전달하고,
    공통 candidate invariant는 configuration-dependent 필수 후보, ambiguous 복수 callable 후보,
    unsupported 선택적 callable 후보, bound/external/dynamic 후보 0을 상태별로 검증한다. formal red 1건은
    교정 후 19/19 green이다. actual r11·전체 회귀·봉인 직접 판정은 아직 미검증이다.
25ak. actual r11/r12는 독립 실행에서 count·fact/call/binding/callable ledger와 봉인 40 selection이
    deterministic하게 동일했다. 새 actual checker는 unsupported 487건 전부에 대해 candidate 존재/중복 0,
    callable kind, 같은 terminal name, unavailable compatibility, active presence, call-site visibility를 전수
    검증해 green이다. 직접 판정 착수 후 C-078 검증 결함을 발견했다. 기존 `binding:declaration_bound` 층의
    5건이 전부 `direct_definition`이라 actual `compatible_definition` 40건을 직접 표본 0개로 놓쳤다.
    제품 합격을 선언하지 않고 resolution mode별 별도 층으로 표본을 다시 봉인한다.
25al. r13은 resolution mode별 층을 포함한 40건을 다시 봉인했고 compatible 5/direct 4를 포함해 주 에이전트
    source→callable/declaration/definition→binding 직접 판정 40/40 exact다. Parser 전체도 222 tests,
    failures/errors 0, skipped 10이다. 이어 diff 감사에서 합법적인 parenthesized function declarator의 재귀
    name token은 callable fact에 쓰지만 legacy FUNCTION AST 이름은 바깥 Identifier만 읽는 C-079 후보를
    발견해 callable→AST identity 반례를 formal red로 추가했다. 실행 결과 20 tests 중 1 failure이며 callable
    fact와 direct binding은 exact지만 첫 FUNCTION의 `name`만 absent였다. 재귀 grammar name token을 AST와
    callable의 단일 생산자로 쓰는 교정 후 20/20 green이다. final actual r14는 r13과 selection/fact/call-binding/
    callable ledger가 동일하고 봉인 40건 판정을 그대로 재현한다. report SHA-256은
    `29d7ace44058b9317dfa750baa735625552c0558c6def5c27226ca60233892ad`, two-pass wall 37,580ms다.
    최종 Parser 전체는 223 tests/failures 0/errors 0/skipped 10, golden 5언어 resource/generated hash 5/5
    동일이다. Parser commit `5dd64106540674a4aebfdb37ad897a375950d335`는 `origin/main`에 push됐고
    working tree는 clean이다. Analyzer consumer는 아직 전이다.
25am. Analyzer red fixture를 final Parser IR 1.3 wire와 일치시켰다. `resolutionMode`,
    `compatibilityScope`, declaration/definition의 `compatibilityStatus|Scope`, `definitionStatus`, 실제 존재하는
    `declarationFactId`, unsupported candidate reference를 모두 포함한다. compatible/direct를 분리하고 direct가
    같은 key의 다른 definition을 검색하지 않는 경계, target 0/1/N, source/configuration 격리, external aggregate
    node 0, unsupported candidate 보존, missing referenced fact와 missing binding fail-closed를 고정했다. 권위
    unittest는 9 tests 모두 `classify_call_sites() got an unexpected keyword argument 'callable_facts'`로 error다.
    이는 제품에 Parser-owned callable/binding 소비 경계가 아직 없다는 formal red이며 구현 성공으로 해석하지 않는다.
25an. 사용자 아키텍처 정정과 source 재감사로 C-080을 확정했다. Parser `5dd6410`의 별도 language adapter는
    실제로 존재하지 않고 `CAstListener.callableEvidenceExtraction()`이 `bindCall()`을 직접 실행한다. 같은 listener가
    `effectiveTargetScope()`, `functionCompatibility()`, `canonicalBuiltinType()`으로 C linkage/type compatibility와
    `declaration_bound|external|dynamic|configuration_dependent|unsupported`를 최종 결정한 뒤 `CLanguageModule`이
    결과를 sealer에 직결한다. r14 bound 57건은 direct 17+compatible 40이며 전부 same translation unit,
    cross-TU positive 0이라 범용 cross-TU 정확도를 증명하지 않는다. Analyzer strict consumer를 이 wire에 맞추면
    잘못된 ownership을 고착하므로 IR 1.3은 역사 evidence로만 보존하고 canonical consumer 계약에서 폐기한다.
    상세 원인 사슬·공식 근거·rollback·미검증은
    `evidence/call-binding-ownership-correction-20260821.md`가 소유한다.
25ao. forward 목표는 두 서비스 그대로 `Parser structural evidence → Analyzer internal language semantic adapter
    → language-neutral resolution core`다. Parser IR 2.0은 explicit language/frontend identity, call/callee/receiver/
    argument range, callable declaration/definition, lexical scope/order, typed specifier/declarator/parameter/attribute
    syntax와 presence만 생산하고 final binding/status/key/completeness를 제거한다. Analyzer가 C/Java/Python/SQL
    adapter와 authoritative provider capability를 소유하며 common core는 target 0/1/N과 exact-once ledger만
    검증한다. product external은 부분 업로드의 정상 경계이고 unresolved graph entity는 만들지 않는다.
    `ontology_roles.yaml.roles`는 primary graph materialization 분류로 유지하고 call binding에는 사용하지 않는다.
    SQL `EXIT`의 control/statement 중복은 현재 DBMS CodeNode와 별도 `exit_loop` effect를 잃지는 않지만
    `role_of()` 첫 항목 순서에 의존하므로 독립 facet 계약에서 순서 의존을 제거한다.
25ap. 현재 Analyzer working tree의 `pipeline/steps/step2_load_ast/evidence_ir.py`는 이전 IR 1.3 strict decoder
    시도의 보존된 미완성 diff다. 삭제한 `_KINDS` 참조, callable 수집/version 전달/생성자 결합이 닫히지 않아
    제품 합격 상태가 아니다. 이 diff를 잃거나 IR 1.3 consumer로 완성하지 않고 IR 2.0 structural decoder로
    forward 교체한다. Parser tree는 clean이고 Analyzer 외 Catalog/Fabric/Architect WIP와 DB는 무변경이다.
25aq. 사용자 우선순위 변경으로 C-080 구현은 폐기하지 않고 TODO/contract/red 대상으로 봉인한 채 Strategy A
    실제 데이터 검증을 먼저 수행한다. 기존 shopmall source 11파일은 sealed hash 11/11 일치하고, 20함수
    모집단은 77 actual prompt record/519 exact row다. 1단계는 결과 열람 전 complex=`member_authenticate`,
    medium=`apply_point_balance`, state-transition=`next_ship_status`, negative-control=`db_commit`의 4함수로
    고정했다. 합계는 15 record/104 row/18 gold cases이고 selected ledger SHA-256은
    `75292142cb95b9980b1d3064857a517cbfbfcb4dd43ab4fbebe36a224ee4182e`다.
25ar. Strategy A 역사 script는 tracked `strategy-a-v3-results.jsonl`을 먼저 truncate하고 3 repeats/16 workers/
    231호출, meaning maxLength 500/1000, output 4096 token을 고정하므로 그대로 실행하지 않는다. 원본과 역사
    결과 hash는 불변으로 보존한다. 새 runner는 spec 131 `_runs/framework/strategy-a`만 쓰고 1회 15호출의
    actual system/user/schema/raw/parsed/usage/finish reason을 보존한다. 자동 계약·104행 exact-once·18 gold
    직접 의미 판정을 모두 통과해야 20함수/77호출로 확대한다. 이전 모델 3회와 현재 1회는 동일 입력 정확도와
    역사 변동 범위를 비교하되 현재 반복 안정성은 미검증으로 분리한다. 비교 결과는
    `D:\work\robo\docs\작업보고내용`에 2026-08-21 쉬운 보고서로 작성한다.
26. 검증: 자동 회귀→결정론 전수 회계→봉인 직접 의미 판정→실제 APAS 순으로 계속 수행한다.
27. 실행: 임시 DB를 만들지 않고 `framework|dbms`만 재사용하며 종료 시 임시 자원을 정리한다.
