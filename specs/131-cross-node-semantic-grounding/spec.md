# Analyzer 의미 무손실 Parser→Graph 제품 명세

**Status**: Active — SKT 1,503줄 실제 생성·3층 검증·책임 slice push 완료. Catalog pagination 외부 계약과 GPU 복구 후 약 5만 LOC 최종 성능 검증이 남아 있음
**Updated**: 2026-08-31
**Entry point**: `specs/131-cross-node-semantic-grounding/README.md`

## 1. 목표와 범위

원문 bytes를 ANTLR Parser가 언어별 문법·구조 사실로 바꾸고, Analyzer가 그 사실을 해석해
binding·type·linkage·호출·데이터 사용·업무 의미를 결정한 뒤 기존 Neo4j DB에 원자적으로 공개한다.
중간 단계는 의미를 잃거나 추측으로 채우지 않으며, 모든 필수 대상은 결과 또는 명시적 종료 상태에
정확히 한 번 포함된다.

범위는 원문→Parser→Analyzer→`neo4j|test|framework|dbms|system` 기존 DB까지다. 새 DB를 만들지
않는다. Architect와 goal-verify는 코드·서비스·DB·WIP를 포함해 전면 범위 밖이다.

## 2. 전 구간 불변식

- 원인이 실제 입력→변환→출력으로 확정되기 전 제품 로직을 수정하지 않는다.
- 임의 개수·길이·시간 상한, 조용한 truncation·skip·fallback, 이름 모양 기반 추측으로 속도나
  정확도를 가장하지 않는다.
- 입력·설정·계약·정답지·baseline·모델 runtime은 한 번 hash로 봉인한다. 여러 파일로 된 정답지와
  baseline은 manifest 한 파일이 아니라 전체 하위 파일의 canonical tree hash로 봉인하고, 같은 hash의 원문이나 결과를
  반복 생성·재분석하지 않는다.
- partial upload는 정상이다. 분석 범위 밖 정의는 `external`로 닫는다.
- 전체 source population과 각 partial source population은 서로 다른 안정 scope를 사용한다. 같은 선택 집합의
  반복 실행만 기존 active generation을 교체할 수 있다.
- unresolved 전용 graph node·label을 만들지 않는다.
- Python parse 성공이나 반복 문자열 동일성을 업무 의미 정확도 또는 제품 합격으로 승격하지 않는다.
- 필수 정보는 제거하지 않는다. 소비처가 없거나, 다른 필드에서 완전히 복원되거나, 정확성·완전성·
  재현성에 영향을 주지 않는 값만 제거한다.

## 3. Parser 책임

Parser는 문법이 증명하는 구조 사실만 생산한다.

필수 source 정보:

- source identity, 원본 bytes SHA-256, encoding, language
- grammar/frontend/parser/configuration version

필수 구조 정보:

- 안정된 fact identity와 grammar kind
- 정확한 start/end source range, parent/child, source order
- call 전체·callee·receiver·각 argument의 정확한 range
- callable·declaration·use·type reference·import·macro·조건식의 문법 구조
- conditional compilation 등 문법적으로 확인되는 presence

Parser는 final symbol binding, overload 선택, type compatibility, C linkage, Java dispatch, Python
rebinding, internal/external, 표준·framework 여부, 업무 의미를 결정하지 않는다. Analyzer가 원문을
언어별 정규식으로 다시 파싱해 Parser 결손을 숨기는 것도 금지한다.

Parser와 Analyzer 사이의 실행 wire는 `contracts/parser-structural-facts-v2.md`가 소유한다.

## 4. Analyzer 구조·연결 책임

Analyzer는 source↔AST population을 먼저 exact-once 대조한다. 누락·중복·범위 이탈이 있으면 의미
분석이나 graph 공개를 시작하지 않는다.

언어 adapter는 C·Java·Python·SQL의 scope, declaration visibility, type/linkage, import, overload,
dispatch, dynamic 한계를 해석한다. 그 결과를 공통 resolution core의 중립 후보로 넘기며, core가
각 발생을 다음과 같이 닫는다.

- `internal`: 업로드 범위의 사용자 작성 정의와 유일하게 연결
- `external`: 정상 partial upload 경계
- `standard_library` 또는 `framework_provided`: 의미 해석에 필요한 내부 사실은 보존하되 제품
  `CALLS` 관계는 만들지 않음
- `configuration_dependent|ambiguous|dynamic|inactive|unsupported`: 구조 발생과 진단은 보존하되
  추측 edge를 만들지 않음

