## [FUNCTION SUMMARY ROLE CONTRACT]

- `FUNCTION.RESPONSIBILITY`: 호출자가 관측하는 target의 책임 경계를 한 문장으로 말한다. 이름을
  번역한 제목은 금지하지만, 다른 FUNCTION role이 소유한 입력·분기·처리·결과 세부를 여기서 다시
  열거하지 않는다.
- `FUNCTION.INPUT_ROLE`: 입력값이 target에서 맡는 입증된 역할을 쓴다. 식별자 모양으로 업무 뜻을
  만들지 않는다.
- `FUNCTION.GUARDS_AND_BRANCHES`: 결과를 바꾸는 선행 검증, 조기 종료, 주요 분기와 fallback을
  조건별 결과와 연결한다. 일부 오류 경로를 전체 반환 정책으로 일반화하지 않는다.
- `FUNCTION.PROCESSING_EFFECTS`: target이 직접 수행하는 호출·조회·변경·대입·transaction과
  외부 상태 변화를 실행 순서에 맞게 쓴다. callee 내부 동작은 복사하지 않는다.
- `FUNCTION.RESULT`: 반환과 출력 구조를 정확한 조건에 연결한다. 서로 다른 조건의 반환·출력을
  합쳐 하나의 무조건 결과처럼 쓰지 않는다.

- 선언됐지만 사용되지 않는 파라미터는 그 미사용 상태 자체가 현재 SOURCE로 완전히 입증된
  interface 사실이다. 이름에서 예상한 역할이나 호출자 문맥을 missing_context로 요구하지 않는다.
- write-only/output-only 파라미터는 입력값으로 읽는다고 만들지 말고, SOURCE에 보이는 출력 변경과
  그 조건만 `sufficient`로 쓴다. 호출자 문맥을 missing_context로 요구하지 않는다.
- 즉시 상수 반환만 하는 no-op routine은 보이는 그대로가 완전한 동작이다. 이름에서 예상한 검사·
  변환이 없다는 이유로 `partial|insufficient`로 낮추지 않는다.
- 해당 역할의 실제 구조축이 없어 APPLICABLE_SLOTS에 배정되지 않았다면 그 역할을 다른 칸에
  억지로 만들어 넣지 않는다.
