## [INPUT GUIDE — 각 값의 뜻과 권한]

- `assigned_table_usages`는 Analyzer가 exact direct 접근만 `(table, access)`별로 묶은 필수 목록이다.
  FT key는 이번 응답 안에서만 쓰는 칸 이름이다.
- `physical_table_id`와 `access_mode`는 확정된 연결 결과다. 다시 추정하거나 바꾸지 않는다.
- `occurrences`는 이 접근이 원문에서 실제로 발생한 줄, 파서가 확인한 operation과 qualified
  column을 담는다. 이 목록에 없는 다른 테이블 접근을 현재 FT에 합치지 않는다.
- `linked_table_context`는 현재 physical table에 이미 확정된 설명이 있을 때만 제공한다.
  물리 이름을 번역해 빈 설명을 꾸미지 않는다.
- `SOURCE`는 현재 분석 대상의 줄 번호가 붙은 원문이다. occurrences와 같은 줄의 조건·연산·결과
  사용을 함께 읽되, 호출된 함수 내부나 보이지 않는 후속 동작을 만들지 않는다.

## [DECISION PROCEDURE]

1. FT별 physical table, access, occurrences를 고정한다.
2. 각 occurrence의 원문 줄을 찾아 현재 테이블 접근이 수행하는 업무상 용도를 읽는다.
3. 같은 FT의 여러 occurrence가 입증하는 양립 가능한 용도를 한 설명으로 종합한다.
4. 테이블 설명은 원문 해석을 보조할 때만 사용하고, 다른 테이블이나 callee의 효과는 섞지 않는다.
5. 입력만으로 업무상 용도를 입증할 수 없으면 창작하지 않고 해당 FT 값을 빈 문자열로 둔다.

예시 — 입력이 READ occurrence와 그 결과를 승인 여부에 비교하는 원문을 함께 제공한 경우:

- 잘못: “테이블을 조회한다”처럼 access를 되풀이하거나 다른 저장 결과까지 추가
- 좋음: “승인 여부를 판정하는 기준값을 조회한다”처럼 원문이 입증한 현재 사용 목적만 작성

예시 — 이름과 READ 표식만 있고 원문에서도 업무 용도를 확인할 수 없는 경우:

- 잘못: 물리 테이블 이름을 번역해 업무 목적을 추측
- 좋음: 해당 FT 값을 빈 문자열로 둔다.

## [SELF CHECK]

- 배정된 FT key를 빠짐없이 정확히 한 번 채웠는가?
- 각 FT 값이 의미 문자열 하나이며 내부 객체로 포장되지 않았는가?
- 설명에 FT key, 좌표, 상태, 근거 메타데이터를 복사하지 않았는가?
- 다른 table, callee, sibling node의 효과를 섞거나 보이지 않는 의미를 만들지 않았는가?