같은 사용자 함수가 한 함수 안에서 여러 번 호출되면 occurrence는 각각 보존하고 제품 `CALLS` pair는
한 번만 만든다. 표준·라이브러리·framework 제공 함수는 제품 `CALLS` 대상이 아니다.

## 5. 의미 분석 입력

함수마다 원문을 한 번만 번호 붙인 뒤 Parser 범위로 필요한 함수 body를 자른다. 같은 정보를 여러
section에 복제하지 않는다. 모델에 전달 가능한 동적 문맥은 의미 판정에 실제로 필요한 다음 값뿐이다.

1. 분석 목표와 응답 locale
2. 줄 번호가 붙은 정확한 원문 코드
3. 원문 중첩을 보존한 작은 구조 지도
4. 현재 원문만으로 알 수 없는 사용 변수·field·상수의 타입과 선언값
5. 직접 사용한 table·column과 READ/WRITE 사실
6. 확정된 사용자 callee가 caller에 전달하는 반환·외부 상태·table 효과
7. 검증된 프로젝트·framework 계약 중 현재 코드가 실제로 소비하는 부분

graph ID, hash, file path, occurrence ID, 불필요한 source range 숫자, resolution 진단, confidence,
question, evidence 복창은 모델 입력·출력에서 제외한다. Summary에는 구조 사실을 source-order `facts`
배열로 한 번만 보내며 출력은 fact별 칸 없이 whole-target 의미만 가진다. 여러 의미값을 정확한 대상에
다시 결합해야 하는 Rule·FunctionTableUsage·DBMS batch에만 `C1/E1/R1/FT1/N1` 형식의 짧은
request-local 순번 key를 허용한다. 이 key에는 stable ID·hash·line/range를 인코딩하지 않는다. 결합과
감사에 필요한 canonical evidence와 좌표는 Analyzer 내부에만 둔다.

unknown Scan과 residual의 모델 출력은 pattern/rule 의미 body만 가지며 pattern ID는 Analyzer가 검증
장부에 내부 순번으로 부여한다. residual의 confirmed pattern과 attempted pattern 결합에만 `P1..Pn`
요청 순번을 사용한다. unknown external-format 출력은 검증할 `pattern`만 가지며 skill/rule 이름과
고정 `key` capture 계약은 Analyzer가 부여한다. SKILL 원문의 내부 rule ID도 모델-visible YAML에서
제거한다.

## 6. LLM 호출 계약

허용된 생성 호출은 unknown project Scan, unknown external-format adapter, unmatched table pattern,
함수 Summary, FunctionTableUsage, Rule meaning, Table/Column description뿐이다. Module·Component·Glossary·
Example은 LLM 0회다. 정적 callsite inventory가 이 목록 밖의 호출을 실패시킨다.

모든 family는 byte-identical 공통 prefix와 strict closed schema를 사용한다. schema에는 검증 제약이
아닌 title·description annotation을 넣지 않고 prompt가 의미 설명을 한 번만 소유한다. 첫 실제 요청이 cache
warm-up이며 dummy 요청은 없다. Summary·Table·Rule은 서로 기다리지 않고 비차단 병렬 실행한다.
같은 concern 재질문, 두 의미 동시 질의, hidden judge/repair, content retry, 자동 follow-up/tool loop는
금지한다. 현재 runner와 provider SDK의 자동 재시도는 0이다. 서버가 request bytes를 받지 않았음이
증명되는 전송 계약이 생기기 전에는 transport 실패도 다시 보내지 않는다. `deadline`·`length`·
`repetition`·schema 실패를 더 작은 의미 요청이나 수정된 응답으로 성공 승격하지 않는다.

첫 실제 요청이 데우는 것은 provider KV-prefix cache다. Analyzer 영속 응답 cache는 기본 OFF다.
모델 이름이나 endpoint만으로 이전 답을 재사용하지 않으며, 모델 실행환경 정본 ref와 SHA-256이 맞고
필터 적용 뒤 실제 메시지·provider·endpoint·sampling·reasoning·extra body·출력 한도·schema가 모두
같을 때만 적중한다. 별도 framework 전역 response cache는 사용하지 않는다.

실제 provider hard context limit을 넘는 큰 함수만 겹치지 않는 source partition으로 나눈다. 각 source
item과 의미 obligation은 정확히 한 partition에 속해야 하며, 모든 leaf가 검증된 뒤 synthesis를 한 번만
수행한다. 함수 크기나 임의 token 수를 정상 분할 근거로 사용하지 않는다.

## 7. 3분할 출력

### 7.1 Summary

