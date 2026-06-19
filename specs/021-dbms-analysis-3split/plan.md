# Implementation Plan: DBMS 함수분석 3분할 재설계

**Spec**: [spec.md](./spec.md) | **Created**: 2026-06-19 | **Status**: Draft

## 결정 (사용자 확정)

dbms도 framework와 **동일한 3관심사**(①요약 ②테이블 ③비즈니스로직)로 분할하되, **전달은 배치**(framework=노드 1개씩 per-node, dbms=같은 유효부모 형제 여러 개씩 그룹). **부모-자식 흐름(compact_body·effective tree) 유지.** = 현 합본 1콜(깊은 중첩 출력 → 폭주)을 **3개의 얕은 배치콜**로 분리하는 것이 핵심.

## 현 구조 (코드 확정 — 추측 0)

- dispatch: `analyze_function_step.py:135-141` — `strategy.name=="dbms"` → `run_dbms_batch_analysis`. framework는 per-node 3분할(`three_split`).
- 합본: `batch_analyze._batch_call`이 `function_batch.yaml` 1콜 → `BatchAnalysisOutput{nodes:[NodeAnalysis{summary,stereotype,logical_name,rules[],examples[](→affected_tables[]),questions[],table_insights[](→columns[])}]}` (깊은 중첩 = 폭주).
- 배치/스킵: `batch_planner.build_effective_tree`(skip=DECLARE/TRY/TRIGGER_BLOCK 구조노드, LLM제외+그래프보존+자식 bubble) + `plan_batches`(같은 유효부모 형제 토큰버짓 greedy). → **유지(재사용).**
- 테이블 DML 판별: framework `three_split`은 `if ctx.reads or ctx.writes`(=결정론 link_dbms_tables가 채운 R/W=DML노드) 게이트. → dbms 테이블배치도 동일 기준.

## 변경 설계

### 1) `_batch_call` 1콜 → 3 배치콜 (`batch_analyze.py`)
한 배치(node_ids)에 대해:
- **요약배치**: 배치 전 노드 → `function_summary_batch` → 평탄 `{node_key, summary, stereotype, logical_name}[]`.
- **규칙배치**: 배치 전 노드 → `function_rules_examples_batch` → 평탄 `{node_key, rules[], examples[], questions[]}[]`.
- **테이블배치**: 배치 중 **R/W 있는(DML) 노드만** → `function_table_insights_batch` → 평탄 `{node_key, table_insights[]}[]`. R/W 노드 0이면 콜 skip.
- 흐름: framework 패턴 차용 — summary 먼저(cache 워밍) → rules+insights 병렬(prefix 적중). 부분성공 허용, BudgetExceededError만 전파.
- node_key 로 3결과 fan-in 머지 → Function (현 `_apply_node_analysis` 확장: 3소스 머지).

### 2) 출력 모델 (`function_analysis_output.py`)
- `NodeAnalysis`/`BatchAnalysisOutput`(중첩) **폐기**.
- 신설 평탄 배치 모델: `BatchSummaryOutput{nodes:[{node_key,summary,stereotype,logical_name}]}` · `BatchTableInsightsOutput{nodes:[{node_key,table_insights:[TableInsightItem]}]}` · `BatchRulesOutput{nodes:[{node_key,rules,examples,questions}]}`. (TableInsightItem/RuleItem/ExampleItem/QuestionItem 재사용 — framework와 동일 항목.)

### 3) 프롬프트 (`prompts/dbms/`)
- `function_batch.yaml` **폐기** → `function_summary_batch.yaml`·`function_table_insights_batch.yaml`·`function_rules_examples_batch.yaml` 신설. 기존 function_batch.yaml의 dbms 지침을 관심사별로 분배. 입력 user_template = 배치 공통(프로시저·조상골격) + 노드별 블록(node_key·body·R/W). **system byte-identical**(KV prefix, `check_byte_identical`).

### 4) 스킵 (US4) — 현 동작 유지
`build_effective_tree`의 구조노드 스킵(DECLARE/TRY/TRIGGER_BLOCK) 그대로. ★"trivial/빈블록 스킵 확대"는 명확한 판별 규칙 없으면 추가 안 함(추측 금지) — "빈 줄/빈 함수" 요약 남발은 실은 소스경로 BLOCKER(B1) 증상이라 정상경로 재분석 시 해소.

### 5) 데드 폐기 (US5 / §3)
- `function_batch.yaml`·`NodeAnalysis`·`BatchAnalysisOutput` 제거 후 전참조 grep=0.
- table_insights 소비처 = `enrich_table_description_step`(→Table/Column.analyzed_description) 확인됨. 정상경로 재분석서 analyzed_description 채워지는지 검증; 안 채우면 데드 판정.
- run_dbms_batch_analysis 가 batch 1콜 외에 하는 일(EM emit·persist) = framework 경로와 parity 확인(구현 직전 정독).

## Blast Radius (변경/확인 대상 — 심볼변경시 전수 grep)
- analyzer: `batch_analyze.py`(3콜)·`function_analysis_output.py`(모델)·`prompts/dbms/*`(3프롬프트)·`analyze_function_step.py`(dispatch/emit)·`batch_planner.py`(유지 확인)·`enrich_table_description_step`(table_insights 소비).
- 전참조 grep: `NodeAnalysis`·`BatchAnalysisOutput`·`function_batch`·`run_dbms_batch_analysis`.
- framework 회귀 0 확인(three_split·prompts/framework 무변경).
- catalog/architect/frontend: step6 출력은 그래프 노드 속성(summary 등)으로만 흘러 = 라벨/관계 불변이면 영향 0(확인).

## 검증 (SDD analyze + 실행)
- py_compile + import 스모크 + `check_byte_identical`(system diff 0).
- **재분석(정상 소스경로) 1회** → SC-001 length-error 0 · SC-002 누락 0 · SC-003 테이블배치=DML만 · SC-004 스킵노드 LLM0+그래프존재 · SC-005 미사용속성 0 · SC-006 framework 회귀 0. AST·LOGS·그래프 3종 원문대조([[principles]]§6).

## 미해결 선행
- B1(소스경로 이중 RWIS) = 정상 재분석 테스트의 전제(앱 흐름은 정상이나 CLI 깨짐). 테스트는 앱 흐름 or B1 선수정.
