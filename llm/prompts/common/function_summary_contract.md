## [FUNCTION SUMMARY ROLE CONTRACT]

### 함수 전용 연결 문맥

- `linked_callee_outcomes_json`은 현재 TARGET이 직접 호출하는 사용자 작성 함수 중 이미 확정된
  호출 결과만 담는다. caller가 관측하는 반환·외부 상태 효과를 이해하는 데 사용하되 callee의
  내부 절차나 책임 전체를 현재 TARGET에 복사하지 않는다.
- `linked_external_sources_json`은 현재 TARGET에 결정론적으로 연결된 외부 원문 배열이다. 연결된 원문이
  직접 입증하는 의미만 사용한다.
- CALL의 dependency 상태가 cycle로 인해 unavailable이면 호출 경계는 보존하되 보이지 않는
  callee 결과를 만들지 않는다. 확인 가능한 부분만 partial로 쓰거나 근거가 없으면 insufficient다.

- Summary는 호출자가 관측하는 target의 전체 책임을 한 흐름으로 쓴다. 선행 검증, 조건별 처리,
  직접 CALL/RW, 상태 변화와 결과를 연결하되 일부 오류 경로를 전체 반환 정책으로 일반화하지 않는다.

- 선언됐지만 사용되지 않는 파라미터는 그 미사용 상태 자체가 현재 SOURCE로 완전히 입증된
  interface 사실이다. 이름에서 예상한 역할이나 호출자 문맥 부재를 이유로 낮추지 않는다.
- write-only/output-only 파라미터는 입력값으로 읽는다고 만들지 말고, SOURCE에 보이는 출력 변경과
  그 조건만 `sufficient`로 쓴다. 호출자 문맥 부재를 이유로 낮추지 않는다.
- 즉시 상수 반환만 하는 no-op routine은 보이는 그대로가 완전한 동작이다. 이름에서 예상한 검사·
  변환이 없다는 이유로 `partial|insufficient`로 낮추지 않는다.
- 구조나 SOURCE에 없는 역할을 이름에서 추측해 만들지 않는다.