함수당 의미 생산자는 하나다. 모델은 전체 흐름 `summary`와 허용 ontology `stereotype`을 한 번
생산한다. responsibility·decision·effect·result를 별도 생성 후 다시 합치는 중복 경로는 없다.

### 7.2 Table

Analyzer가 확정한 직접 `(function, table, access_mode)`만 설명한다. callee의 table 사용을 caller에
복사하지 않는다. DDL의 전체 table/column 제품 key를 먼저 닫고 실제 사용 문맥이 있는 key만 모델에
보낸다. FunctionTableUsage가 생성한 자연어를 TableDescription 입력으로 다시 보내지 않는다. TABLE은
자기 DDL/source 설명과 결정론적 직접 READ/WRITE/DML만, COLUMN은 자기 DDL/source 설명과 해당 컬럼에
초점을 맞춘 직접 원문만 소비한다. 모델 wire의 table 값에는 내부 `db:` ID 접두사를 넣지 않고, 빈
column 목록과 access mode에서 완전히 복원되는 operation은 보내지 않는다. 문맥 없는 key는 모델을
호출하지 않고 명시적 `missing_usage_context`로 회계한다.

### 7.3 Rule과 Example

Analyzer가 R-row, condition/effect atom, 직접 효과, source closure를 먼저 확정한다. Rule LLM은 같은
한 응답에서 exact effect atom key마다 업무 의미 문자열 하나를 채우고 각 행을 다음 하나의 분류 문자열로
판정한다. 필요한 effect 의미가 비면 Analyzer가 그 행을 `insufficient`로 닫는다. 구조 condition은 모델이
자연어로 다시 번역하지 않고 Analyzer가 원문 구조에서 만든 값을 그대로 사용한다.

- `business_rule`
- `implementation_detail`
- `insufficient`

모델은 새 row·symbol·effect를 발견하거나 condition 의미·Python·Rule·Example을 작성하지 않는다.
Analyzer는 `business_rule`이면서 필요한 effect 의미가 모두 존재하는 행만 구조 condition과 restricted
Python 업무 효과를 결합해 제품 Rule로 공개한다. Example은 그 동일 값에서 LLM 없이 생성한다.

구현 세부는 `excluded`, 의미 partial/insufficient는 `unverified`, 호출 실패는 `unavailable` finding으로
행마다 정확히 한 번 남긴다. 제품 Rule의 존재 자체가 충분한 업무 의미 검증 완료를 뜻하므로 모든
Rule에 같은 status 상수를 저장하지 않는다.

## 8. Graph와 공개

구조 stage와 의미 stage는 `(identity, property)` 소유권이 겹치지 않는다. 모든 제품 node와 관계는
analysis scope와 generation으로 격리한다. building generation을 완성하고 identity·property·endpoint·
exact-once ledger를 같은 generation에서 다시 읽어 검증한 뒤 active pointer를 한 transaction에서 바꾼다.
실패한 building generation은 공개하지 않는다.

제품 graph에는 검증된 Package·Module·File·CodeNode·Table·Column·Rule·Example·ExternalChunk·
Component·GlossaryTerm·SearchSegment와 계약된 관계만 둔다. occurrence, unresolved, candidate, raw response,
local key map, finding은 graph node가 아니라 run artifact다.

검색과 상세조회는 이 제품 graph를 별도 호환 투영 없이 직접 읽는다. 모든 제품 identity는 `stable_id`이며,
검색 corpus는 active completed generation의 `CodeNode-[:HAS_SEARCH_SEGMENT]->SearchSegment` exact-once
population이다. 벡터 인덱스는 `analysis_scope_id`와 `generation_id`를 in-index filter로 사용한다. 저장된
embedding model ID와 질의 encoder가 다르면 fallback이나 암묵 재임베딩 없이 실패한다. 상세조회는 원문·
내부 evidence를 복제하지 않고 함수 의미, 구조화 Rule/Example, 사용자 CALLS, READS/WRITES 데이터 문맥만
`semantic-detail/v3`으로 반환한다.

Rule property는 `anchor_line`, `structural_condition`, `structural_effects`, `business_effects`만 보존한다.
소유 함수, rule id, 항상 같은 status와 검증되지 않은 자연어 condition은 관계·anchor 또는 구조 정본에서
복원되거나 제품 정확도를 해치므로 중복 저장하지 않는다. Example은 `given_conditions`와
`expected_effects`만 보존한다.

## 9. 성능과 검증

