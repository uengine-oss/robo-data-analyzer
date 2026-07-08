# Implementation Plan: 규칙 추출 노드 게이트 + rules_examples 프롬프트 재탄생

**Spec**: [spec.md](./spec.md) | **Status**: Draft

## 설계 원칙 (검증된 현행 구조 위에서)

- 근본해결 = 구조 정리: 이미 있는 **table_insights 게이트(`if ctx.reads or ctx.writes`)와 대칭**으로 rules 게이트를 둔다. 분기 덧대기가 아니라 비대칭 해소.
- 단일진실(DRY): "규칙 대상 노드타입" 분류는 `ontology_roles.yaml` 한 곳.
- 손실 0: bottom-up + `compact_body`(자식→[줄범위]summary)가 이미 존재 → 리프 규칙 생략해도 부모가 흡수.
- 순수 결정론: 게이트는 노드타입 비교(LLM 0).

## US1 (P1) — rules 게이트 (DBMS 전용)

**근거(원문 확인)**: `three_split_llm_fanout.py:174` rules는 무조건, `:183` insights는 `if reads/writes`. CodeNode의 antlr type = `fn.label`. `strategy.name`∈{framework,dbms}. RWIS AST 전수로 DBMS 문장타입 확정.

**변경 3곳:**

1. **`shared/lang/ontology_roles.yaml`** — 신규 섹션 추가 (roles·stereotype 풀 안 건드림):
   ```yaml
   rule_bearing:   # rules_examples 추출 대상 문장타입 (DBMS 전용 — framework는 함수단위라 항상 대상)
     dbms: [PROCEDURE, FUNCTION, TRIGGER, IF, ELSIF, ELSE, LOOP, SELECT, INSERT, UPDATE, DELETE, MERGE, EXCEPTION]
   ```
   - 제외(게이트 skip): CALL·ASSIGNMENT·COMMIT·ROLLBACK·DECLARE·CURSOR_VARIABLE·OPEN/FETCH/CLOSE_CURSOR·EXIT·RETURN·TRY·UNION_ALL·JOIN·PARAMETER·VARIABLE·FILE·TRIGGER_BLOCK.
   - 판단 확정: UNION_ALL/TRY/JOIN = 결정 아닌 구조·구성 → skip(자식 SELECT가 규칙 보유). EXCEPTION = 도메인 silent-pass 담을 수 있어 포함(로깅뿐이면 프롬프트가 []).

2. **`shared/lang/ontology.py`** — 신규 함수:
   ```python
   @lru_cache(maxsize=None)
   def is_rule_bearing(node_type: str, strategy: str) -> bool:
       """rules_examples 추출 대상 노드인지. framework=항상(함수단위), dbms=결정노드만."""
       if strategy != "dbms":
           return True
       return node_type in frozenset(_load().get("rule_bearing", {}).get("dbms") or [])
   ```
   - framework 무조건 True → FR-004/SC-005(무영향) 보장. dbms만 화이트리스트.

3. **`three_split_llm_fanout.py`** — rules 태스크를 게이트로 감쌈 (insights와 대칭):
   ```python
   tasks = {}
   if is_rule_bearing(fn.label, strategy.name):
       tasks["rules"] = _track_split("split_rules", run_yaml_rule(rule_name="code_rules_examples", ...))
   if ctx.reads or ctx.writes:
       tasks["insights"] = ...
   ```
   - `rules_out = _take(done, "rules", ...)` 는 키 없으면 None → `fn.extra["rules"]=[]`·`ctx.extra["rules"]=[]` (기존 insights-skip과 동일 경로, 무회귀).
   - import `from shared.lang.ontology import coerce_stereotype, is_rule_bearing`.

**검증(SC-001/002/005)**: RWIS 재분석 → ASSIGNMENT 등 리프 규칙 0, FN_EDIT_YN 규칙수 급감·도메인 결정 부모에 보존(원문대조). zapamcom 재분석 → 함수별 규칙 분포 불변.

## US2 (P2) — rules_examples 프롬프트 재탄생 (파일 2개 각각, 안 합침)

**대상(별도 유지)**: `prompts/framework/code_rules_examples.yaml`, `prompts/dbms/code_rules_examples_batch.yaml`. `_common.yaml` `system`은 KV byte-identical 유지(FR-008) — 재작성은 각 분할 `user_template_specific`에 한정, 단 _common §단계1~7 중 rules 전용으로 옮길 주제(주석처리 등)는 이관 후 _common에서 중복 제거(3분할 동일성 유지 범위 내).

**주제별 단일 섹션 구조(초안 — tasks에서 확정):**
1. 목적·품질기준 (완전성 vs 무폭주)
2. 규칙 판단 (도메인 가치 결정트리 / 기술노이즈 제외)
3. **주석 처리 (단일 통합 섹션)** — 죽은코드(commented-out)=무시 / 노트=형태·의미로 유용성 판단·활성코드 우선·모순 시 question. (현 _common §단계7 + 분할 §6.10 통합)
4. 코드값 매핑 (집합 완전성·값→결과·DECODE default·부호 곱)
5. 부모-자식 경계 (게이트 후 전제: 결정 노드만 옴 → 자식 재추출 금지 간결화)
6. 도메인 언어 (코드 토큰 블랙리스트 단일화)
7. 종료 경로 (silent pass)
8. questions (0=최고, 가드·else=답)
9. 워크드 예시 (범용·프로젝트 식별자 0)

**원칙**: 같은 규칙이 두 곳에 안 나옴(FR-006). 게이트가 구조를 정리해 "사소 리프 skip" 류 지침은 프롬프트에서 축소 가능(중복 제거).

**검증(SC-003/004/006)**: 재분석 → 죽은코드 환각 0·기존개선 회귀 0. 프롬프트 자체 = 주제 중복 위치 0·범용.

## Blast Radius

- `is_rule_bearing` 신규 → 호출부 = three_split_llm_fanout 1곳. `rule_bearing` yaml 키 신규 → 소비 = ontology.py 1곳.
- `ctx.extra["rules"]`/`fn.extra["rules"]` 빈 배열 소비처: `_format_child_business_logic`(빈=무시), Neo4j 영속(규칙 없으면 RULE 노드 0 — insights-skip 선례와 동일), example_mapping 빌더(빈 규칙=엣지 0). 회귀 없음.
- 프롬프트 편집 = user_template_specific(휘발), _common `system` diff 0 유지.

## 검증 절차 (SDD §6 데이터검증)

1. FR-1 구현 → RWIS 재분석(`test`) → SC-001/002 + zapamcom(`neo4j`) → SC-005.
2. FR-3 구현 → 양쪽 재분석 → SC-003/004 원문 대조.
3. 컴파일·import 확인, KV byte-identical(`_common` diff 0).
