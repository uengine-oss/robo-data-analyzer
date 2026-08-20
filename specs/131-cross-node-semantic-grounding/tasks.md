# spec 131 현재 Tasks

## 2026-08-20 문서 메모리 정리

- [x] D131-01 Codex 단일 진입점과 원문 손상 경고 작성
- [x] D131-02 절대규칙·현재상태·역사·evidence의 단일 소유권 분리
- [x] D131-03 handoff 00/NEW-SESSION/HANDOFF의 낡은 현재 진입점 표시 제거
- [x] D131-04 문서 참조·중복 현재상태·diff 최종 감사
- [x] D131-04a 정본 handoff·raw 실험·루트 `_runs`를 spec 131 아래로 이동하고
  `docs/핸드오프`에는 링크 README만 유지
- [x] D131-05 상황·배경·확정/미확정·권장 방향 설명 후 사용자 피드백 반영

사용자가 2026-08-20 전체 목표와 방향을 승인해 이 단계는 종료됐다.

## 2026-08-20 의미 무손실 전수조사

- [x] A131-01 Analyzer 전체 상한·절단·Top-N·sampling·skip/fallback·중간 LLM·summary-of-summary·
  timeout/retry/cache/batch/concurrency 생산자·소비자 inventory 완성
- [x] A131-02 각 후보의 source→AST→actual prompt→raw/parsed→validator→Neo4j→API/MCP 배선,
  실제 값, 유입 commit 확정
- [x] A131-03 실제 의미 손실/안전한 실행 분할/표시 preview/검색 파생 제한/명시적 degraded 분류와
  원인·영향도 보고
- [ ] A131-04 계약·실패/경계/과대 입력 red test 고정 후 책임 슬라이스별 근본 구현·검증·commit/push
  - [x] A131-04a F-001/C-001/C-002 및 동일 canonical diagnostic leaf의 문자·항목 상한 제거,
    red→green과 step7/schema/API 집중 회귀 검증
  - [x] A131-04b FUNCTION role taxonomy와 evidence-anchor obligation slot 분리, exact source/evidence
    closure call 및 canonical ID exact-once merge
    - [x] A131-04b-FW 공통 planner evidence-anchor 귀속, Framework token-derived exact closure 분할,
      source-order projection, length/deadline/repetition exact-once merge
    - [x] A131-04b-DBMS 기존 node batch envelope를 effective AST CodeNode별 exact
      source/evidence fragment runner로 교체
  - [x] A131-04c 중간 prose 권위 입력·source literal 비가역 projection·SCC dependency 누락 제거
    - [x] A131-04c-EXT linked external chunk 8건/6000자 canonical 선택 상한 제거,
      chunk별 exact evidence obligation과 downstream token 분할 회계 연결
    - [x] A131-04c-PROSE module/component·agent 중간 prose 권위 입력 제거
      - [x] C-010 Framework module pre-analysis producer·prompt·23회 호출·module_intents 배선 제거
      - [x] C-009c step8 module/component 후속 LLM·prompt·response model 제거, canonical member slot
        source-order exact-once 결정론 projection과 display/stereotype explicit degraded 계약 고정
      - [x] C-016 Agent context compaction과 `ScanResult.codebase_notes` downstream LLM 권위 배선 제거
    - [x] A131-04c-SOURCE source literal 비가역 projection producer·호출·replay switch 제거,
      Framework/DBMS prompt literal/escape exact-text 계약과 반대 fixture 고정
    - [x] A131-04c-SCC suppressed `(caller, callee)`를 canonical CALL unavailable evidence로
      exact-once 물질화하고 prompt·normalizer·missing_context까지 연결
  - [x] A131-04d source/AST/diagnostic closure와 completed generation 원자 publish/consumer guard
    - [x] A131-04d-CLOSURE parser-supported source↔AST↔EXACT diagnostic SHA/status 전수 one-to-one
      회계, strict UTF-8, malformed/unknown AST fail-closed, Neo4j mutation 전 immutable closure artifact
    - [x] A131-04d-GENERATION same-DB completed generation 원자 publish와 API/MCP/search consumer guard
  - [ ] A131-04e 남은 구조 closure(C-004/C-017~021/C-024/C-028/C-031) 책임 슬라이스별 해결
- [ ] A131-05 자동 회귀·결정론 전수 회계·봉인 직접 의미 판정 3층 완료

## 보존된 기준선

