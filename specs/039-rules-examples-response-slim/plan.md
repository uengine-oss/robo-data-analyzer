# Plan: code_rules_examples 응답구조 최소화

## 설계 원칙
LLM은 의미(statement + given/when/then)만 출력. 구조(룰 흐름)는 응답·그래프에서 폐기. examples는 rule 하위 중첩. **공유 심볼 정밀성**: 룰 레벨 NEXT/BRANCH만 제거, 구문(statement) NEXT·PARENT_OF·BpmTask.NEXT·AFFECTS_TABLE·QUESTION 불변.

## 최종 응답 스키마
```
RulesExamplesOutput { rules: [RuleItem], questions: [QuestionItem] }
RuleItem   { statement, coupled_domains, examples: [ExampleItem] }   # rule_id·flow_id 제거, examples 중첩
ExampleItem{ given, when, then, affected_tables: [AffectedTable] }   # rule_id·description 제거
```

## 그래프 계약 변화 (생산자 → 소비자)
- 폐기: `HAS_RULE.flow_id` 속성 · 룰 레벨 `(RULE)-[:NEXT|BRANCH]->(RULE)` 엣지 · `EXAMPLE.description`
- 유지: RULE(`statement`,`coupled_domains`) · EXAMPLE(given/when/then) · `AFFECTS_TABLE` · `HAS_QUESTION`/QUESTION · 구문 NEXT/PARENT_OF · BpmTask.NEXT

## Blast Radius — 파일별 변경 (전수)

### A. analyzer 생산자 (robo-data-analyzer)
1. `data_models/code_analysis_output.py` — RuleItem(remove rule_id/flow_id, add `examples`), ExampleItem(remove rule_id/description), RulesExamplesOutput(top-level `examples` 제거 → rule 중첩), Batch 변형(BatchRulesItem 등) 동일.
2. `prompts/framework/code_rules_examples.yaml` + `prompts/dbms/code_rules_examples_batch.yaml` — 출력 스키마 예시·지침에서 rule_id/flow_id/description 제거, examples 중첩 표기. 공통 system 불변(KV byte-identical).
3. `shared/example_mapping/builder.py` — flow_id 파싱 + 룰 NEXT/BRANCH 도출 로직(94~176) 제거. rule_id 접착제 대신 중첩 구조로 example↔rule 연결.
4. `shared/example_mapping/graph_writer.py` — 룰 NEXT/BRANCH writer 호출 제거, AFFECTS_TABLE·HAS_EXAMPLE 유지.
5. `shared/neo4j/edges/rule_flow.py`, `edges/next.py`(룰용 부분) — 룰 레벨 NEXT/BRANCH upsert 폐기. ★`next.py`가 구문 NEXT도 담당하면 룰 부분만 정밀 제거.
6. `shared/neo4j/edges/has_rule.py` — `flow_id` 속성 SET 제거.
7. `shared/neo4j/rel_types.py` — 룰 NEXT/BRANCH 상수: **구문/BpmTask가 같은 상수 공유 시 삭제 금지**, 사용처만 제거.
8. `shared/workflow/state.py`, `artifact_writer.py`, `analysis_flow/step6/{analyze_code_step,batch_analyze,three_split_llm_fanout}.py` — rules/examples 적재·emit 경로에서 폐기 필드 참조 제거, 중첩 반영.
9. `analysis_flow/step6/rw_table_filter.py` — `examples[*].affected_tables` 접근 경로가 중첩 구조에서도 유효한지 정합(유지).

### B. architect 소비자 (robo-architect)
10. `api/features/ingestion/hybrid/code_to_rules/dbms_rule_linearizer.py` — `hr.flow_id` 정렬키·guard/branch(flow 유래) 참조 제거. 순서=구문 line, 분기=AST(ELSIF/ELSE) 유지.
11. `api/features/ingestion/hybrid/bpm_context_builder.py` — `hr.flow_id` 및 룰 NEXT/BRANCH에서 guard/branch 도출(287-288,298-301,319-321,398-401) 제거 → guard/branch 힌트 빈 값 graceful. statement·affected_tables·coupled_domains·GWT 컨텍스트 유지.
12. `api/features/ingestion/hybrid/code_to_rules/rule_extractor.py` — flow_id/guard/branch 필드 SELECT·매핑 제거.
13. `api/features/ingestion/hybrid/contracts.py` — RuleDTO의 flow_id/guard_rule_id/branch_from/next_rule_local_ids/branch_rule_local_ids 필드 정리(옵셔널 제거 또는 데드화). ExampleDTO.description 제거.
14. `event_storming_bridge/{rule_classifier,decomposer,persistence}.py` — 미배선(데드)이나 flow/branch 참조 코드 정합(데드 제거 원칙 §4).
15. frontend(robo-data-frontend) — 룰 레벨 NEXT/BRANCH 렌더 존재 시 제거(그래프 스토어·엣지타입). 없으면 해당 없음(확인 대상).

## 회귀 가드 (§5 전수 grep 체크리스트)
- `flow_id` grep → 생산·소비 0 잔재
- 룰 `NEXT`/`BRANCH` upsert 호출부 → 0, 단 구문 NEXT·BpmTask.NEXT 호출은 유지 확인
- `description`(example) grep → 0
- `AFFECTS_TABLE`·`HAS_QUESTION` → 재분석 전후 개수 동등

## 검증 (§6 실행)
framework(neo4j·zapamcom)·dbms(test) 재분석 → 그래프 원문 대조: flow_id 0, 룰NEXT/BRANCH 0, description 0, AFFECTS_TABLE·QUESTION·구문NEXT 불변. architect 하이브리드 인제스천 무에러.

## Constitution 체크
신규 스키마 0(속성/관계 제거만), LLM 프롬프트 변경(KV byte-identical 유지), 신규 스킬 0. PASS.