수정은 formal red와 작은 실제 SKT 좌표에서 먼저 닫고 영향 범위를 점진 확대한다. 실제 유료 검증은
`zapamcom13010.c`와 `zapamcom10150.c`, 합계 1,503줄을 분석해 정확도, 누락, partial-upload external
분류, 단계별 시간·token·cache를 판정했다. 약 5만 LOC 전체 재분석은 GPU 복구 뒤 모든 관련 수정의
마지막에 한 번만 수행하며, 그 전까지 제품 규모 처리 속도는 미검증이다.

최종 합격에는 세 층이 모두 필요하다.

1. 자동 회귀와 prompt/schema/hash 계약
2. source→AST→Analyzer→graph의 결정론 exact-once 전수 회계와 baseline diff
3. 봉인된 실제 좌표의 직접 업무 의미 판정

단계별 wall time, queue, input/output token, cache 관측, 첫 실패·첫 병목 좌표를 공개한다. 정확도·누락을
줄여 시간을 맞추지 않는다.

## 10. 보호와 저장소

- `pipeline/steps/step2_load_ast/evidence_ir.py`의 사용자 WIP를 보존한다.
- `scripts/recompute_strategy_a_notation.py`는 읽기·실행·수정·stage하지 않는다.
- Strategy A 312대312 봉인 실험은 재실행·재분석하지 않는다.
- Catalog·Fabric·Architect와 goal-verify WIP를 건드리거나 stage하지 않는다.
- `specs`에는 현재 SDD만 둔다. audit·archive·run·experiment는 README가 지정한 별도 위치가 소유한다.
- 검증된 한 책임 slice만 commit/push한다.

## 11. 완료 기준

- Parser와 Analyzer 책임 위반 및 Analyzer 정규식 재파싱 0
- 필수 population residual·duplicate·unknown 0
- 승인 목록 밖 LLM 호출, 중복 concern 호출, hidden retry 0
- 모델 입력의 중복·내부 ID·근거 복창 0
- 표준/framework 제품 CALLS 0, unresolved graph node/label 0
- graph stage/property/endpoint ledger와 active generation 불일치 0
- 자동 회귀·결정론 전수 회계·봉인 직접 의미 판정 통과
- 봉인한 SKT 2파일 1,503줄의 정확도·누락·external 분류·실행 비용 결과 공개
- GPU 복구 전에는 실행하지 않은 약 5만 LOC 제품 규모 성능을 미검증으로 명시하고, 복구 뒤 마지막 한 번의
  동일 required-population 실행으로 baseline·단계별 시간·token·cache·누락 회계를 검증
- constitution·spec·contracts·code·tests·최종 HTML 일치
- 보호 WIP가 staged diff에 포함되지 않고 Analyzer origin/main과 검증 commit이 일치

## 12. 실행기 단일 공개 계약

- workflow 종료 후 final state의 모든 제품 producer field가 존재해야 하며, streamed call/import ledger는
  final state ledger와 canonical value가 같아야 한다. 하나라도 다르면 제품 write 전에 실패한다.
- `CompleteProductPlanInput`은 14개 닫힌 stage를 endpoint node 생성, relationship 생성, 의미 property
  patch 순서로 조립한다. 결정론을 이유로 alphabetical sort해 dependency를 바꾸지 않는다.
- 실행기는 building generation에 각 batch를 쓰고 stage/operation/property/read-back ledger를 검증한다.
  그 뒤 한 번의 `publish_generation`이 같은 transaction에서 완전한 제품 plan, AnalysisHealth,
  call/import/data-object 회계를 다시 읽고 publication-version CAS로 active pointer를 전환한다.
- 제품 node 13종은 모두 `(analysis_scope_id, generation_id, stable_id)` 유일성 제약을 갖는다.
  생산자·소비자가 없는 `HAS_HEALTH` relationship은 닫힌 목록에서 제거하고 AnalysisHealth record 자체를
  generation product로 검증한다.
- 실패 종료는 legacy `ANALYZER_OWNED` label이 아니라 정확한 scope/generation으로 새 제품 node를
  격리 삭제한다. 이전 active generation과 control record는 삭제하지 않는다.
- 정적 집중 회귀는 `79/79` 통과했다. 기존 `test` DB의 UUID 격리 scope와 단일 transaction에서
  실제 Neo4j rollback integration도 `1 passed in 9.01s`로 통과했다. transaction 안에서 active pointer와
  14개 stage closure를 확인한 뒤 rollback했으며 새 DB와 잔존 데이터는 만들지 않았다. 이후 기존
  `framework` DB에 SKT 1,503줄 generation `20260829_005638_ac5f8999f742`를 실제 공개해 선택 범위의
  atomic publication과 3층 검증을 닫았다. 약 5만 LOC 제품 규모 성능은 아직 미검증이다.
