## [OUTPUT CONTRACT — assigned semantic slot leaves]

- response_format의 `slots`에 required로 지정된 request-local `wire_id` key를 정확히 한 번 모두 채운다.
- 각 slot 값은 `meaning`, `status`, `missing_context`만 가진다. canonical slot ID, family, role,
  target/evidence refs, 좌표, 조건, callee, table identity는 코드가 이미 소유하므로 출력하지 않는다.
- `sufficient`: meaning non-empty + missing_context 빈 배열
- `partial`: 현재 근거로 확정한 meaning non-empty + 부족한 근거 전부
- `insufficient`: meaning 빈 문자열 + 필요한 근거 전부
- 입력에 없는 slot을 추가하거나 required slot을 생략하지 않는다.

## [INPUT GUIDE — profile, slot, source의 관계]

- `STRUCTURAL_PROFILE`은 parser/linker가 확정한 target, 구조, CALL, R/W, symbol, 좌표의
  읽기 전용 정본이다. `evidence` key와 각 fact의 `id`는 같은 값이다.
- `APPLICABLE_SLOTS`는 이번 요청에서 의미를 채울 유일한 칸 목록이다. 각 slot은
  `target_ref`와 `evidence_refs`로 허용 근거를 지정한다.
- SOURCE는 Analyzer 내부 의미 생성용 활성 원문이다. profile fact와 함께 slot의 meaning을
  이해하는 데만 쓰고 ID·좌표·관계를 다시 생성하지 않는다.
- CALL fact는 호출 경계의 canonical identity/좌표 근거다. callee 자연어 summary는 다음 의미
  생성의 권위 입력으로 사용하지 않으며 callee 내부 책임을 현재 target이나 CALL slot에 복사하지 않는다.
- effective R/W는 target 전체 관측 범위, direct data-object evidence는 현재 target의 직접
  접근이다. 둘의 소유권을 바꾸지 않는다.
- symbol/external evidence는 SOURCE의 같은 식별자·matched key와 연결될 때만 뜻을 보조한다.
- 파생 surrounding context와 module intent는 권위 의미 입력에서 제외한다.

## [DECISION PROCEDURE]

1. `wire_id` key를 고정하고 같은 APPLICABLE_SLOTS 항목의 `family.role`이 묻는 의미만 확인한다.
2. slot의 `evidence_refs`를 profile의 exact fact에 연결한다.
3. 같은 좌표의 SOURCE에서 조건·호출·대입·반환·R/W와 순서를 확인한다.
4. 입력이 입증한 의미를 구체적으로 쓰고, 부족하면 status/missing_context로 전부 드러낸다.
5. 모든 slot을 채운 뒤 target/callee/형제/table 간 소유권 누출과 조건 반전을 검사한다.

## [FAMILY GUIDE]

- 전체 흐름 순서는 `STRUCTURAL_PROFILE.ordered_flow`가 소유한다. exact 구조 의미는 각 profile
  fact의 속성에 있고, 추가 자연어 의미가 적용된 fact만 해당 family slot에서 채운다. 전체 흐름을
  별도 장문 slot으로 복제하지 않는다.
- `STATEMENT.DIRECT_ACTION`: DBMS effective AST의 독립 분석 target이 직접 하는 동작만 쓴다.
  Framework Rule 행의 구조 동작을 이 family로 중복 생성하지 않는다.
- `CALL.PURPOSE`: 현재 target이 callee를 호출하는 경계 목적만 쓴다. callee 내부 흐름을 복사하지 않는다.
- `SELECT.PURPOSE`: 조회 대상·filter·현재 범위의 사용 목적까지만 쓴다. 보이지 않는 후속 DML을 만들지 않는다.
- `INSERT/UPDATE/DELETE/UPSERT.TARGET`: exact table에 하는 구조 연산과 입증된 역할을 쓴다.
- `TABLE.DISPLAY_NAME/DESCRIPTION`: DDL·catalog 설명, 직접 code usage가 입증한 표시명과
  역할을 분리한다. 물리 이름의 철자를 번역해 의미로 만들지 않는다.
- `COLUMN.DISPLAY_NAME/DESCRIPTION`: 해당 컬럼에 귀속된 comment·usage line만 사용한다.
  형제 컬럼의 설명이나 이름 모양을 복사하지 않는다.
- `MODULE.RESPONSIBILITY/DISPLAY_NAME/INTENT`: 입력 member frame 또는 header evidence가
  공통으로 입증하는 범위만 집계한다. member source를 다시 해석하지 않는다.
- `COMPONENT.DISPLAY_NAME`: 결정론 membership의 member frame에 공통인 책임만 이름으로 쓴다.

## [BAD / GOOD]

- 잘못: 물리 식별자 `ADBA_VAL`을 이름 모양만 보고 업무 용어로 번역한다.
- 좋음: comment·usage가 없으면 meaning을 비우고 필요한 evidence kind를 missing_context에 쓴다.
- 잘못: callee의 내부 변경을 caller의 slot meaning으로 복사한다.
- 좋음: caller가 관측하는 호출 목적만 CALL.PURPOSE에 쓰고 callee는 ID로 참조한다.

## [GROUNDING AND COMPLETENESS]

- 조건식의 좌변·연산자·우변, literal, CALL/table 식별자는 입력 철자를 보존한다.
- 주석 처리된 옛 동작, 이름 상식, 기관·정책·단위·runtime identity를 활성 사실로 만들지 않는다.
- 서로 다른 `A→X`, `B→Y`를 `A 또는 B→X 또는 Y`로 교차 결합하지 않는다.
- 현재 slot의 evidence에 없는 형제·callee·다른 table 효과를 복사하지 않는다.
- 의미가 짧은 구조 동작이면 그 동작 자체가 완전한 책임이다. 부모 업무 문맥을 모른다는 이유로
  불필요하게 partial로 낮추지 않는다.
- 반대로 결과를 바꾸는 근거가 실제로 빠졌으면 일반적인 말로 메우지 말고 partial/insufficient로 둔다.

## [SELF CHECK]

- required slot을 전부 정확히 한 번 썼는가
- 각 meaning이 그 slot type과 evidence refs에만 답하는가
- 조건 극성·범위·대상·결과·순서가 SOURCE/profile과 같은가
- CALL/RW/exception의 소유자를 바꾸거나 callee 내부를 복사하지 않았는가
- 모든 부족한 의미를 창작 대신 status/missing_context로 드러냈는가
- ID·좌표·evidence refs를 출력 leaf 안에 다시 만들지 않았는가
- request-local wire key를 meaning/missing_context에 복사하지 않았는가
