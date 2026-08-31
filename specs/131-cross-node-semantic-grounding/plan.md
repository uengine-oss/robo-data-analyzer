# Analyzer 의미 무손실 구현 계획

**Updated**: 2026-09-01
**Requirements**: `spec.md`
**Current state**: `CURRENT_STATUS.md`

## 1. 실행 원칙

1. Neo4j 인증과 허용 DB online 상태를 먼저 확인한다. 실패하면 DB·분석 작업을 중단한다.
2. 입력·설정·계약과 정답지·baseline의 전체 파일 tree hash를 한 번 봉인한다.
3. 기존 actual과 source/data flow 같은 값싼 증거로 원인을 닫는다.
4. 닫히지 않을 때만 영향 좌표→집중 계측→최소 재현→history/bisect→외부 1차 근거 순으로 깊어진다.
5. 원인이 확정된 관련 수정만 한 책임 slice로 묶는다.
6. formal red와 작은 실제 데이터에서 통과한 뒤 점진 확대한다.
7. 작은 실제 생성 검증은 봉인한 SKT 2파일 1,503줄로 닫는다. 2026-09-01 사용자 결정에 따라 GPU와
   약 5만 LOC 제품 규모 성능 gate는 현재 완료 범위에서 제외하며 실행하지 않은 성능을 합격으로 말하지 않는다.

## 2. 구현 순서

### P1. Parser→Analyzer 경계

- Parser fact kind, source range, tree/order/presence, version 계약을 닫는다.
- source↔AST required/produced exact barrier를 둔다.
- Analyzer 언어 adapter와 중립 resolution core 사이의 candidate wire를 닫는다.
- C·Java·Python·SQL의 shadowing·overload·linkage·dynamic·partial upload 반례를 고정한다.

### P2. 호출·데이터·외부 연결

- 모든 call occurrence를 한 종료 상태로 분류한다.
- 모든 import binding entry를 Parser 사실과 명시적인 선택 source/module 환경에 결합해
  `internal|external|excluded|unresolved` 중 하나로 exact-once 종결한다. 환경 부재와 완전한 빈 환경을
  구분하고 basename·suffix 일치만으로 internal을 만들지 않는다.
- 사용자 internal pair만 CALLS로 만들고 occurrence와 pair를 분리한다.
- 표준·library·framework 제공 함수 edge를 제외한다.
- 직접 table/column access와 external exact key만 소유 함수에 결속한다.

### P3. 최소 의미 입력

- 함수 원문은 한 번 번호 붙이고 Parser 범위로 자른다.
- source, 구조 지도, symbol, table, callee outcome, 검증 project contract를 비중복 section으로 조립한다.
- 모델에 내부 ID/hash/range/status/evidence/question/confidence가 노출되지 않게 하고, 구조 사실은
  request-local `facts`에 한 번만 두며 출력 칸은 `fact_refs`로 참조하는 정적 계약을 둔다.
- import occurrence/fact/module ID는 결정적 exact-once ledger 안에만 유지하며 LLM packet과 제품 그래프에
  전달하지 않는다.
- Summary는 whole-target `summary`와 `stereotype`만 생성한다. 같은 source/fact를 잘게 나눈 detail
  meaning map, request digest key와 fact-reference map은 입력·출력에서 제거하고 내부에는 whole-summary
  의무 하나만 유지한다.
- Rule·FunctionTableUsage·DBMS batch는 순서만으로 값 교환을 탐지할 수 없으므로 exact response 결합에
  필요한 최소 순번 key만 유지한다. key는 `C1/E1/R1/FT1/N1`처럼 짧고 연속적이며 line/range·stable ID·
  hash를 포함하지 않는다. response schema의 설명용 title·description·동적 fingerprint도 제거한다.
- TableDescription은 FunctionTableUsage LLM 문장을 재입력하지 않고 DDL/source와 결정론 직접 사용
  사실만 소비한다. 빈 optional 값과 다른 필드에서 완전히 복원되는 값은 직렬화하지 않는다.
