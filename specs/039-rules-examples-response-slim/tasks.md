# Tasks: code_rules_examples 응답구조 최소화

## Phase 1 — analyzer 생산자 (그래프 계약 원천) ✅ 완료·검증
- [x] T1. `data_models/code_analysis_output.py`: RuleItem(rule_id/flow_id 제거, `examples` 중첩), ExampleItem(rule_id/description 제거), RulesExamplesOutput(top-level examples 제거), BatchRulesItem 동일.
- [x] T2. `prompts/framework/code_rules_examples.yaml` + `prompts/dbms/code_rules_examples_batch.yaml`: 출력스키마·필드지침·flow_id예시·footer 정합, 중첩 반영. system(_common) byte-identical. YAML 유효.
- [x] T3. `builder.py`: flow_id/NEXT/BRANCH 도출 제거, 중첩 example 소비. `graph_edges.HasRule`(local_rule_id/flow_id 제거)·`graph_nodes.Example`(description 제거).
- [x] T4. `graph_writer.py`: 룰 NEXT/BRANCH writer 호출·import 제거.
- [x] T5. **데드 완전제거(흔적0)**: `edges/rule_flow.py` 파일 삭제 · `graph_edges.Next/Branch` dataclass · `state.next_edges/branch_edges` + import · `artifact_writer` 키 · `rel_types.BRANCH` 상수 · `has_rule.py` flow_id/local_rule_id SET · `example.py` description SET · 주석/도크스트링 흔적 5곳. 구문 NEXT(StatementNext)·BpmTask.NEXT 불변.
- [x] T6. `analyze_code_step`·`batch_analyze`·`three_split_llm_fanout`·`rw_table_filter`: emit·fan-in·필터 중첩 반영.
- [x] T7. 전수 grep: 제거심볼 잔재 **0** · py_compile 16파일 · import 체인 · builder 실동작 · 단위테스트 8/8 통과.

## Phase 2 — architect 소비자 (계약 정합)
- [ ] T8. `code_to_rules/dbms_rule_linearizer.py`: flow_id 정렬키·guard/branch(flow) 제거. 순서=구문line, 분기=AST(ELSIF/ELSE).
- [ ] T9. `bpm_context_builder.py`: flow_id·룰NEXT/BRANCH guard/branch 도출 제거 → 힌트 graceful 빈값. statement·affected_tables·coupled_domains·GWT 유지.
- [ ] T10. `code_to_rules/rule_extractor.py`·`contracts.py`: flow_id/guard/branch/description 필드 정리.
- [ ] T11. `event_storming_bridge/{rule_classifier,decomposer,persistence}.py`: 데드 참조 정합(§4).
- [ ] T12. frontend 룰흐름 렌더 확인 → 존재 시 제거.

## Phase 3 — 검증
- [ ] T13. framework(neo4j)·dbms(test) 재분석.
- [ ] T14. 그래프 원문 대조: flow_id 0·룰NEXT/BRANCH 0·description 0 / AFFECTS_TABLE·QUESTION·구문NEXT 불변.
- [ ] T15. architect 하이브리드 인제스천 무에러 확인.
