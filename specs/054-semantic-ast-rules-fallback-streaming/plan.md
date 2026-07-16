# Implementation Plan: 의미 AST 뼈대 보존 · Rules/Examples 폴백 · 스트림 가시성

**Branch**: `054-semantic-ast-rules-fallback-streaming` | **Date**: 2026-07-15 | **Spec**: [spec.md](./spec.md)

## Summary

세 갈래를 한 스펙으로 묶는다: (US1) rules/examples 프롬프트 감사·재구성(우선), (US2) parser가 만든 제어문/
호출 뼈대를 그래프에 보존하되 분석 대상에서 분리, (US3) 품질 이상 시 결정론 AST 예산 분할→조각분석→병합
LLM 폴백, (US4) 폴백·약한 구간 스트림 보강. 언어 무관 로직이라 C-first 파서 위에서 전부 완성한다.

## Technical Context

**Language/Version**: Python 3.11+, LangGraph 파이프라인
**Primary Dependencies**: 기존 `analysis_flow/*`, `shared/*`, `data_models/*`. 신규 외부 의존 없음(AST 예산은
문자 길이 근사 — tokenizer 도입 안 함).
**Storage**: Neo4j (기존 upsert 경로 재사용)
**Testing**: `pytest` (기존 `test_*.py` 관례) + 보존된 `_runs/prompt-iteration-3/` 회귀 표본
**Project Type**: 단일 프로젝트(분석 파이프라인)
**Performance**: 정상 경로 성능·KV캐시 히트 불변(폴백은 이상 시에만). 제어문 노드로 인한 LLM 추가 호출 0.
**Constraints**: 원칙 I(결정론), IV(silent fail 금지), V(KV캐시 byte-identical), VI(출력 스키마 절제)
**Scale/Scope**: reader/ontology + 분석대상 분리 + 품질검사기 + 분할기 + 조각/병합 러너 + 스트림 + 프롬프트
재구성 + 스키마 캡 조정.

## Constitution Check

- **I. 결정론 우선**: 품질검사기·AST 예산 분할기는 LLM 0(문자 근사·정규화·재귀). 분할 계획에 LLM 미사용.
  **PASS**.
- **IV. Silent Failure 금지**: 폴백 각 실패(조각/병합/재검사)는 log_error + stream error + raise/명시적
  실패결과. 재검사 이상은 run을 죽이지 않되 "이상 플래그 + error 스트림"으로 표면화. **PASS(설계로 충족)**.
- **V. KV캐시 byte-identical**: 조각/병합 프롬프트는 기존 rules 지침 재사용 + 얇은 병합 지시. `_common.yaml`
  공통 prefix 불변. 프롬프트 재구성 후 `prompt_hashes.json` 갱신 + `byte_identical` 가드. **PASS**.
- **VI. 출력 스키마 절제**: examples는 자연어 단일 문자열 유지(과거 4·5차 폭주 회피). max_length는 캡만
  상향(30→80), 구조 심화 없음. **PASS**.
- **III. 단일 진실**: `analysis_targets`가 "실제 함수" 단일 진실. 관계명은 기존 `rel_types.py` 재사용. **PASS**.

## Project Structure

```text
specs/054-semantic-ast-rules-fallback-streaming/
├── spec.md
├── plan.md                    # 이 문서
├── contracts/
│   └── stream-events.md       # 폴백/보강 processing 이벤트 계약 (entity_kind, sub_action, status)
├── research.md                # 임계값·예산 시작값 근거 + coverage matrix 초안
└── tasks.md

# 변경/신규 소스 (실제 경로)
shared/lang/ontology_roles.yaml                         # + control_structure role (C 제어문 5타입)
analysis_flow/step2_load_ast/antlr_json_reader.py       # _walk_framework: owner_routine 스레딩 + 제어문 재귀
analysis_flow/step2_load_ast/load_ast_step.py           # graph_nodes vs analysis_targets 분리
data_models/analysis_context.py                         # 분석대상 집합 + 함수별 control_subtree 실어 나르기
data_models/code_analysis_output.py                     # RuleItem.examples max_length 30→80 + docstring
analysis_flow/step6_analyze_code/analyze_code_step.py   # all_ids/cycle/round → analysis_targets 만
analysis_flow/step6_analyze_code/three_split_llm_fanout.py # 품질검사 훅 + 폴백 오케스트레이션
analysis_flow/step6_analyze_code/rule_quality.py        # 신규: 결정론 품질검사기
analysis_flow/step6_analyze_code/ast_budget_splitter.py # 신규: 결정론 AST 예산 분할기
analysis_flow/step6_analyze_code/rules_fallback.py      # 신규: 조각분석 + 병합 러너
prompts/framework/code_rules_examples.yaml              # 재구성 (WIP 감사)
prompts/framework/code_rules_merge.yaml                 # 신규: 병합 전용 얇은 지시 (rules 지침 재사용)
prompts/dbms/code_rules_examples_batch.yaml             # 공통 원칙 반영
cli_scripts/prompt_hashes.json                          # 해시 갱신

# 신규 테스트
analysis_flow/step2_load_ast/test_control_structure_walk.py
analysis_flow/step6_analyze_code/test_rule_quality.py
analysis_flow/step6_analyze_code/test_ast_budget_splitter.py
analysis_flow/step6_analyze_code/test_analysis_target_isolation.py
```

**Structure Decision**: 결정론 로직(품질검사·분할)은 각각 독립 모듈로 분리해 단위 테스트 가능하게 한다.
폴백 오케스트레이션만 `three_split_llm_fanout.py`의 rules 채널 직후에 훅으로 붙인다(기존 정상 경로 코드는
분기 없이 그대로 통과).