- Scan/residual pattern과 external-format rule은 모델이 의미 body만 제안하고 Analyzer가 검증용 내부
  identity를 부여한다. residual 교차 참조만 요청 내 `P1..Pn`을 사용하며 external key capture는 `key`로
  고정한다. model-visible SKILL 계약에서는 내부 rule ID를 제거한다.
- 모든 이름은 producer→consumer→storage→search에서 같은 개념을 표현하도록 통일한다.

### P4. LLM 실행 경계

- 허용 callsite inventory를 fail-closed로 유지한다.
- family 공통 prefix byte identity와 exact response schema를 유지한다.
- Summary·Table·Rule을 비차단 병렬 실행하고 첫 실제 요청으로 cache를 warm-up한다.
- dummy, judge/repair, content retry, 같은 concern 재질문, hidden tool loop를 제거한다.
- Agent 초기 message와 필수 탐색 tool 사이의 sample 통계를 단일 소유로 만들고, verify tool은 현재 판정과
  다음 행동만 보낸다. 동일 page의 cached report와 성공 page의 실패 교정 설명을 반복 전송하지 않는다.
- filesystem/sample/mining/code-node page header는 closure용 total/next만 남기고 current/returned 복창을
  제거한다. prompt의 도구 목록 표는 tool schema와 중복되므로 제거하되 작업 순서와 pagination 의무는
  system prompt에 유지한다.
- 실제 provider tool wire를 직렬화해 description과 parameter schema를 별도 계측한다. description은
  purpose·보안/선택 범위·page closure·production verify 의미로 줄이고, system workflow의 단계·예시·
  운영 팁과 schema가 이미 소유한 default/signature 복창을 제거한다. numbered source와 match text는
  후보 판정 근거이므로 절감량보다 판단 손실이 크면 유지한다.
- production 11종은 prompt renderer, provider response schema, Agent tool serializer의 실제 결과를
  하나의 fail-closed inventory로 닫는다. contract key/section만 금지값을 검사하고 원문 값과 상대
  path/file/line selector는 분리한다. 유료 검증 전에는 봉인된 동일 source의 기존 usage ledger와 현재
  call graph로 호출 수·토큰·공식 단가·보수적 비용 상한을 확정하고 실행별 budget을 별도로 낮춘다.
- 봉인 1,503줄의 유료 재검증은 Framework Summary 17, TableUsage 4, Rule 17, TableDescription 2의
  40호출만 허용한다. 2026-08-31 공식 `gpt-5.4-mini` 단가(input $0.75/M, cached input $0.075/M,
  output $4.50/M)와 기존 실제 $0.360159를 기준으로 25% 변동 여유는 $0.450199다. 실행 시
  `ROBO_BUDGET_RUN_USD=0.50`을 사용하되 post-charge 누적 차단을 provider의 절대 청구 상한으로
  표현하지 않는다. 비용 확인 전 유료 호출은 0회다.
- 같은 run의 SearchSegment OpenAI embedding도 `text-embedding-3-small` 공식 $0.02/M input 단가로
  성공 HTTP response별 usage를 기록하고 동일 budget에 누적한다. 생성 40호출과 embedding request 수를
  섞어 logical generation count를 바꾸지 않되 총 provider token·비용에서는 어느 쪽도 누락하지 않는다.
  실행 환경에서 provider/model을 `openai`/`text-embedding-3-small`로 명시하고, 유료 embedding의
  HTTP 실패를 재전송·분할로 우회하지 않는다.
- provider-managed `gpt-5.4-mini-2026-03-17`의 hard limit과 disclosure는
  `llm/runtime_capabilities/gpt54_mini_openai_2026_03_17.yaml` 및 그 SHA-256을 실행 환경에 명시해
  현재 config와 함께 봉인한다. 빈 ref/hash나 모델 불일치 상태로 실행하지 않는다.
