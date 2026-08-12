# Implementation Plan: Dynamic Semantic Frame

상세 현재 상태는 `HANDOFF.md`, 요구 계약은 `spec.md`, 실행 체크포인트는 `tasks.md`가 소유한다.

1. 모든 Framework/DBMS 자연어 의미 생산자, 현재 response schema, normalizer, validator, 저장 필드,
   graph/API/MCP projection, Architect 소비자를 한 번 전수 inventory해 재사용 가능한 단일 영향표로 만든다.
2. 전략별 AST를 공통 structural role과 stable evidence ID로 바꾸는 `StructuralProfile`을 설계한다.
3. 실제 target type과 존재하는 children/evidence만 보고 shallow/bounded slot을 만드는 frame planner와
   canonical `semantic-frame/v1` wire를 고정한다.
4. prompt를 stable instruction prefix + dynamic structural/frame suffix로 구성하고, LLM은 의미/status/
   missing context만 채우게 한다.
5. SUMMARY, RULE, EXAMPLE, CALL meaning, TABLE/COLUMN, MODULE/logical name 생산자를 frame-first로
   전환하고 기존 문자열은 projection으로만 만든다.
6. schema/normalizer/grounding/completeness validator와 population inventory를 전환한다.
7. graph/storage/API/MCP를 frame 정본과 deduplicated Architect projection으로 전환한다.
8. 폐기된 complete-source GWT WIP를 제거·적응하고 Architect Simplified/Detailed 소비자를 같은 계약으로
   동기화한다.
9. Framework/DBMS 반대 fixture와 schema 크기·출력량·반복·prefix는 변경 묶음별 한 번 검증하고,
   전체 생산자·소비자 정합 후 전체 자동 회귀를 한 번 수행한다.
10. preflight 뒤 새 격리 DB/output root에서 최소 actual Analyzer 실행과 봉인 직접 판정을 수행한다.
11. Analyzer 합격 뒤 Architect 실제 두 경로를 최소 실행해 GWT/evidence refs를 원문과 대조한다.
12. HANDOFF·작업보고를 결과로 교체하고 불필요한 임시 실행물만 정리한 뒤 요청 범위만 commit/push한다.

## Constitution Check

- I/VI: 구조와 applicable slot은 결정론, 업무 의미는 LLM이며 frame은 shallow/bounded다.
- III: semantic frame이 의미 단일 진실이고 legacy 문자열은 projection이다.
- IV: 누락/grounding 실패는 finding으로 보존하고 조용한 빈 성공을 금지한다.
- V/VII: byte-identical prefix와 검증된 structured channel을 유지한다.
- IX/X: 3층 검증과 source부터 시작하는 원인 추적을 수행한다.
- II/Cost: Framework/DBMS strategy와 병렬 엔진을 보존하고 새 격리 DB/output만 사용한다.

12단계까지 구현·실제 실행·검증을 완료했다. 최종 증거는
`evidence/actual-validation-20260812.md`가 소유한다.