## 핵심 설계 (Phase 1 결론)

### 1) reader: owner_routine 스레딩 (US2)

`_walk_framework(node, ctx, module_id, parent_fid)` → `owner_routine_id`를 인자로 추가.

```python
def _walk_framework(node, ctx, module_id, parent_fid, owner_routine_id):
    for child in node.get("children") or []:
        role = role_of(child.get("type",""), ctx.lang)
        if role == "control_structure":
            cid = _make_structural_node(child, parent_fid, owner_routine_id, ctx)  # graph_nodes만
            _walk_framework(child, ctx, module_id, parent_fid=cid,
                            owner_routine_id=owner_routine_id)          # 함수 불변
        elif role == "internal":                                        # FUNCTION_CALL/NEW_INSTANCE/IMPORT
            _collect_internal(child, owner_routine_id)                  # CALLS/RW 소유자 = 함수
            # (구조 노드로도 그래프에 보존 — 결정론 필드만)
        elif role == "function":
            fid = _make_code_node(child, ...)                           # graph_nodes + analysis_targets
            _walk_framework(child, ctx, module_id, parent_fid=fid, owner_routine_id=fid)
```

- `_make_structural_node`는 `CodeNode`를 만들되 LLM 필드는 손대지 않는다(기본 None). `code_text`는 라인
  범위로 슬라이스(기존 `code_text` 채우기 로직 재사용).
- 함수별 제어문 서브트리(각 노드 type/start/end/부모)는 `AnalysisContext.extra["control_subtree"]`에 실어
  step6 분할기가 그래프 재조회 없이 쓰게 한다.

### 2) graph_nodes vs analysis_targets 분리 (US2)

`load_ast_step`이 `state["code_nodes"]`(전부)와 `state["analysis_target_ids"]`(함수만)를 분리 산출.
step6와 downstream은 `analysis_target_ids`만 본다. cycle/round의 엣지 목록에서 제어문 PARENT_OF를 제외
(또는 `all_ids ∩ analysis_targets`로 자연 필터). **downstream 전수 감사 대상**: 검색색인, `summarize_module`,
`health_metrics`, `build_components`, glossary — 각각 순회 소스를 `analysis_target_ids`로 확인.

### 3) 품질검사기 (US3, 결정론)

`rule_quality.check_rule_quality(rules_out, code_body) -> RuleQualityReport`:
정규화 튜플 `(given,when,then,affected_tables)` 중복 비율, 최다 반복 그룹 크기, 캡 도달, best-effort 원문
미연결 신호. 임계 시작값: 중복 20%+, 반복 3회+, 캡 도달 → 이상.

### 4) AST 예산 분할기 (US3, 결정론)

`ast_budget_splitter.split(control_subtree, code_lines, budget) -> list[CodeFragment]`:
서브트리가 예산 안이면 통째 한 조각, 초과 시 자식으로 재귀. if 조건+몸통 불가분(같은 서브트리), then/else
불합침(다른 서브트리), 작은 함수 미분할. 예산 = 문자 길이 근사(시작값 ~4천 토큰 상당, research.md).

### 5) 조각분석 + 병합 (US3)

`rules_fallback.run_rules_fallback(ctx, report, fragments, code_body) -> RulesExamplesOutput`:
각 조각을 기존 rules LLM 채널로(시그니처+조상 조건경로 포함) 분석 → 병합 LLM 1회(`code_rules_merge.yaml`)
→ 재검사. 실패 계약은 FR-023.

### 6) 훅 위치 (three_split_llm_fanout.py)

rules LLM 호출 직후:
```python
rules_out = await call_rules_channel(...)
report = check_rule_quality(rules_out, code_body); emit(quality)
if report.is_anomaly:
    emit(Fragment, fallback, start)
    frags = split(ctx.extra["control_subtree"], code_lines, BUDGET)
    rules_out = await run_rules_fallback(ctx, report, frags, code_body)
    report2 = check_rule_quality(rules_out, code_body)
    if report2.is_anomaly:
        rules_out = deterministic_dedupe(rules_out); rules_out.anomaly_flag = True; emit(failed)
    else: emit(done)
# 정상이면 블록 통째 skip → 기존 경로 그대로
```

## 마이그레이션·롤백

- 순수 additive + 기본 경로 무분기. 롤백은 폴백 훅 제거 + role 추가 되돌림으로 가능.
- 기존 Neo4j 데이터: full-rebuild 파이프라인이라 재분석 시 자연 반영(별도 마이그레이션 불필요).

## 위험과 완화

- **downstream 소비자 누락**: 하나라도 `code_nodes` 전체를 순회하면 제어문이 함수처럼 샌다 → FR-013
  전수 감사 + `test_analysis_target_isolation.py`로 "구조 노드가 검색/요약/health 결과에 0건" 회귀 고정.
- **owner_routine 스레딩 실수**: internal 노드가 IF를 소유자로 잡으면 CALLS 오염 → `test_control_structure_walk.py`
  에서 IF 아래 호출의 CALLS 출발점이 함수인지 assert.
- **예산 임계 오설정**: 정상 함수가 폴백 타거나 이상 함수가 안 탐 → 회귀 표본(load_target_orders/
  get_code_name/calc_discount)으로 시작값 검증·조정, 조정 결과를 research.md·최종 보고에 기록.
- **프롬프트 재구성 중 캐시 깨짐**: `_common.yaml` prefix byte 변경 시 캐시 붕괴 → 변경 후 실제 사용량
  로그로 cache_read 확인, `byte_identical` 가드 통과 필수.

## Complexity Tracking

*Constitution Check 위반 없음 — 비워둔다.*
