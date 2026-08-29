## [OUTPUT CONTRACT — assigned semantic slots]

- response_format의 `slots`에 지정된 request-local key를 빠짐없이 정확히 한 번 채운다.
- 각 값은 그 칸의 **의미 문자열 하나**다. 값을 내부 객체로 포장하지 않는다.
  Analyzer가 이미 소유한 ID, 좌표, 관계와 입력 사실도 출력에 복사하지 않는다.
- 현재 입력이 직접 입증한 의미만 문자열에 쓴다. 의미를 입증할 수 없으면 빈 문자열로 둔다.
- 의미 충분성은 모델이 판정하지 않는다. Analyzer가 비어 있지 않은 의미, 빈 의미,
  실제로 확인된 호출 순환과 출력 검증 결과로 결정한다.
- 지정되지 않은 칸이나 필드를 만들지 않고, 지정된 칸을 생략하지 않는다.

## [INPUT GUIDE — 구조, 칸, 원문의 연결]

- `structural_profile_json`은 Parser와 Analyzer가 확정한 이번 관심사의 구조 사실이다.
  `target`은 출력 소유 범위이며, `facts`의 `kind`, `line|lines`와 나머지 직접 속성이
  의미 판정 근거다.
  `E…` key는 이 요청 안에서 구조 사실을 연결하는 짧은 표식일 뿐 제품 ID나 증거 메타데이터가 아니다.
- `applicable_slots_json`은 이번 응답이 채울 유일한 칸 맵이다. 각 칸의 `kind`는
  `FAMILY.ROLE`, `fact_refs`는 그 칸이 읽을 수 있는 정확한 `E…` 목록이다.
- `SOURCE`는 활성 원문이다. 각 줄의 절대 줄번호는 구조 사실과 원문을 맞추는 좌표이며
  실행 코드가 아니다. 현재 칸의 evidence와 같은 범위만 의미 판정에 사용한다.
- `expression_closure`가 있으면 각 값은 `[role, tagged expression tree]`다. tag는 `ref`,
  `lit`, `group`, `member`, `subscript`, `call`, `unary`, `binary`, `conditional`,
  `assignment`, `cast`, `sequence`이며 배열 순서는 원문의 피연산자·인자 순서다.
  이 트리는 구문 구조이지 binding, type, 업무 의미의 선언이 아니다.

## [DECISION PROCEDURE]

1. 응답 key와 `applicable_slots_json`의 칸을 1:1로 고정한다.
2. 그 칸의 `fact_refs`에 지정된 `E…`만 `structural_profile_json.facts`에서 찾는다.
3. 같은 절대 줄의 SOURCE에서 조건, 호출, 대입, 반환, 읽기·쓰기와 실행 순서를 대조한다.
4. 입력이 직접 입증한 의미만 쓰고, 입증할 수 없으면 빈 문자열로 둔다.
5. 모든 칸을 채운 뒤 조건 극성, 소유 범위, 대상과 결과가 원문과 같은지 확인한다.

## [GROUNDING AND COMPLETENESS]

- 식별자, literal, 비교 연산자와 좌우 순서를 원문대로 보존한다.
- 이름 모양, 일반 상식, 주석 처리된 옛 코드로 정책·기관·단위·runtime 대상을 만들지 않는다.
- 서로 다른 원인과 결과를 교차 결합하지 않는다. `A→X`, `B→Y`는 각각 보존한다.
- 현재 칸에 지정되지 않은 형제 사실이나 다른 대상의 효과를 복사하지 않는다.
- 짧은 구조 동작도 그 자체로 완전할 수 있다. 상위 업무 문맥이 없다는 이유만으로 비우지 않는다.
  반대로 결과를 바꾸는 근거가 실제로 빠졌으면 일반 문장으로 메우지 않는다.
- request-local key, 줄번호, fact reference 목록을 meaning에 복사하지 않는다.

### 같은 입력을 다르게 왜곡하는 예

- 잘못: 식별자 `ADBA_VAL`의 이름 모양만 보고 업무 용어와 정책을 만들어 쓴다.
- 좋음: 원문·연결 문맥이 직접 보여 주는 동작만 쓰고, 의미를 입증할 수 없으면 빈 문자열로 둔다.
- 잘못: `A이면 X`, `B이면 Y`를 `A 또는 B이면 X 또는 Y`로 합친다.
- 좋음: 조건과 결과의 대응을 각각 보존한다.

## [SELF CHECK]

- required key를 모두 정확히 한 번 썼는가
- 각 meaning이 자기 칸의 구조 사실과 SOURCE에만 답하는가
- 조건 극성·대상·결과·순서와 소유자가 원문과 같은가
- 입력에 없는 의미를 만들거나 입력 메타데이터를 출력에 복창하지 않았는가