- [x] C131-01 봉인40과 source/prompt/output/stored 직접 판정 기준선 보존
- [x] C131-02 배치 상한과 source 의미 상한 분리, full node ID 의미 identity, 입력 충분성 상태 보존
- [x] C131-03 기존 계약 기준 `39/40=97.5%`, fabrication 0, contradiction 0 기록 보존
- [x] C131-04 동적 의미 프레임 방향과 complete-source Architect 기본 전달 폐기를 문서 정본에 고정

## 동적 의미 프레임 전환

- [x] T131-19 Framework/DBMS 전체 자연어 의미 producer→schema→normalizer→validator→storage→
  graph/API/MCP→Architect consumer inventory와 WIP keep/adapt/remove 단일 영향표 작성·후속 재사용
- [x] T131-20 공통 `StructuralProfile`, frame family, stable slot/evidence ID, `semantic-frame/v1` 계약 고정
- [x] T131-21 shallow dynamic schema와 stable-prefix prompt를 구현하고 schema/token/repetition/KV 관측 기준 고정
- [x] T131-22 SUMMARY·RULE·EXAMPLE·CALL·TABLE·COLUMN·MODULE·logical name을 frame-first 생산으로 전환
- [x] T131-23 legacy summary/description/nl을 frame projection으로 전환하고 자연어 이중 생산자 제거
- [x] T131-24 sufficiency/grounding/completeness/ownership validator와 population inventory 전환
- [x] T131-25 graph/storage/API/MCP semantic-frame 정본과 deduplicated Architect projection 구현
- [x] T131-26 Architect complete-source 기본 전달 WIP 제거·적응 및 spec055 Simplified/Detailed 소비 동기화
- [x] T131-27 Framework/DBMS 범용·반대 fixture, prompt/schema 성능, 전체 회귀 검증
- [x] T131-28 새 격리 DB/output의 최소 actual Analyzer 실행과 봉인 직접 의미 판정
- [x] T131-29 Analyzer 합격 뒤 Architect 두 실제 경로 GWT/evidence ref 원문 대조
- [x] T131-30 작업보고·HANDOFF 결과 교체, 임시 실행물 정리, diff 감사, 요청 범위 commit/push

## 불변

- A Python 관계식은 KEEP이며 수정·재실험하지 않는다.
- 기존 dirty WIP·보존 DB·logs를 reset/checkout/clean/overwrite하지 않는다.
- Architect 정상 생성 packet에 complete source나 새 source 조각을 넣지 않는다.
- `accept/apply`를 호출하지 않는다.

## 2026-08-18 APAS 재개

- [x] T131-31 최신 parser로 APAS 23파일 AST를 재생성하고 old/new node·좌표·구조 vocabulary delta와
  active named GOTO→LABEL exact 해소를 전수 감사. 2026-08-18 완료 표시는 저장 AST가 LABEL을 누락한
  사실을 놓친 오판이므로 재개. 2026-08-19 source hash mismatch 0, 기존 stable node 삭제/변경 0,
  신규 LABEL 67 + postfix ASSIGNMENT 2, GOTO 5/5 exact 해소, parser 154/0/0과 Analyzer preflight 통과
- [x] T131-32 중단 actual run에서 summary 741요청·5,293 slot 중 CALL.PURPOSE 4,349개와
  deadline/repetition retry 0을 실측해 원인을 owner 생산자로 확정
- [x] T131-33 Framework CALL occurrence를 evidence-only로 되돌리고 DBMS CALL CodeNode만
  CALL.PURPOSE를 소유하도록 planner와 반대 fixture 수정
- [x] T131-34 canonical slot ID를 request-local compact wire ID로 바꾸되 normalizer가 exact ID로
  복원하고 1/7/8/9/24/65 경계 누락·추가·중복을 거부
- [x] T131-35 정상 summary의 고정 8-slot microbatch를 제거하고 당시 target-owned 최대 3 slot invariant
  적용. T131-39b에서 실제 흐름 완전성 결함을 근거로 최대 5개 구조 적용 역할로 대체
- [x] T131-36 FW/DBMS producer·consumer·호출/batch 영향표와 과거 탈락안·server 공식 research 기록
- [x] T131-36a Rule 행당 단일 `RULE.BUSINESS_MEANING`, 결정론 structure 무손실, digest-scoped private
  wire key 격리 계약을 producer/normalizer/projection/graph/MCP/Architect 양끝에 구현
