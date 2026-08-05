# Feature Specification: 검문소 3지표 — support / contradiction / ambiguity

**Created**: 2026-07-30 | **Status**: Implemented (동세션) | **출처**: 딥리서치 3차 유일 채택안

## 문제 (구멍 재현)

현행 `verify_table_patterns` 통과 조건 = 매치≥1 · 엣지≥1 · 부분추출0 · op인식률≥θ.
→ **넓게 오탐하는 규칙이 "운 좋은 엣지 1개"로 통과 가능** (매치 50 중 진짜 1·쓰레기 49
여도 통과 — 쓰레기 49는 미해결 큐를 오염). 실사고 기록은 없음 — 예방 공사.
또한 `_resolve_table`이 fallback prefix 복수 해석(T_USER·TB_USER 공존) 시 **첫 해석을
조용히 선택** — 어느 쪽인지 아무도 모름.

## FR

- FR-001: 완전한 entity 매치인데 단어장(DDL∪실DB)에 없는 건수(`dict_misses`)와
  복수 해석 건수(`ambiguous_sites`)를 `LinkOutcome`에 계수한다.
- FR-002: 통과 조건에 contradiction_ratio ≤ θc(기본 0.8) · ambiguity_ratio ≤ θa(기본 0.2)
  추가. 임계는 `ROBO_AGENT_SCAN_REGEX_MAX_{CONTRADICTION|AMBIGUITY}_RATIO`.
- FR-003: **엣지 생성 동작 불변** (첫 해석 선택 유지) — 판정만 강화. 회귀 0.
- FR-004: verdict summary에 모순·중의 비율 표기 (탈락 사유 표면화).
- 적용 범위: FW 스캔 규칙 + 잔여 패턴 루프 (같은 관문). DBMS 레인은 역방향 매칭이라
  구조적으로 해당 없음 — 로직은 레인·언어 무관 (범용성 게이트 충족).

## 검증 (2026-07-30)

- `tests/pipeline/step5_linking/test_verify_three_indicators.py` **4 passed**:
  구멍 재현(모순 90% 탈락) / 정밀 규칙 회귀 통과 / 중의 100% 탈락 / 첫 해석=종전 선택.
- 전량 pytest **652 passed·5 skipped** / 실패 1 = 사전 존재(test_wipe_graph).
