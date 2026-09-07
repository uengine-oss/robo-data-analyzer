# ROBO Data Analyzer 헌법

이 문서는 Analyzer가 어떤 기능을 추가하거나 리팩터링하더라도 지켜야 하는 최상위 원칙이다.
구체적인 wire와 필드는 `specs/001-analyzer-redesign/`의 현행 spec과 contract가
소유한다. 과거 spec·실험·보고서는 현재 제품 계약을 바꾸지 못한다.

## I. 사실을 확인한 뒤에만 제품을 바꾼다

- 잘못된 결과 하나의 정확한 원문 좌표에서 시작해
  `원문 → Parser 구조 사실 → Analyzer 내부 변환 → 실제 모델 요청 → 응답 검증 → graph 공개`를
  실제 입력값·변환값·출력값으로 추적한다.
- 가설은 다음 검사를 고르는 데만 사용한다. 원인이 닫히기 전에 제품 로직을 수정하지 않는다.
- 가장 값싼 증거인 봉인 hash, 기존 actual, exact-once ledger와 현재 source/data flow부터 확인한다.
  필요할 때만 영향 좌표, 집중 계측, 최소 재현·역실험, Git history/bisect, 외부 1차 근거 순으로
  조사 범위를 넓힌다.
- 특정 고객 코드, 이름 모양, 한 언어의 문자열 패턴에 맞춘 패치는 제품 해법이 아니다.

## II. Parser는 구조를 기록하고 Analyzer가 의미를 결정한다

- ANTLR Parser는 decode 결과, grammar 종류, 정확한 source range, parent/child, source order,
  call/callee/receiver/argument 범위, 선언·사용·import·type reference·조건식과 문법적 presence만
  생산한다.
- Parser는 final binding, overload 선택, type compatibility, linkage, internal/external,
  standard-library/framework 분류, 업무 의미와 graph edge를 결정하지 않는다.
- Analyzer의 언어별 semantic adapter가 C·Java·Python·SQL 등의 scope, visibility, import,
  type, linkage와 동적 한계를 해석한다. 언어 중립 resolution core가 최종 상태와 관계 후보를
  정확히 한 번 확정한다.
- Analyzer가 Parser 결손을 언어별 정규식, 문자열 분할, 괄호 세기, 이름 추측으로 다시 파싱해
  숨기는 것은 금지한다. 필요한 구조 사실이 없으면 Parser 계약을 고친다.

## III. 모든 필수 대상을 손실 없이 정확히 한 번 닫는다

- 각 단계는 먼저 required population을 확정하고, 각 key를 성공·정상 미확정·명시 제외·실패 중
  계약이 허용한 한 상태에 정확히 한 번 배정한다.
- missing, duplicate, unexpected, unclassified residual을 빈 성공이나 정상 미확정으로 바꾸지 않는다.
- 같은 함수·원문·의미 의무를 같은 concern에서 반복 분석하지 않는다. 큰 입력을 나눌 때도 각 source
  item과 의미 obligation은 정확히 한 partition에만 속하고 한 번만 합쳐져야 한다.
- partial upload는 정상 입력이다. 업로드 범위 밖 정의는 `external`로 닫는다. `unresolved`나
  occurrence를 별도 제품 graph node/label로 만들지 않고 run artifact와 진단 장부에 보존한다.

## IV. 모델에는 의미 판정에 꼭 필요한 비중복 정보만 보낸다

- 함수 원문은 한 번 줄 번호를 붙인 뒤 Parser 범위로 잘라 한 section에만 둔다.
- 모델 입력은 원문, 작은 구조 지도, 원문만으로 알 수 없는 사용 symbol의 확정 선언값·타입,
  직접 table/column 사용, 확정된 사용자 callee의 반환·외부 상태·직접 data 효과, 현재 코드가 실제로
  소비하는 검증된 project/framework 계약으로 구성한다.
- stable ID, hash, 절대 경로, char offset, grammar 내부 ref, candidate/resolution 장부, confidence,
  evidence 복창, question처럼 모델의 의미 판정을 개선하지 않는 내부 정보는 모델에 보내지 않는다.
- 같은 사실을 source·structure·symbol·table·callee section에 반복하지 않는다. 빈 선택 section도
  보내지 않는다.
- 같은 정적 지시도 system·user·공통 contract 중 한 정본에만 둔다. Summary·FunctionTableUsage·Rule의
  system prompt는 하나의 정본에서 읽어 실제 전송 바이트가 같아야 한다.
