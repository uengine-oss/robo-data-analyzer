# Analyzer 의미 무손실 구현 계획

**Updated**: 2026-08-31
**Requirements**: `spec.md`
**Current state**: `CURRENT_STATUS.md`

## 1. 실행 원칙

1. Neo4j 인증과 허용 DB online 상태를 먼저 확인한다. 실패하면 DB·분석 작업을 중단한다.
2. 입력·설정·계약과 정답지·baseline의 전체 파일 tree hash를 한 번 봉인한다.
3. 기존 actual과 source/data flow 같은 값싼 증거로 원인을 닫는다.
4. 닫히지 않을 때만 영향 좌표→집중 계측→최소 재현→history/bisect→외부 1차 근거 순으로 깊어진다.
5. 원인이 확정된 관련 수정만 한 책임 slice로 묶는다.
6. formal red와 작은 실제 데이터에서 통과한 뒤 점진 확대한다.
7. 작은 실제 생성 검증은 봉인한 SKT 2파일 1,503줄로 먼저 닫는다. 모든 관련 수정이 끝나고 GPU가
   복구되면 약 5만 LOC SKT 전체 성능·전수 회계를 마지막에 한 번만 수행한다.

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
  hash를 포함하지 않는다. response schema의 설명용 title·동적 fingerprint도 제거한다.
- TableDescription은 FunctionTableUsage LLM 문장을 재입력하지 않고 DDL/source와 결정론 직접 사용
  사실만 소비한다. 빈 optional 값과 다른 필드에서 완전히 복원되는 값은 직렬화하지 않는다.
- 모든 이름은 producer→consumer→storage→search에서 같은 개념을 표현하도록 통일한다.

### P4. LLM 실행 경계

- 허용 callsite inventory를 fail-closed로 유지한다.
- family 공통 prefix byte identity와 exact response schema를 유지한다.
- Summary·Table·Rule을 비차단 병렬 실행하고 첫 실제 요청으로 cache를 warm-up한다.
- dummy, judge/repair, content retry, 같은 concern 재질문, hidden tool loop를 제거한다.
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
- constitution과 최종 HTML을 실제 코드·필드·호출 순서와 동기화한다.

## 3. 검증 확대

1. 변경 함수의 unit/formal red
2. 영향받은 작은 실제 SKT 파일·함수 좌표
3. 같은 언어·같은 concern의 확대 slice
4. 전체 정적 회귀와 prompt hash
5. 봉인한 SKT 2파일 1,503줄 실제 생성과 직접 의미 판정
6. 선택 source의 baseline diff, exact-once ledger, 새 실패 좌표부터 전수 감사
7. GPU 복구와 Catalog pagination 계약 연결 뒤 약 5만 LOC SKT 전체 성능·전수 회계 1회

현재 완료된 속도 판정은 같은 1,503줄 required population을 손실 없이 처리한 단계별 시간과 token으로만
한정한다. 약 5만 LOC 최종 실행 전까지 제품 규모 성능은 미검증이다. 결과 수를 줄이는 cap이나 skip은
성능 개선으로 인정하지 않는다.

## 4. 최종 인도

- spec↔contracts↔constitution↔code↔tests↔HTML을 마지막 stable hash에서 한 번 전수 대조한다.
- 실제·정적·봉인 검증 증거와 남은 한계를 CURRENT_STATUS에 기록한다.
- 보호 WIP와 범위 밖 repository를 staged allowlist에서 제외한다.
- 검증된 Analyzer 책임 slice만 commit/push한다.
