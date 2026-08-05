# 컨텍스트·값 흐름 직접 검증 축

고정 20개 정확도 판정은 최종 문장만 보지 않는다. 각 gold claim마다 아래
흐름을 prompt log와 graph read-back에서 역추적한다.

`source definition/assignment → reference/condition → call/table/output/return`

## A. 컨텍스트 완전성

- 함수의 실제 source span과 필요한 상위·하위 범위가 입력에 들어왔는가
- 변수의 선언만이 아니라 초기값, 후속 대입값, 읽기 위치가 들어왔는가
- 매크로·상수·상태코드의 실제값과 비교 연산이 들어왔는가
- 구조체 필드의 소유 경로와 대상 필드가 보존됐는가
- callee의 계약과 반환값이 summary/search_text handoff에서 열화되지 않았는가
- 실제 사용 테이블·컬럼과 DDL 설명이 해당 관계에 연결됐는가

근거가 prompt input에 없으면 `context/link/handoff` 최초 실패로 판정한다.

## B. 지침을 사용한 의미 해석

- 입력에 있는 조건·값·행위·종료 방식을 Rule이 완전하게 연결했는가
- Example이 Rule을 반복 서술하는 데 그치지 않고 실제 분기값과 결과를
  정확히 예시화했는가
- 변수명만 복사하고 역할·값·흐름을 해석하지 않은 출력은
  `unresolved-symbol-semantic` 의심으로 표시한다.
- 반대로 구분에 필요한 실제 필드명·호출명·테이블명·상수값은 삭제하거나
  임의 자연어로 바꾸지 않는다.

입력에 근거가 있는데 최초 LLM 응답부터 누락·왜곡되면
`prompt/model-interpretation` 실패로 판정한다.

## C. 후처리 보존

LLM parsed response가 맞는데 최종 산출물이 다르면 아래 순서로 최초 실패를
고정한다.

`response model/parser → normalize/filter → fan-out/handoff → merge/write → graph read-back`

## 판정 원칙

- 이름이 그대로 나왔다는 사실만으로 실패도 성공도 아니다.
- 이름과 실제값·참조·결과의 연결이 없으면 의심하고 upstream context를 연다.
- 의미가 비슷해도 조건 집합, 예외, 보류 상태, 반환값이 다르면 다른 주장이다.
- 누락을 재시도나 별도 LLM 보정으로 덮지 않는다.
- 원인 위치를 확정한 뒤 동일 원인군만 범용적으로 수정한다.

## 2026-07-30 재실행 실측

대상 실행은 `20260730_075154_480012698fb3`이다.

- 고정 20개 활성 gold claim 219개의 `evidence_text`에서 주석·괄호만 제외한 실행 줄을
  실제 `code_rules_examples` user prompt와 대조했다. `219/219` 모두 해당 prompt에
  글자 그대로 존재했다.
- `zapamcom10060:b000_main_proc`의
  `LEN_ZAPAMCOM10060_IN_CHECK_CARD_RESULT=3`은 `referenced_symbols_json`에 있었다.
  그런데 최초 LLM 응답은 `strncmp(..., "A00", 3)`을 3자리 prefix가 아닌 값
  `A00`의 완전 일치처럼 서술했다. 최초 실패는 `prompt/model-interpretation`이다.
- `zordmutl:ordf_chk_adlt_vald`의 `YY_EIGHTEEN="18"`,
  `YY_NINETEEN="19"`, `YY_TWENTY="20"`도 prompt에 있었다. 최초 응답은 이 두 자리
  접두사와 입력 생년월일 연결을 `1918/1919/2000`으로 잘못 조립했다. 이 역시
  `prompt/model-interpretation`이다.
- `zapamcom10020:b000_main_proc`의 SOURCE에는 64개 `PFM_TRY` 호출과 마지막
  `PFM_CATCH: return RC_ERR`가 있지만, `PFM_TRY` 자체의 제어 계약은 source,
  `referenced_symbols_json`, `codebase_notes` 어디에도 없었다. 은행 검사 실패를
  정상 반환으로 뒤집은 최초 실패는 `context/framework-runtime-handoff`이다.

따라서 “SOURCE가 들어갔는가”와 “참조 심볼의 실제 값·런타임 계약까지 의미적으로
폐쇄됐는가”를 별도 항목으로 센다. 단순 문자열 포함률만으로 컨텍스트 완전성을
통과시키지 않는다.

## 2026-07-30 context-closed 재실행 추가 추적

대상 실행은 `20260730_083055_7b29d1c31792`이다.

- 고정 20개 실행 근거 줄은 `4139/4139`, claim 단위로는 `219/219` 모두 Rule prompt에
  존재했다. 다음 함수 시작을 알리는 주석성 배너 9줄만 제외하면 실행 근거 누락은 없다.
- `c210_insert_zpay_spc`에서 공통코드 테이블에 실제 전달한 DBIO 입력은
  `comm_cd_id`, `comm_cd_val`인데 prompt 컬럼에는 같은 함수의 다른 입력 구조체에서
  보인 `pay_mthd_cd`, `bank_card_co_cd`가 섞였다. 최초 실패는 모델이 아니라
  `table_prompt_context.direct_column_names`가 소유 구조체를 보지 않고 이름 존재만
  검사한 `context/table-column-ownership`이다.
- 함수별 DDL 허용 컬럼도 prompt에 실제 보인 부분집합이 아니라 테이블 전체 목록을
  후처리에서 허용했다. prompt와 validator의 단일 진실 불일치다.
- `d510`의 1930 ELSE는 1877의 직접 여집합이며 1862·1869의 외곽 분기를 상속한다.
  기존 `branch_index`는 들여쓰기만 제공해 모델이 이 ELSE에 앞선 거래 ID 그룹까지
  포함하는 모순을 만들었다. complement에 직접 대응 decision과 외곽 decision 좌표를
  명시하도록 구조 인덱스를 보강했다.
- `b450_mq_trns`의 두 statement는 저장·파서에서 손상된 것이 아니라 최초 모델 JSON
  문자열 자체가 `mpfmdbio(`에서 닫혔다. 미닫힌 괄호·백틱을 의미 수정 없이 거절하는
  구조 검증을 Rule occurrence 계약에 추가했다.
- 구조체 멤버명이 용어로 나온 것은 `referenced_symbols`가 외곽 타입만 가진 경우
  멤버 식별자가 금지 집합에서 빠진 것이 원인이다. 현재 source의 `->`/`.` 멤버를
  정확 식별자로 추가하되 snake_case 모양만으로 일반 후보를 제거하지 않는다.