- 짧게 만들기 위해 이름을 암호처럼 축약하거나 의미를 생략하지 않는다. 제거 기준은 오직
  `없어도 정확성·완전성·재현성이 변하지 않고 다른 권위 값에서 완전히 복원되는가`다.

## V. LLM 호출은 닫힌 목록이고 같은 의미를 두 번 묻지 않는다

제품에서 허용되는 생성 family는 다음뿐이다.

1. 알려지지 않은 project의 scanning adapter 후보
2. 알려지지 않은 외부 문서 형식의 adapter 후보
3. 기존 패턴으로 연결되지 않은 table 접근 후보
4. 함수 Summary
5. 직접 FunctionTableUsage
6. Rule atom 업무 의미와 row 분류
7. 최종 Table·Column 설명

- 예시 문장, Module, Component, Glossary, search segment, graph projection과 모든 ledger를 위한
  별도 LLM 호출은 만들지 않는다. 현행 제품 graph에서 제거된 객체는 생성·저장하지 않는다.
- Summary·FunctionTableUsage·Rule은 같은 함수 입력에서 concern별 projection을 만들고 서로의
  자연어 결과나 완료를 기다리지 않는다. 승인된 3분할 외에 같은 함수를 다른 의미로 다시 묻지 않는다.
- hidden judge, repair, second meaning pass, dummy warm-up, 자동 follow-up/tool loop, content retry는
  금지한다. 서버가 request bytes를 받지 않았음이 증명된 transport 실패만 같은 invocation identity로
  재전송할 수 있다. 현재 runner와 provider SDK의 자동 재시도는 0이며, `deadline`·`length`·`repetition`·
  schema 실패를 더 작은 의미 요청이나 수정된 응답으로 성공 승격하지 않는다.
- 공통 system prefix는 byte-identical하게 유지하고 가변 입력은 뒤에 둔다. 각 family의 첫 실제
  요청이 cache를 데우며, cache hit를 위해 의미가 다른 요청을 억지로 합치지 않는다.
- 위 cache는 provider의 KV-prefix cache다. Analyzer의 영속 응답 cache는 기본 OFF이며, 모델 이름이나
  endpoint를 동일성 근거로 삼지 않는다. weights/revision·tokenizer·quantization·serving runtime·provider
  hard limit 정본의 ref와 SHA-256이 일치할 때만 사용하고, 실제 필터 적용 메시지와 모든 생성 설정을 key에
  포함한다. LangChain 등 별도 숨은 response cache는 금지한다.

## VI. 출력 빵틀은 누락을 막되 모델에게 구조 소유권을 넘기지 않는다

- 모델 응답은 closed schema를 사용한다. 여러 의미값의 swap을 순서만으로 탐지할 수 없는 family만
  Analyzer가 미리 배정한 연속 순번 local key를 사용하며, 그 key에 stable ID·hash·line/range를
  인코딩하지 않는다. extra field와 임의 key를 거부한다. schema의 설명용 title·description과 동적
  fingerprint는 의미 제약이 아니며 prompt와 중복되므로 provider wire에 넣지 않는다.
- 출력 token은 자연어 의미와 wire 문법을 분리해 측정한다. 의미 필드명과 local key를 암호식 약어 또는
  위치 배열로 바꿔 귀속 검증과 사람이 읽는 계약을 약화시키지 않는다. wire 축약은 같은 의미·같은
  exact-once 검증을 유지하면서 실측 이득이 충분할 때만 허용한다.
- Summary 모델은 함수 전체 흐름의 `summary`와 허용된 `stereotype`을 한 번만 만든다. 세부 문장을
  따로 생성해 기계적으로 이어 붙인 것을 최종 Summary로 승격하지 않는다.
- Table 모델은 Analyzer가 확정한 직접 `(function, table, access mode)`만 설명한다. 하위 함수의 table
  사용을 caller에 복사하지 않고 Summary나 FunctionTableUsage의 생성 문장을 다시 입력으로 사용하지
  않는다. TABLE/COLUMN 의미는 자기 source 설명과 결정론 직접 사용 사실만 소비한다.
- Rule 모델은 Analyzer가 확정한 condition/effect atom key마다 의미 문자열 하나와 row의
  `business_rule|implementation_detail|insufficient` 분류 문자열 하나만 한 응답에서 채운다. atom이
  비면 Analyzer가 해당 row를 `insufficient`로 닫는다. 새 row·symbol·effect,
  Python, Rule, 근거·질문 문장을 만들지 않는다.
