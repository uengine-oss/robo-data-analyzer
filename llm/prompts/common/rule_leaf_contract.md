## [OUTPUT CONTRACT — Rule leaf 계약]

### 출력 개요

response_format에 required로 지정된 각 `R<행>`마다 `nl` 한 칸만 채운다.
좌표, 조건 경로, 직접 효과는 parser 증거가 이미 확정했으며 시스템이 같은 parser 증거에서
`anchor_line`·`cond`·`then`을 결합한다. 이를 출력하거나 다시 선택하지 않는다.

## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]

- `RULE_TARGET`의 `target_id`는 현재 SOURCE node, `owner_id`는 Rule 행·effective R/W를 소유한
  루틴, `assigned_rows`는 이번 출력의 행 범위다. 다른 node/owner/row 의미를 가져오지 않는다.
- `conditions`: 조건 번호(C##)와 소스의 판정식 원문. 조건 의미와 극성 판정에만 쓴다.
- `effects`: 효과 번호(E##)와 소스의 직접 동작. 결과 술어와 대상 판정에만 쓴다.
- `rows`: 행 번호(R##)별 `role`, 정확한 전체 구조 경로인 `when`, 자연어에서 펼칠 현재
  분기 조건인 `narrative_focus`, 직접 효과인 `direct`, 해당
  원문인 `source`, 하위 제어가 있을 때의 `nested_control`, do-while처럼 본문 뒤에서
  반복을 판정할 때의 `post_condition`. `continuation` 행의 `after_control`은 앞선
  제어 좌표가 끝난 뒤 도달한 별도 실행 시점이고, `at_label`은 parser가
  확정한 정적 라벨에 일반 흐름 또는 GOTO로 도달한 합류 좌표이다.
- `owner_effective_reads_json`/`owner_effective_writes_json`은 소유 루틴에서 허용된 TABLE
  좌표 범위다. 현재 row의 직접 효과는 `direct`가 가리키는 effect가 소유하며, R/W 목록만 보고
  현재 row가 실행한다고 만들지 않는다.
- `owner_linked_table_operations_json`은 소유 루틴에 연결된 table operation 범위다. 현재 row의
  `direct` effect와 table id가 연결될 때 동작을 구체화하며 목록만으로 row 효과를 추가하지 않는다.
- symbol definition의 관계는 `TARGET_REFERENCES_DEFINITION`, callee interface는
  `TARGET_CALLS_CALLEE`, external context는 `TARGET_REFER_TO_EXTERNAL_CHUNK`다. 현재 row SOURCE의
  식별자·호출과 연결될 때만 뜻을 보조하며, callee 내부 동작을 row 효과로 복사하지 않는다.
- `api_contracts_json`이 있으면 SOURCE에 실제 호출된 API의 인자·결과값 의미를 보조한다. 계약의
  존재만으로 호출·성공·실패 효과를 만들지 않는다.
- `[]`/`{}`는 해당 범위에 확인된 보조 근거가 없다는 뜻이다. `rows`의 condition/effect까지
  없다는 뜻이 아니며 이름 상식으로 채우지 않는다.

## [DECISION PROCEDURE]

1. required R행과 그 row의 `when`/`narrative_focus`/`direct` 참조를 고정한다.
2. `when`·`narrative_focus`→`conditions`, `direct`→`effects`를 번호로 연결한다.
3. row SOURCE와 relation이 연결된 symbol/callee/external 사실만 사용해 사람이 읽게 표현한다.
4. 현재 row 소유의 조건·직접 효과만 한 문장으로 쓰고 cond/then/좌표는 출력하지 않는다.

## [DECISION — `nl` 작성법]

- 한 문장으로 "어떤 상황에서 무엇이 일어나는가"를 사람이 이해하게 쓴다.
- `when`은 시스템이 graph `cond`로 보존할 전체 경로이지 전부 자연어로 나열하라는 목록이
  아니다. 자연어에서는 `narrative_focus`가 가리키는 현재 분기 조건만 펼치고, 나머지 선행
  경로는 필요할 때만 `앞선 검증을 통과한 뒤`처럼 한 구절로 묶는다. 동작은 `direct`가
  가리키는 `effects`의 내용으로 설명한다. C##, E## 번호 자체는 쓰지 않는다.
- 문장의 술어는 직접 효과가 말하는 호출·반환·설정·DB 동작이다. `처리한다`처럼 실제
  동작을 숨기는 말로 끝내지 않는다.
- `direct`가 없고 `nested_control:true`이면 조건을 만족할 때 하위 분기 로직을
  평가한다고 쓴다. 자식 효과를 현재 행의 것으로 복사하지 않는다.
- `post_condition`은 본문 진입 조건이 아니다. 본문은 먼저 한 번 실행되고 그 뒤 해당
  조건이 참일 때 반복된다고 쓴다. 이를 `when` 조건처럼 문장 앞에 옮기지 않는다.
- 서로 다른 C## 조건은 각 좌표에서 관찰된 사실이다. 같은 변수명이 다시 보여도 두 좌표
  사이 대입으로 값이 바뀔 수 있으므로, def-use 근거 없이 서로 모순이라 하거나 도달 불가로
  판정하지 않는다.
- `nested_control:true`인 행에서는 자식 분기 전후의 값을 접어 계산하거나 초기값을
  자식 실행 뒤의 값으로 대입하지 않는다. 현재 행의 직접 효과와 자식 소유 효과를 섞지 않는다.
- `role:continuation`이면 entry 동작이라고 부르지 말고, `after_control`의 앞선 제어가
  끝나 이 좌표에 도달했을 때 현재 `direct`만 실행된다고 쓴다. 이때 `narrative_focus`는
  비어 있고 `when`은 전부 시스템용 선행 경로이므로 개별 조건을 나열하지 않는다. 앞선
  행의 효과나 정확한 업무 여집합을 추측해 합치지 않는다.
- `at_label`이 있으면 해당 라벨 좌표에 도달했을 때의 `direct`만 쓴다.
  정상 순차 실행인지 GOTO 진입인지, 또는 특정 선행 분기가 참이었는지는 추측하지 않는다.
- 둘 다 없으면 효과를 지어내지 말고 아무 효과 없이 지나간다는 사실을 쓴다.
- 긴 공통 경로는 한 구절로 묶되, 현재 행을 가르는 마지막 조건은 분명히 남긴다.
- 한 문장은 보통 240자 안에서 끝낸다. 길이가 부족하면 선행 경로의 세부 나열부터 줄이고
  현재 `narrative_focus`와 모든 `direct` 동작은 반드시 남긴다. 문장 중간에서 끊지 않는다.
- 식별자·상태값은 원문을 보존한다. 업무적 뜻은 입력 증거가 입증할 때만 병기한다.

예시:

- 나쁨: `조건 C1이 성립하면 E1을 실행한다.` — 번호 밖에 정보가 없다.
- 나쁨: `조회 결과가 없으면 해당 케이스를 처리한다.` — 실제 동작이 없다.
- 좋음: `조회 결과가 없고 재시도 한도에 이르지 않았으면 요청을 다시 보내고 처리 상태를
  대기로 남긴다.`
- 나쁨(continuation): `입력 검증, 조회, 재고 검증, 금액 검증, ...` — 선행 조건을 나열하다
  현재 직접 동작을 잃었다.
- 좋음(continuation): `앞선 주문 검증 흐름이 끝나 이 지점에 도달하면 cart_clear를 호출해
  해당 회원의 장바구니를 비우고 결과를 rc에 저장한다.`

## [SELF CHECK]

- 제공된 증거만 사용하고 실행 결과를 추측하지 않는다.
- `cond`, `then`, `extra`, `anchor_line`, Example은 출력하지 않는다.
- required `R<행>`을 빠짐없이 정확히 한 번 출력하고 다른 키를 만들지 않는다.
- 내보내기 전에 각 `nl`에 조건과 직접 동작이 모두 있는지, 한 문장인지 확인한다. `C##`/`E##`
  토큰이 하나라도 남았으면 아직 번호 연결을 끝내지 않은 것이므로 해당 source 내용으로 바꾼다.
- `direct`가 있으면 모든 직접 동작의 대상이 문장에 남아 있는지, 끝맺음이 잘리지 않았는지 확인한다.
