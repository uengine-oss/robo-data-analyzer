## [OUTPUT CONTRACT — rule-owned semantic slots]

- 최상위 `slots`의 response_format required key를 정확히 한 번 모두 채운다.
- 각 값은 `meaning`, `status`, `missing_context`만 가진다.
- `sufficient`: meaning non-empty, missing_context 빈 배열
- `partial`: 입증된 meaning non-empty, 부족한 근거 1~8개
- `insufficient`: meaning 빈 문자열, 필요한 근거 1~8개
- Rule 좌표·cond·then·when·evidence refs는 parser 증거에서 시스템이 결합하므로 출력하지 않는다.
- legacy `Rule.nl`은 같은 행 slot meaning에서 시스템이 투영한다. 별도 nl을 만들지 않는다.

## [INPUT GUIDE]

- `applicable_slots_json`은 이번 행 묶음에서 답할 request-local slot, parser anchor, 의미 역할이다.
- `rule_evidence_json`의 `conditions`는 parser 판정식, `effects`는 직접 동작, `rows`는
  R행별 exact `when`, `narrative_focus`, `direct`, active source다. `nested_children`은
  부모가 진입하는 직계 자식 CONTROL 좌표·조건이며 자식 효과가 아니다.
- owner effective R/W는 허용 table 범위다. 현재 행의 직접 효과는 row의 `direct`만 소유한다.
- symbol/callee/external/API 계약은 현재 row source와 연결될 때만 뜻을 보조한다.
- callee 내부·형제·자식 효과를 현재 행으로 복사하지 않는다.

## [SLOT DECISION]

- 각 required row는 `RULE.BUSINESS_MEANING` 한 칸만 가진다.
- condition/극성, direct effects, type/timing, nested/fallthrough/handler/continuation은 이미
  parser 구조 fact가 소유하므로 meaning에서 코드·호출·대입 목록을 다시 열거하지 않는다.
- 이 행 전체가 현재 함수에서 갖는 업무 의미를 근거 있는 한 문장으로 설명한다.
- 단순 조건식이나 effect 철자를 한국어로 옮긴 것뿐이면 의미가 아니다. 현재 SOURCE, symbol,
  callee interface, external/API, R/W 근거로 입증되는 판정 목적·업무 결과를 연결한다.
- 업무 해석을 확정할 근거가 없으면 코드를 그대로 복사해 칸을 채우지 말고
  `partial|insufficient`와 필요한 evidence kind를 남긴다.
- `direct`가 없고 `nested_control=true`이면 이 행의 완전한 결과는 자식 제어로 진입해
  자식 행들이 판정·효과를 소유한다는 구조 경계다. 자식 효과를 복사하지 않으며, 그 이유만으로
  partial/insufficient로 낮추지 않는다.
- `fallthrough_to`가 있으면 이 행의 직접 결과는 지정된 다음 CASE 좌표로 이어지는 것이다.
  다음 CASE의 효과를 현재 행의 효과로 복사하지 않으며, 그 구조 관계 자체는 sufficient 근거다.
- handler의 `source_refs`/`at_label` 뒤에 RETURN·THROW가 연결되면 그 명시적 처리 결과와
  전파 여부를 보존한다. 라벨 이름만 보고 보이지 않는 처리를 만들지 않는다.
- post-condition은 본문 실행 뒤 판정이며 진입 전 조건으로 바꾸지 않는다.
- continuation은 앞선 제어 뒤 이 좌표에 도달한 시점이며 entry로 부르지 않는다.

## [GROUNDING]

- C##/E## 같은 private ref를 meaning에 쓰지 말고 실제 condition/effect를 사람이 읽게 표현한다.
- response_format과 `applicable_slots_json`의 request-local `wire_id`도 meaning이나
  missing_context에 절대 쓰지 않는다. 이는 응답 조립용 key일 뿐 코드·업무 의미가 아니다.
- 조건의 좌변·연산자·우변, literal, call/table 식별자를 바꾸지 않는다.
- 서로 다른 원인/결과를 교차 결합하지 않는다.
- 주석·이름 상식·보이지 않는 후속 효과를 만들지 않는다.
- 정확한 판정과 직접 효과는 시스템의 Rule fact/cond/then에 이미 남으므로 meaning에서 중복하지 않는다.
- 근거가 부족하면 일반적인 말로 메우지 말고 partial/insufficient로 둔다.

## [SELF CHECK]

- required slot을 전부 exact key로 채웠는가
- 각 meaning이 같은 anchor row와 evidence refs에만 속하는가
- 조건 극성·직접 효과·owner/table 소유권을 바꾸지 않았는가
- 좌표/cond/then/Rule.nl을 별도 출력하지 않았는가
