# Implementation Plan: wrapper 규칙 DSL

**Branch**: `main` | **Date**: 2026-07-30 | **Spec**: [spec.md](./spec.md)

## Summary

스캔 에이전트 산출물을 3종 닫힌 템플릿(wrapper_key / method_call / sql_fragment)으로 구조화.
컴파일러가 규칙 → `call_regex` 를 기계 생성해 **기존 TableAccessPattern 경로에 합류** —
검증기·링커·잔여 루프 무변경. raw 정규식은 탈출구로 유지(동일 관문).

## Research 결정

- **D1 닫힌 템플릿 3종, 조합 문법 금지**: 기존 10패턴 전수가 3형태로 수렴함을 실측 —
  임의 조합 DSL은 정규식 재발명(미니 언어 함정). 새 형태는 별도 spec 으로만 추가.
- **D2 컴파일러 홈 = `agents/rule_compiler.py`**: 소비자가 scan `_finalize`(agents 계층) —
  linking 은 컴파일 결과(패턴)만 본다 (헌법 VIII).
- **D3 named group 구조 보장**: 템플릿 골격에 `<entity>`·`<op_token>` 이 박혀 있어 에이전트가
  빠뜨릴 수 없다 — jpa/ejb/mybatis 죽은 규칙 사고의 구조적 재발 방지.
- **D4 스킬 이사 시 교정**: entity 누락 4패턴은 receiver-entity 로 교정(스킬 문서의 의도 그대로),
  ejb setter 는 entity 원천 부재로 제거(사유 명기). "원본과 행동 동일" 증명 대상은 유효 9패턴.
- **D5 프롬프트 핀**: `agents/scan.md` 변경은 `llm/prompts/prompt_hashes.json` 동반 갱신 (헌법 V).

## Constitution Check

I 결정론 ✅ (컴파일 결정론, LLM 역할 축소) / II 분기0 ✅ (전략 무관) / III 단일진실 ✅
(규칙→패턴 변환기 한 곳, 검증 관문 기존 한 곳) / IV ✅ (컴파일 실패 raise) /
V ⚠️ scan.md 해시 동반 갱신 / VI ✅ (rules 스키마는 평탄한 필드 — 자유문 없음) /
VII ✅ (submit 패턴 유지) / VIII ✅ (D2). Cross-Service: 없음 (analyzer 내부).

## Source

agents/contracts.py [수정: 3 rule 모델 + ScanResult.table_access_rules]
agents/rule_compiler.py [신설] / agents/scan.py [수정: _finalize 합류]
agents/prompts/scan.md + llm/prompts/prompt_hashes.json [동반 갱신]
agents/skills/framework/*/SKILL.md [6종 이사] / tests/agents/test_rule_compiler.py [신설]
