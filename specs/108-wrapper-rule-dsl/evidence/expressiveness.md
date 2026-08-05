# spec 108 — 표현력 증명 (채택 조건 a) · 완료 증거 (2026-07-30)

## 표현력 증명 (SC-001)

기존 스킬 6종의 패턴 10개 전수 분류 결과와 DSL 표현:

| 원본 패턴 | 판정 | DSL 표현 | 행동 증명 |
|---|---|---|---|
| proframe 본 R/W (`(?<!_s)` lookbehind) | 유효 | wrapper_key + entity_not_ending | test_rule_compiler: 실코퍼스 키 매치·시퀀스 거부 |
| proframe 시퀀스 (`_s_`) | 유효 | wrapper_key + separator="_s_" | 동상 |
| mybatis sqlSession (첫 인자 entity) | 유효 | method_call + first_arg_string | 동상 |
| ofbiz delegator (긴 prefix 우선) | 유효 | method_call (정렬은 컴파일러 보장) | findOne≻find 증명 |
| shopmall 4종 (C 문자열 개행 이음) | 유효 | sql_fragment ×4 | 기존 회귀 코퍼스 전체를 컴파일 경유로 통과 |
| jpa repository / ejb home / mybatis mapper | **entity group 누락 — 죽은 규칙** | method_call + receiver 로 **교정** | 리시버 entity 추출 증명 |
| ejb setter (`.setXxx(`) | **원천 무효** (entity 원천 부재) | 표현 불가가 아니라 규칙 자체 무효 — 제거 + 스킬에 사유 명기 | — |

**결론: 유효 9/9 표현 + 죽은 규칙 4건 교정 + 원천 무효 1건 제거.** 조건 (a) 충족.
raw 정규식 탈출구는 fence·ScanResult 양쪽 유지, 동일 verify 관문 (조건 b — SC-003).

## 검증 결과

- tests/agents/test_rule_compiler.py **13 passed** (표현력 + named group 구조 보장 + 오류 raise)
- 스킬 소비자 정합: load_skill_contract(rules 키 추가)·_apply_skill_contract(컴파일 합류)·
  scan._finalize(합류) — 관련 테스트 72 passed (shopmall 원본 회귀 코퍼스가 DSL 컴파일 경유로 재증명)
- 전량 pytest **631 passed·5 skipped** / 실패 1 = test_wipe_graph (사전 존재 — 106에서 증명)
- 프롬프트 핀: scripts/check_byte_identical.py **OK - 31 prompts** (scan.md 갱신 + 해시 동반)

## 미검증 (정직 기록)

- 실 LLM 에이전트가 DSL 규칙을 실제로 채우는 품질 — 세션 내 LLM 런타임 미가용.
  실기동 시: Framework 프로젝트 /analyze → scan 로그에서 table_access_rules 채움 여부 +
  verify 통과율 확인.
