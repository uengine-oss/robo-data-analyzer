## Rule leaf 계약

- `cond`는 해당 행의 `when` 문자열 배열을 순서까지 그대로 복사한다. `C12`는 성립,
  `!C12`는 불성립이다. `when:[]`이면 `cond:[]`이다.
- `then`에는 행의 `direct`에 있는 `E번호`를 빠짐없이 한 번씩 복사한다. `direct`가 없으면
  `then:[]`이다. `effects`가 각 번호의
  exact CALL/RETURN/대입을 정의하므로 같은 내용을 객체로 다시 쓰지 않는다.
- `extra`는 보통 `[]`이다. `direct`로 표현되지 않았고 현재 행의 권위 table op 또는 명시 오류
  효과가 직접 증명된 경우만 아래 고정 객체를 추가한다. 함수 이름의 관습·일반 지식만으로 CALL을
  대입·오류·테이블 효과로 바꾸지 않는다.
  - 오류: `{"op":"raise_error","target":null,"value":null,"from_source":null}`
  - 테이블: `{"op":"select|insert|update|delete|upsert","target":"정확한 table ID","value":null,"from_source":null}`
- 값을 계산·축약·해석하지 않고 상태값의 업무 뜻을 추론하지 않는다. 시스템이 선택한 ref/extra 객체를
  표준 Python Rule 표기로 직렬화한다.
- `nl`은 같은 규칙을 사람이 이해할 수 있게 설명한 개행 없는 한 문장이다. 입증되지 않은 상태값은
  exact 값을 함께 표시한다.
- Example과 anchor_line은 출력하지 않는다. 키가 anchor 좌표이며 Example은 승인된 Rule에서 시스템이
  파생한다.