- [x] T131-36b actual APAS 오행 역추적으로 확인한 별도 wire↔row 조인을 제거하고, 직접 의미 근거가
  있는 행만 `output_wire_id + focus condition + direct/source`를 소유하게 한다. 구조 전용 행은 LLM
  slot 없이 결정론 Rule로 물질화한다. target-wide 보조 목록도 direct CALL/DATA_OBJECT/exact matched
  key 기반 row-local context로 교체하고 의미 없는 이름·sentinel 확장 번역을 막으며 Framework/DBMS
  producer·normalizer·replay·checker를 정합. 구현·최종 replay는 완료했으나 모델이 지시를 위반해
  조건 극성 모순과 이름 확장 창작이 남았으므로 이 후보 자체는 기각
- [x] T131-36c Rule prose 소비자 0과 후단 semantic validation 공백을 전수 감사하고,
  `RULE.BUSINESS_MEANING` provider/schema/normalizer/batch를 제거한다. 모든 required row를 exact
  cond/then Rule로 결정론 물질화하고 Example 및 graph/MCP/Architect evidence refs 무손실을 반증.
  `rule-structures/deterministic-rule-v1` 영구 감사 산출물, graph 비어 있는 `nl`, 구조 렌더링,
  Architect 소비자까지 닫았고 collision-safe audit 파일명까지 포함해 Analyzer 1,029 tests/5 skipped,
  prompt 23/23,
  Architect 집중 62 및 전체 1,383 tests/2 skipped로 검증
- [ ] T131-37 APAS 봉인 slice에서 concern/scheduling 후보를 calls/tokens/cache/latency/accuracy로 비교·채택
- [ ] T131-37d 2026-08-20 교체된 향상 모델의 server identity/structured-output 계약을 기록하고,
  구 모델 run은 역사 기준선으로 분리한다. 새 모델 bounded replay에서 남은 600자 summary 경계를
  범용 계약으로 해결한 뒤 final cold의 calls/tokens/wall/의미를 새 기준으로 판정
- [x] T131-37a module/summary GPU 호출 전 static named jump target preflight를 제품·상설 preflight에
  같은 함수로 구현하고 missing/duplicate/blank target 반대 fixture와 resolved fixture를 검증
- [x] T131-37b 공통 deterministic Rule 경계에서 rejected/unavailable/회계 잔차만 fatal로 유지하고
  unverified를 scoped finding으로 격리. DBMS 이중 채점을 제거하고 FW/DBMS trusted+finding 회계 검증
- [x] T131-37c Rule audit에 routine row→실제 graph owner를 보존하고 judge_run의 obligation/trusted/
  finding/unavailable 잔차와 graph 기대 trusted row를 전수 대조
- [ ] T131-38 채택 구조의 정상·실패·경계·중복·순서·취소/종료 회귀 및 전체 자동 테스트
- [ ] T131-39 재사용 `framework` DB와 이 spec의 `_runs/framework`의 실제 APAS 전체 cold run,
  judge_run+graph, 봉인 30~40 의미 직접 판정
- [x] T131-39a actual graph에서 발견된 긴 Rule 조건 EXAMPLE 12건 누락을 과거 LLM-era 500자 상한의
  잔재로 확정하고, 결정론 Rule truth 무손실 계약·반대 테스트·상설 judge 재판정으로 누락 0 검증
- [x] T131-39b final summary finding 4건과 봉인 FUNCTION 10건의 공통 원인을 3-slot 과압축과
  no-op/unused/output-only 충분성 계약 부재로 고정. routine summary를 최대 5개 target-owned 역할로
  분해하고 population/projection/prompt 반대 fixture 및 전체 회귀 검증
- [ ] T131-39c 새 APAS full cold에서 FUNCTION 10을 동일 봉인 ID로 재판정하고 Rule/Example/Table/Column
  30건과 합쳐 40건 직접 의미 판정 완료. omission/fabrication/contradiction과 needs_context를 분리 기록
- [ ] T131-40 **보류** — Architect 사용자 승인 전 수정·실행 없음. 이후 작은 실제 MCP 질의→
  Architect Opus 5 GWT, Analyzer optional 두 경로 검증
- [ ] T131-41 2026-08-18 작업보고·HANDOFF·GPU server 문서 갱신, diff 감사, selective commit/push, 임시 자원 정리
