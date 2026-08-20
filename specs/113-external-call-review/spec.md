# Feature Specification: external 호출 LLM 재검증 — 후보 필터 + 결정론 게이트

**Created**: 2026-07-30 | **Status**: Superseded by spec 131 C-047 (removed)

> 2026-08-21 actual evidence: 모델이 네 항목의 reason에 “실제 호출일 수도 있어 확신이
> 부족하므로 드롭하지 않음”이라고 썼지만, 구조상 `drops[]`에 들어간 것만으로 21 routine의
> 실제 CALLS 46개를 삭제했다. 이름/비율 게이트는 내용 모순을 막지 못했고, LLM이 canonical
> 호출 그래프를 삭제하는 권한 자체가 원칙 위반이라 spec 131 C-047 슬라이스에서 기능 전체를
> 제거했다. 이후 이 문서는 유입 이력만 보존한다.

## 무엇/왜
이름 매칭이 external 로 뭉갠 호출(미제공 헤더의 매크로 등 가짜 포함)을 LLM 이 심사해
확신하는 가짜만 드롭. 문헌(SEA류) 배치 그대로 — **정밀 확정된 internal 은 불가침**,
LLM 은 external 잔여의 필터로만.

## 결정론 게이트 (LLM 판정은 이 관문 통과분만)
1. 프로젝트 정의 루틴 드롭 시도 → 무시 + 경고
2. 목록 밖 이름(환각) → 무시 + 경고
3. 드롭 비율 > 상한(`ROBO_AGENT_CALL_REVIEW_MAX_DROP_RATIO`, 기본 0.5) → **전체 무시** + 경고
4. fail-open: 에이전트 실패 = 전량 유지 (필터는 정화기 — 데이터 손실 금지)

설정: `ROBO_AGENT_CALL_REVIEW_ENABLED`(기본 true) / `…_MAX_CALLS`(3).
프롬프트 `agents/prompts/call_review.md` — 해시 핀 등록 (32 prompts OK).
배선: link_calls_by_name_step 내부, upsert 전 (Calls 단일 생산자 유지). FW 레인 전용.

## 검증 / 미검증
tests/agents/test_call_review_gate.py **5 passed** (게이트 4계약 + 빈 목록 무호출).
**미검증(정직)**: 실 LLM 판정 품질 — 실기동 시 드롭 목록·비율 로그로 확인, 과도 드롭 시
ENABLED=false 롤백 또는 상한 하향.
