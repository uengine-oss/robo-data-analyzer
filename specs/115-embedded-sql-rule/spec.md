# Feature Specification: embedded_sql — wrapper DSL 제4형 (울타리 블록 SQL)

**Created**: 2026-07-30 | **Status**: Implemented (동세션) | **출처**: 딥리서치 3차 보류→사용자 전체 반영 지시

## 무엇

Pro*C/COBOL/ABAP 류 "구분자로 둘러싸인 SQL 블록"(`EXEC SQL … END-EXEC`)을 잡는 4번째
닫힌 템플릿. 에이전트는 delimiter 2칸만 채우고, 블록 안 R/W·dml_type 판정은
**DML 의미표(spec 106)가 그대로** 수행 — R/W 로직 중복 0 (단일 진실 유지).

## 계약

- `EmbeddedSqlRule(kind="embedded_sql", id, delimiter_start, delimiter_end)` — op_mappings 없음
  (블록 안은 진짜 SQL — 의미표가 판정).
- `split_rules()`가 (컴파일 대상, embedded)로 분리 — `compile_rule(embedded)`는 명시 오류.
- `RegexTableLinker.link(embedded_rules=…)` — 블록 추출 → first_statement 판정(비 DML 블록
  skip) → write/read 대상 추출 → 단어장 해소. **검문소 3지표(spec 114) 동일 계수**.
- 잔여 큐(unresolved_cases)는 정규식 패턴 영역 유지 — embedded 미해소는 지표 계수만.
- 소비 배선: scan `_finalize`(verify 포함) · `_apply_skill_contract` · `link_deterministic_step`.
- 프롬프트 scan.md 3종→4종 + prompt_hashes.json 동반 (게이트 OK).

## 알려진 한계 (정직 기록)

- 코퍼스 실측 0건 (grep "EXEC SQL" = 0) — **미래 고객 대비 선반영** (사용자 지시).
- COBOL/ABAP은 호스트 언어 파서가 없어 코드노드가 안 생김 → 텍스트 매치는 돼도 caller 미해소.
  Pro*C(.pc)도 ANTLR C 파서 통과 여부가 실전 선행 조건. **실기동 검증 항목.**

## 검증 (2026-07-30)

`tests/pipeline/step5_linking/test_embedded_sql_rules.py` **6 passed** — COBOL UPDATE=WRITES /
Pro*C SELECT+TRUNCATE / 비 DML 블록 skip / 단어장 모순 계수 / split·compile 계약.
전량 pytest green (아래 종합 증거 참조).
