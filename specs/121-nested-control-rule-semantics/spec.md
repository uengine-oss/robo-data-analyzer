# spec 121 — direct-empty controller의 사람용 RULE 의미

## 문제

trusted RULE 39건 전수에서 5건이 자식 제어 블록에 실제 효과가 있는데도
`direct=[]`만 보고 “아무 효과/동작 없이 지나간다”고 설명했다. 자식 효과를 부모에
복사하면 행 소유권과 중복 방지 계약을 깨므로 금지한다.

## 요구사항

- FR-001: `rule_evidence`는 행 소유 branch에 중첩 제어 자식이 있음을 결정론 boolean으로 제공한다.
- FR-002: `direct`는 계속 현재 행의 직접 효과만 소유하며 자식 효과를 복사하지 않는다.
- FR-003: `nested_control=true`이고 `direct=[]`이면 NL은 “아무 효과 없음”이라고 쓰지 않고
  하위 분기 로직을 평가/수행한다는 경계를 설명한다.
- FR-004: 실제로 직접 효과도 중첩 제어도 없는 행만 “아무 효과 없이 지나감”을 허용한다.
- FR-005: framework/DBMS 공통 계약이며 strategy 분기를 만들지 않는다.

## 성공 기준

- SC-001: 중첩 IF 부모 evidence는 `nested_control=true`, `direct` 없음이다.
- SC-002: leaf direct-empty 행은 `nested_control`이 없거나 false다.
- SC-003: prompt byte ledger와 전체 회귀가 통과한다.
- SC-004: 최종 DBMS run의 문제 좌표 5건에 “아무 효과/동작 없음”이 0건이다.

