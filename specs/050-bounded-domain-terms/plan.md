# Plan 050

1. Pydantic 경계에 `DomainTermItem` 배열을 두고 Framework·DBMS 모두 10개 절대 상한을 강제한다.
2. 공용 정규화/JSON codec을 만들고 두 분석 경로에 연결한다.
3. CodeNode, glossary 수집, Neo4j 저장, 재종합을 dict/JSON 계약으로 변경한다.
4. 두 전략 프롬프트에 10개가 목표가 아니라는 종료 원칙, 의미 우선순위, 정상·실패·경계 예시와
   규칙별 출력 토큰 상한을 반영한다.
5. 0·10·11개 경계, 중복, 원문 부재, 파생 체인, codec·소비처 회귀와 prompt 계약/hash 검사를 실행한다.