- 첫 승인 명령이 provider 호출 전 adapter schema binding에서 실패하면 같은 run을 실제 검증으로
  승격하지 않는다. canonical/provider semantic schema는 title-free로 유지하고, adapter copy의 고정
  protocol name 하나만 허용한다. 실패 증거와 비용 0을 봉인한 뒤 자동 재실행하지 않고 새 사용자 확인을
  받는다.
- hard provider limit을 실제로 넘는 함수에만 exact partition+synthesis를 적용한다.

### P5. Strategy B

- Analyzer가 structural row와 atom을 exact-once 생성한다.
- Rule 호출 한 번에서 직접 effect atom의 업무 의미와 row disposition만 받는다. 구조 condition 의미는
  모델에게 다시 생성시키지 않는다.
- Analyzer가 충분한 business row만 구조 condition과 restricted Python 업무 effect를 결합해 Rule로 공개한다.
- Example은 승인 Rule에서 0-call로 생성한다.
- published/excluded/unverified/unavailable 행 합집합이 후보 집합과 정확히 같은지 검사한다.

### P6. Graph 공개

- 구조·의미 stage별 property owner를 고정한다.
- building generation의 canonical identity/property/endpoint hash를 다시 읽어 대조한다.
- 모든 검증 뒤 active pointer를 원자 교체한다.
- unresolved/candidate/occurrence/finding은 제품 node로 공개하지 않는다.

### P7. 저장소·문서

- `specs`에는 README, spec, plan, tasks, CURRENT_STATUS, v2 contracts만 남긴다.
- audit·archive·run·experiment를 각 전용 위치로 이관한다.
- 소비처가 없는 script와 호환 껍데기는 보호 WIP를 제외하고 제거한다.
- 승인된 물리 구조는 `pipeline/analysis_ir`, `pipeline/semantic_tasks/{summary,table,rule}`, `cli`,
  `tools/{verify,admin}`이다. 먼저 파일별 이동·import·경로 리터럴·CI·runbook 소비자표를 만들고
  dependency 역순으로 `git mv`한다. 옛 import path를 살리는 shim은 만들지 않는다.
- 제품 밖 실행 자산은 `D:\work\robo\_playground\analyzer\{profiles,runs,experiments,archive}`가
  소유한다. source·AST·gold를 복사하지 않고 profile과 봉인 hash로 참조하며 제품/spec 경로로 run output을
  쓰는 설정은 preflight에서 거부한다.
- constitution과 최종 HTML을 실제 코드·필드·호출 순서와 동기화한다.

## 3. 검증 확대

1. 변경 함수의 unit/formal red
2. 영향받은 작은 실제 SKT 파일·함수 좌표
3. 같은 언어·같은 concern의 확대 slice
4. 전체 정적 회귀와 prompt hash
5. 봉인한 SKT 2파일 1,503줄 실제 생성과 직접 의미 판정
6. 선택 source의 baseline diff, exact-once ledger, 새 실패 좌표부터 전수 감사
7. Catalog pagination 계약과 봉인 1,503줄 provider-bearing 3층 검증 완료; GPU/약 5만 LOC 성능은
   사용자 결정으로 현재 범위 밖이며 미실행 상태를 그대로 기록

현재 완료된 속도 판정은 같은 1,503줄 required population을 손실 없이 처리한 단계별 시간과 token으로만
한정한다. 약 5만 LOC 제품 규모 성능은 현재 범위 밖이자 미검증이다. 결과 수를 줄이는 cap이나 skip은
성능 개선으로 인정하지 않는다.

## 4. 최종 인도

- spec↔contracts↔constitution↔code↔tests↔HTML을 마지막 stable hash에서 한 번 전수 대조한다.
- 실제·정적·봉인 검증 증거와 남은 한계를 CURRENT_STATUS에 기록한다.
- 보호 WIP와 범위 밖 repository를 staged allowlist에서 제외한다.
- 검증된 Analyzer 책임 slice만 commit/push한다.
