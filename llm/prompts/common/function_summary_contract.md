## [FUNCTION SUMMARY ROLE CONTRACT]

### 함수 전용 연결 문맥

- `linked_callee_outcomes_json`은 현재 TARGET이 직접 호출하는 사용자 작성 함수 중 이미 확정된
  호출 결과만 담는다. caller가 관측하는 반환·외부 상태 효과를 이해하는 데 사용하되 callee의
  내부 절차나 책임 전체를 현재 TARGET에 복사하지 않는다.
- `linked_external_source_json`은 request-local fact key와 현재 TARGET에 결정론적으로 연결된
  외부 원문 text의 맵이다. 파일·줄·종류·matched key는 structural profile에 한 번만 있으므로
  여기서 반복하지 않는다. 연결된 원문이 직접 입증하는 의미만 사용한다.
- CALL의 dependency 상태가 cycle로 인해 unavailable이면 호출 경계는 보존하되 보이지 않는
  callee 결과를 만들지 않는다. 확인 가능한 부분만 partial로 쓰거나 근거가 없으면 insufficient다.

- `FUNCTION.RESPONSIBILITY`: 호출자가 관측하는 target의 책임 경계를 한 문장으로 말한다. 이름을
  번역한 제목은 금지하지만, 다른 FUNCTION role이 소유한 입력·분기·처리·결과 세부를 여기서 다시
  열거하지 않는다.
- `FUNCTION.INPUT_ROLE`: 이 slot의 fact anchor가 가리키는 입력값이 target에서 맡는 입증된 역할을 쓴다. 식별자 모양으로 업무 뜻을
  만들지 않는다.
- `FUNCTION.GUARDS_AND_BRANCHES`: 이 slot의 RULE/CONTROL fact anchor가 소유한 선행 검증,
  조기 종료, 분기와 fallback을 조건별 결과에 연결한다. 일부 오류 경로를 전체 반환 정책으로 일반화하지 않는다.
- `FUNCTION.PROCESSING_EFFECTS`: 이 slot의 CALL/DATA_OBJECT/STATEMENT fact anchor가 소유한
  호출·조회·변경·대입·transaction과 외부 상태 변화를 쓴다. callee 내부 동작은 복사하지 않는다.
- `FUNCTION.RESULT`: 이 slot의 RETURN fact anchor가 소유한 반환과 출력 구조를 정확한 조건에 연결한다. 서로 다른 조건의 반환·출력을
  합쳐 하나의 무조건 결과처럼 쓰지 않는다.

- 같은 FUNCTION role이 여러 slot에 반복되는 것은 구조 사실의 책임 분할이며 중복 출력이 아니다.
  각 slot은 자기 fact refs만 설명한다. 전체 summary는 같은 응답의 모든 slot과 SOURCE를 종합한다.

- 선언됐지만 사용되지 않는 파라미터는 그 미사용 상태 자체가 현재 SOURCE로 완전히 입증된
  interface 사실이다. 이름에서 예상한 역할이나 호출자 문맥 부재를 이유로 낮추지 않는다.
- write-only/output-only 파라미터는 입력값으로 읽는다고 만들지 말고, SOURCE에 보이는 출력 변경과
  그 조건만 `sufficient`로 쓴다. 호출자 문맥 부재를 이유로 낮추지 않는다.
- 즉시 상수 반환만 하는 no-op routine은 보이는 그대로가 완전한 동작이다. 이름에서 예상한 검사·
  변환이 없다는 이유로 `partial|insufficient`로 낮추지 않는다.
- 해당 역할의 실제 구조축이 없어 APPLICABLE_SLOTS에 배정되지 않았다면 그 역할을 다른 칸에
  억지로 만들어 넣지 않는다.
