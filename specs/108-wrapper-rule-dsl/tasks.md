# Tasks: wrapper 규칙 DSL

- [x] T201 계약: 3종 rule 모델 + ScanResult.table_access_rules (기본 [])
- [x] T202 컴파일러: 규칙→call_regex (named group 구조 보장·형태 오류 raise)
- [x] T203 **표현력 증명** — 유효 기존 9패턴 전부 DSL 표현 + 원본 행동 동일성 테스트
      (proframe `_s` lookbehind 거부 포함) → evidence/expressiveness.md (SC-001 — 조건 a)
- [x] T204 scan._finalize 합류 — rules 컴파일 → patterns 병합 → 기존 verify 단일 관문 (raw 탈출구 = SC-003)
- [x] T205 스킬 6종 이사 — entity 누락 4건 교정·원천 무효 1건 제거(사유 명기)
- [x] T206 scan.md 지침 갱신 + prompt_hashes.json 동반 (SC-005)
- [x] T207 전량 pytest + 종료 감사 (+실 에이전트 기동은 미검증 기록)

완료 증거: evidence/expressiveness.md (2026-07-30)