- 모델의 schema·grounding·coverage 실패는 구조 후보를 제품 의미로 승격시키지 않는다. 모델이 만든
  진단 문장을 또 저장하지 않고 Analyzer가 보존된 구조와 상태로 감사 사유를 결정론적으로 만든다.

## VII. Strategy B에서 업무 의미의 최종 생산자는 Analyzer다

- Strategy A 제품 노선은 사용하지 않는다. 좌표·분기·exact-once 장부는 Strategy B의 구조 기반으로
  보존한다.
- Analyzer는 `business_rule`이면서 필요한 모든 atom이 `sufficient`인 row만 restricted Python의
  업무 조건·효과로 투영해 제품 Rule로 공개한다.
- `implementation_detail`은 `excluded`, 의미 불충분은 `unverified`, 모델 호출 실패는
  `unavailable` finding으로 후보 row마다 정확히 한 번 종료한다.
- Analyzer는 공개할 Rule의 구조 조건·효과와 자연어 설명을 같은 row에서 조립한다. 별도 Example 제품은
  만들지 않는다.
- restricted Python은 자연어 변동을 구조적으로 비교하기 위한 표기이지 실행 코드가 아니다.
  Python parse 성공이나 반복 문자열 동일성은 업무 의미 정확도나 제품 합격이 아니다.

## VIII. 결정론 코드는 의미를 추측하지 않고 조립·검증·보존한다

- AST/DDL 구조, source range, owner, direct data access, call candidate와 occurrence, source order,
  exact key, Rule 조립, graph identity와 ledger는 결정론 코드가 소유한다.
- 결정론 코드는 근거가 없는 업무 뜻을 만들거나 모델의 부족한 뜻을 fallback으로 채우지 않는다.
- 표준·라이브러리·framework 제공 함수는 내부 판정 장부에는 필요할 수 있지만 제품 `CALLS` 관계로
  공개하지 않는다. lexical shadowing 뒤 선택된 callable identity와 봉인 registry로 분류한다.
- 같은 사용자 함수를 한 함수 안에서 여러 번 호출하면 occurrence는 모두 보존하고 제품 `CALLS`
  caller→callee pair는 하나만 만든다.

## IX. 임의 제한으로 속도나 성공률을 꾸미지 않는다

- 임의 item·문자·token·시간 상한, Top-N, sampling, truncation, skip, 조용한 fallback으로 분석량을
  줄이지 않는다.
- provider가 실제로 강제하는 hard context/output limit은 실행 전 model·tokenizer·runtime·GPU 정체성과
  함께 봉인한다. hard limit을 실제로 넘는 경우에만 source와 obligation을 겹치지 않게 분할하고
  exact-once로 한 번 합친다.
- 성능은 결과 수를 줄인 시간이 아니라 동일 required population을 처리한 wall time, queue,
  input/output token, cache, prefill/decode, validation, write/publish 시간으로 측정한다.
- 요청 admission은 실제 token·KV·GPU 자원에 근거한다. 관측 없이 동시성 숫자를 고정하지 않는다.
- client 동시성은 배포 모델 서버의 실제 running-request 상한을 넘기지 않는다. 같은 입력 집단을 여러
  동시성으로 실측해 가장 빠른 값을 쓰며, 서버 상한보다 큰 대기열을 성능 개선으로 계산하지 않는다.
- 병목은 함수와 concern별 queue wait, client-visible first chunk, stream generation, local validation,
  input/output token을 같은 요청 좌표로 연결해 판정한다. 전체 wall time이나 상관관계만으로 서버·모델·
  Analyzer를 원인으로 확정하지 않는다.
- 한 Rule 요청이 지나치게 크면 임의 행 수를 버리거나 재시도하지 않고, 완전한 row 단위로 결정론적으로
  분할한다. 모든 row와 condition/effect atom은 분할 전후 exact-once여야 하며 부분 실패는 해당 분할만
  unavailable로 닫는다.

## X. 검증된 batch만 기존 DB에 원자적으로 공개한다

- 실행 대상 database는 요청이나 실행 설정에서 명시하며, 허용된 database인지 연결 전에 검증한다.
  검증·벤치마크는 사용자가 작업 중인 database를 사용하지 않고 명시적으로 격리된 database만 사용한다.
