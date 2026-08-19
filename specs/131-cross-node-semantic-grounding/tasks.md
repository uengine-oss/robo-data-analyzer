# spec 131 현재 Tasks

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
- [x] T131-37a module/summary GPU 호출 전 static named jump target preflight를 제품·상설 preflight에
  같은 함수로 구현하고 missing/duplicate/blank target 반대 fixture와 resolved fixture를 검증
- [x] T131-37b 공통 deterministic Rule 경계에서 rejected/unavailable/회계 잔차만 fatal로 유지하고
  unverified를 scoped finding으로 격리. DBMS 이중 채점을 제거하고 FW/DBMS trusted+finding 회계 검증
- [x] T131-37c Rule audit에 routine row→실제 graph owner를 보존하고 judge_run의 obligation/trusted/
  finding/unavailable 잔차와 graph 기대 trusted row를 전수 대조
- [ ] T131-38 채택 구조의 정상·실패·경계·중복·순서·취소/종료 회귀 및 전체 자동 테스트
- [ ] T131-39 재사용 `framework` DB와 `_runs/framework`의 실제 APAS 전체 cold run,
  judge_run+graph, 봉인 30~40 의미 직접 판정
- [x] T131-39a actual graph에서 발견된 긴 Rule 조건 EXAMPLE 12건 누락을 과거 LLM-era 500자 상한의
  잔재로 확정하고, 결정론 Rule truth 무손실 계약·반대 테스트·상설 judge 재판정으로 누락 0 검증
- [x] T131-39b final summary finding 4건과 봉인 FUNCTION 10건의 공통 원인을 3-slot 과압축과
  no-op/unused/output-only 충분성 계약 부재로 고정. routine summary를 최대 5개 target-owned 역할로
  분해하고 population/projection/prompt 반대 fixture 및 전체 회귀 검증
- [ ] T131-39c 새 APAS full cold에서 FUNCTION 10을 동일 봉인 ID로 재판정하고 Rule/Example/Table/Column
  30건과 합쳐 40건 직접 의미 판정 완료. omission/fabrication/contradiction과 needs_context를 분리 기록
- [ ] T131-40 작은 실제 MCP 질의→Architect Opus 5 GWT, Analyzer optional 두 경로 검증
- [ ] T131-41 2026-08-18 작업보고·HANDOFF·GPU server 문서 갱신, diff 감사, selective commit/push, 임시 자원 정리
