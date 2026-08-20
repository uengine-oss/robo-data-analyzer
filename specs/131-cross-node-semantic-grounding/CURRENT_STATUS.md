# Analyzer 의미 무손실 감사 현재상태

**Updated**: 2026-08-20
**Status**: A131-04e 진행 중 — source closure와 completed generation 원자 publish 완료, 남은 구조 손실 경로 조사
**Product-code baseline HEAD**: `c216e674cbac43386f9826632097ca2661395d7d`

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
- Analyzer latest pushed slice HEAD `a64b5ab15da138fa7f7b2d588407d96f51488cbf`; 제품 코드 기준선은
  `c216e674cbac43386f9826632097ca2661395d7d`, 재개 시 dirty 0이었다.
- Parser HEAD `4b849f14575b6830b82ae5ee980658100c4d8692`, dirty 0.
- Catalog HEAD `1f1aaa608a3d7ef8b01ce15c3df962cf0f1cfefb`, 기존 dirty 12개가 있다.
- Fabric HEAD `83c9604e2d6ec3f1ececa47d2e2264a74257843d`, 기존 dirty 7개가 있다.
- Architect HEAD `7b771ac2e7338d03ac8238db5c307d97080232c1`, 기존 dirty 4개가 있다.
  Catalog·Fabric·Architect의 기존 WIP는 읽기만 하고 수정·stage하지 않는다.
- 현재 세션에서 DB 생성·삭제·분석 실행·서비스 변경은 0회다. `test` DB에서 generation publish
  transaction 통합 검증 1회를 수행했지만 명시 transaction을 전부 rollback했고 해당 generation
  node/property 잔존은 0이다. composite constraint는 `EXPLAIN`만 수행해 schema 변경도 0이다.

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
- 상태: 원인 확정, 수정 전. 숫자 증설이 아닌 obligation-derived 분할·완전성 회계가 필요하다.

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
22. 검증: 자동 회귀→결정론 전수 회계→봉인 의미 표본→실제 APAS 순으로 수행한다.
23. 실행: 임시 DB를 만들지 않고 `framework|dbms`만 재사용하며 종료 시 임시 자원을 정리한다.