- 전체 graph 삭제와 부분 결과 공개를 금지한다. Analyzer가 소유한 node·relationship만 교체하며 Catalog·
  Fabric 등 다른 서비스 소유 데이터는 보존한다.
- 모든 node·relationship과 exact-once 회계는 DB 밖에서 먼저 완전히 검증한다. 제품 graph에 building
  generation, active pointer, scope 같은 실행 제어 node나 속성을 만들지 않는다.
- 검증된 전체 batch를 한 transaction에서 교체한다. 어느 write나 검사가 실패해도 transaction 전체를
  rollback하고 기존 Analyzer graph를 유지한다.
- 제품 graph에는 검증된 제품 node와 관계만 둔다. candidate, occurrence, unresolved, raw response,
  local key, finding, run ID와 성능 장부는 content-addressed run artifact가 소유한다.

## XI. 검증하지 않은 축은 합격이라고 말하지 않는다

분석 정확도 합격은 다음 세 층을 모두 요구한다.

1. 자동 단위·계약·회귀 테스트와 prompt/schema/hash 검사
2. source→AST→Analyzer→graph의 결정론 exact-once 전수 회계와 sealed baseline diff
3. 사전에 봉인한 실제 좌표의 직접 업무 의미 판정

- 수정 중에는 formal red와 영향받은 작은 실제 데이터를 먼저 확인하고 점진적으로 넓힌다.
- 실제 유료 의미 검증 범위는 실행 전에 봉인한 대표 source slice로 제한할 수 있다. 이 경우 선택 범위의
  정확성·완전성·비용만 판정하며, 실행하지 않은 전체 corpus 처리 속도나 제품 규모 성능을 합격으로
  승격하지 않는다.
- GPU·서비스·DB가 비가용하면 가능한 정적 검증만 수행하고 나머지는 `미검증`으로 남긴다. 실행하지 않은
  실제 분석이나 의미 판정을 과거 수치·Python 구문 성공·테스트 통과로 대신하지 않는다.

## XII. 한 책임은 한 이름·한 위치·한 진실을 가진다

- 함수·필드·파일·관계 이름은 producer부터 consumer, 저장, search, test, 문서까지 같은 개념을 즉시
  알아볼 수 있게 일관된 이름을 쓴다. 별칭과 호환 껍데기로 옛 구조를 무기한 유지하지 않는다.
- 소비처가 0이거나 다른 권위 값에서 완전히 복원되는 필드는 제거한다. 정확성·완전성·재현성에 필요한
  내부 좌표와 상태는 모델에서 숨길 수 있어도 내부 정본에서 삭제하지 않는다.
- `specs`에는 현행 SDD와 실행 가능한 contract만 둔다. audit·archive·run·experiment는 각각 지정된
  전용 위치에 두며 임시 파일과 raw 실행물을 제품 디렉터리에 섞지 않는다.
- Architect와 goal-verify는 이 Analyzer 작업 범위 밖이다. 명시적으로 범위가 바뀌지 않는 한 읽기·실행·
  수정·stage하지 않는다.
- 사용자 WIP와 출처 미확정 파일은 보존한다. 보호 목록에 든 파일을 검증된 책임 slice와 섞어 commit하지
  않는다.

## Governance

- 현재 진실의 우선순위는 `현재 코드와 실제 실행 결과 → 이 헌법과 현행 spec/contracts → tasks → archive`다.
- 변경은 `spec → plan → tasks → 구현 → 국소 반증 → 전체 검증 → diff 감사` 순서로 진행한다.
- 헌법과 현행 spec이 충돌하면 작업을 멈추고 둘을 먼저 정합시킨다. 과거 문서를 현재화해 충돌을 숨기지
  않는다.
- 원칙을 바꿀 때는 이 문서의 major/minor version을 올리고 변경 이유를 기록한다.

**Version**: 2.2.0

**Ratified**: 2026-06-15

**Last Amended**: 2026-09-07

**Amendment**: 기존 공개·정확도 경계를 유지하면서 함수 의미 3분할의 system prompt를 한 정본·동일
바이트로 고정했다. 정적 지시 중복과 입출력 wire 비용은 실제 token으로 분리 측정하되, 의미 누락·위치
배열·암호식 약어로 정확성과 exact-once 귀속을 교환하지 않도록 성능 판정 기준을 강화했다. 큰 Rule은
완전한 row 단위로 결정론 분할하고, client 동시성은 배포 서버의 실측 처리 상한에 맞추도록 명시했다.
