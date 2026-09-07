# Parser → Analyzer 계약

## Parser가 제공하는 사실

- 원문 decode 결과와 정확한 source range
- concrete AST label, parent-child 구조, source order
- 선언, import, call, argument, receiver 구조
- 대입 target, operator, value와 조건·반환 표현식
- 상속·구현 선언
- 각 AST 범위의 식별자 occurrence `(name, line)`
- parse completeness와 diagnostics

## Analyzer가 결정하는 의미

- scope·visibility·type을 반영한 최종 binding
- 내부 호출 대상과 외부·표준·동적 호출 분류
- 코드와 테이블의 읽기·쓰기 관계
- 변수·상수 참조와 초기화 의존 관계
- Summary와 Rule 업무 의미
- 제품 identity, 공개 transaction, 검색 embedding

## 불변식

1. Analyzer는 Parser 사실을 보충하려고 원문을 재토큰화하지 않는다.
2. Parser 사실이 불완전하면 명시적으로 실패하거나 미해결 상태를 run artifact에 기록한다.
3. 미해결 occurrence를 가짜 제품 노드나 관계로 만들지 않는다.
4. Parser producer test와 Analyzer consumer test를 같은 계약 변경에서 갱신한다.
5. Parser가 지원하지 않는 선택적 사실은 completeness로 구분하며 다른 언어에 C 전용 사실을 강제하지 않는다.
