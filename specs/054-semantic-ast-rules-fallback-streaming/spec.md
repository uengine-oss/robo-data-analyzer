# Feature Specification: 의미 AST 뼈대 보존 · Rules/Examples 이상 복구 폴백 · 전체 스트림 가시성

**Feature Branch**: `054-semantic-ast-rules-fallback-streaming`

**Created**: 2026-07-15

**Status**: Draft

**Input**: `docs/핸드오프/2026-07-15-framework-의미AST-규칙예시-폴백-스트림-핸드오프.md`.
상위 계약: `antlr-code-parser/specs/007-semantic-control-flow-ast/contracts/ast-node-vocabulary.md`.
하위 소비: `robo-data-frontend/specs/055-semantic-ast-visibility/`.

## 동기와 인과 (거꾸로가 아니라 순서대로)

```
① 진짜 문제: 프레임워크 rules/examples 3분할 분석의 정확도가 나쁨
             (같은 규칙 수십 회 반복, 환각, 100초+ 폭주)
② 해결책:    함수를 쪼개서(split) → 조각별 분석 → 병합(merge) 하면 정확도↑
③ 필요조건:  "쪼개려면" 어디서 자를지 기준 구조가 있어야 함
④ 그 구조 =  제어문(if/for/switch) AST 뼈대
⑤ 원칙:      이 뼈대는 분석에 절대 불참. 프레임워크 분석은 여전히 함수 단위.
```

즉 제어문 AST 보존은 **목적이 아니라 수단**이다. 정상 함수는 지금처럼 통째로 1회 분석하고, **품질 이상이
감지될 때만** 이 뼈대를 꺼내 결정론적으로 자른다.

## 이번 라운드 범위

- 파서는 **C 언어만** 제어문 AST를 emit한다(spec 007). 이 스펙의 분석기 로직(뼈대 보존·폴백·프롬프트·
  스트림)은 **언어 무관**이므로 이번에 완성한다 — C로 돌려 정확도 개선을 확인하고, Java/Python은 파서
  리스너 추가 후 그대로 재사용된다.
- 노드 어휘·분류·필드 계약은 이 스펙이 정의하지 않고 `ast-node-vocabulary.md`(세 저장소 공유)를 참조한다.

## 배경과 현재 사실 (조사 완료)

- **뼈대 소실 회귀 지점**: `antlr_json_reader._walk_framework()`는 자식 role이 `_FW_DISPATCH`에 없으면
  handler가 `None`이라 그 자식과 자식의 자식 전체를 순회하지 않는다(`antlr_json_reader.py:182-189`). spec
  007이 emit할 `IF/ELSE/LOOP/SWITCH/CASE`를 지금 그대로 두면 그 아래 `FUNCTION_CALL`까지 통째로 사라진다.
- **owner vs structural parent**: `CodeNode`는 이미 `owner_id`(소속 모듈, 불변)와 `parent_id`(구조 부모)를
  분리한다(`graph_nodes.py:175-177`). DBMS `_walk_dbms`는 nested 구조 노드(DECLARE/TRY 등)를 이미 CodeNode로
  보존한다(주석: "구조 노드도 노드로 보존"). 그러나 CALLS/READS/WRITES를 "가장 가까운 함수"에 귀속시키는
  `owner_routine_id` 스레딩은 Framework에 아직 없다 — 지금까진 함수 직속이라 "구조 부모 = 소유 함수"가
  우연히 성립했다. 제어문이 사이에 끼면 깨진다.
- **분석 대상 = 그래프 노드 (미분리)**: `load_ast_step.py`는 `code_nodes`를 단일 평면 리스트로 만들어
  그래프에 upsert하고, 그 목록이 그대로 step6 `contexts`가 되어 LLM 분석 대상이 된다. "그래프엔 있으나
  분석 대상은 아닌 노드" 개념이 없다.
- **CodeNode 필드 그룹이 이미 경계와 일치**: `graph_nodes.py`의 `code_text`/`owner_id`/`parent_id`/`name`/
  `comment`/`return_type`는 "load_ast가 채움"(AST 단계), `summary`/`stereotype`/`logical_name`/`search_text`/
  `domain_terms`/`embedding`는 "analyze_code가 채움"(LLM 단계). → 구조 노드는 **LLM 필드가 None으로 남는
  CodeNode**로 자연 표현된다.
- **분석 순서 오염 경로**: `run_framework_per_node_analysis()`는 `CALLS + PARENT_OF` 엣지를 합쳐
  `find_call_cycles`/`bottom_up_rounds`에 넘긴다(`analyze_code_step.py:189-207`). `all_ids`는 이미
  `contexts.keys()`로 한정돼 있어, 제어문을 `contexts`에 안 넣으면 자동 안전해지지만 **코드가 강제하지
  않으면** 다음 사람이 넣을 수 있다.
- **rules/examples 프롬프트에 지침이 이미 대량 누적**: `prompts/framework/code_rules_examples.yaml`에 현재
  사용자 WIP 202줄이 추가돼 있다(의미적 유일성 계약, 절대우선 판정 게이트, 매핑형 함수 처리, 교차-Rule
  중복 금지 등). 이 스펙은 "또 한 단락 추가"가 아니라 **전체 감사 후 재구성**이다.
- **max_length=30 충돌**: `RuleItem.examples`·`RulesExamplesOutput.rules`·`BatchRulesItem.rules`가 모두
  `max_length=30`이다(`code_analysis_output.py:173-188, 207, 253`). 문제의 `load_target_orders`는 Rule 2개
  × Example 30개 = 60개로 정확히 이 상한에 닿아 있었다 → 상한이 실제로 잘라내고 있었을 가능성. 프롬프트의
  "절대 상한 없음" 요구와 문면 충돌.
- **스트림 스키마**: `shared/log/processing_progress.py`가 `{step, entity_kind, entity_id, sub_action,
  status}` 단일 이벤트와 `track_processing()`(start→done|failed, 조용한 반환 금지)을 제공한다. 폴백 이벤트는
  이 스키마를 재사용하고 새 `entity_kind`(예: `Fragment`)를 추가한다.
- **토큰 예산 유틸 없음**: 사전 텍스트→토큰 추정 유틸이 없다(사후 집계·USD 회로차단기만 존재). AST 예산은
  결정론(원칙 I)·의존성 최소화를 위해 **문자 길이 근사**를 쓴다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rules/Examples 프롬프트를 중복·충돌 없이 재구성한다 (Priority: P1, 먼저 착수)

Framework/DBMS `code_rules_examples` 계열 프롬프트를 감사해 하나의 일관된 우선순위 체계로 재구성한다.
사용자가 "프롬프트를 우선 손봐야 한다"고 명시했고, 폴백의 품질검사기가 프롬프트의 품질 정의와 같은
기준을 써야 하므로 **순서상 먼저** 한다.

**Why this priority**: 품질 이상 판정(폴백 트리거)과 프롬프트 지침이 어긋나면 오탐/누락이 계속된다.
프롬프트와 결정론 검사기가 같은 품질 정의를 공유해야 한다.

**Independent Test**: 재구성된 프롬프트를 정적 감사 — (a) 동일/상충 지침 0, (b) 절대 개수 상한 문구 0,
(c) 원문 근거·Rule 매핑·중복 금지 good/bad 예시 존재, (d) `prompt_hashes.json` 갱신 + byte-identical 통과.

**Acceptance Scenarios**:

1. **Given** 지침이 누적된 현재 프롬프트, **When** 정적 중복/충돌 감사, **Then** 중복·상충 문구가 제거
   또는 단일화된 형태로 재구성된다.
2. **Given** 재구성된 프롬프트, **When** 텍스트 검색, **Then** "N개까지만"류 절대 개수 상한 문구가 없다.
3. **Given** 재구성된 프롬프트, **When** 검토, **Then** Framework 공통 원칙(사실 기반, Rule 직접 매핑,
   무의미 반복 금지, 결과별 대표 사례)이 DBMS 배치 프롬프트에도 충돌 없이 반영돼 있다.
4. **Given** 조각/병합 프롬프트, **When** 검토, **Then** 기존 rules 지침을 재사용하고 병합 전용 지시만
   얇게 얹어(지침 중복 없이) KV-cache 공통 prefix(원칙 V)를 깨지 않는다.

---

### User Story 2 - 의미 AST 뼈대가 보존되고 함수 분석은 오염되지 않는다 (Priority: P1)

parser가 만든 `IF/ELSE/LOOP/SWITCH/CASE`(및 이미 있던 `FUNCTION_CALL`/`NEW_INSTANCE`/`IMPORT`)가 그래프에
`PARENT_OF`로 중첩 보존되되, LLM 분석 횟수·대상·보강은 함수 도입 이전과 완전히 동일하다. 구조 노드는
결정론 정보(code_text·소속 포함)만 갖고 LLM 보강은 받지 않는다. CALLS/READS/WRITES는 여전히 함수 소유.

**Why this priority**: 폴백 분할이 이 데이터 모델 위에 선다. 여기가 틀리면 그래프가 깨지거나 분석 비용이
폭증한다.

**Independent Test**: 제어문 있는 C 함수 재분석 — (a) IF/LOOP가 PARENT_OF로 중첩 보존, (b) 구조 노드에
summary/domain_terms/rules 없음(=None), (c) 함수 분석 호출 수가 제어문 도입 전과 동일, (d) IF 아래 호출의
CALLS 출발점이 함수.

**Acceptance Scenarios**:

1. **Given** parser AST에 새 role 자식(IF)이 있음, **When** `_walk_framework` 실행, **Then** IF와 그 자식
   (FUNCTION_CALL 등)이 모두 그래프에 보존(소실 0).
2. **Given** IF 아래 `FUNCTION_CALL save_order`, **When** 그래프 적재, **Then** `checkout -CALLS-> save_order`
   가 생성되고 `IF -CALLS-> save_order`는 생성되지 않는다.
3. **Given** 구조 노드(IF·FUNCTION_CALL 등), **When** 저장, **Then** `code_text`·`start/end_line`·`owner_id`·
   `parent_id`는 채워지고 `summary`/`domain_terms`/`logical_name`/`search_text`/`stereotype`/`rules` 등 LLM
   필드는 채워지지 않는다.
4. **Given** step6 분석 대상 산정, **When** 실행, **Then** 제어문·호출 노드는 `analysis_targets`에 포함되지
   않아 함수 분석 호출 수가 제어문 추가 전후 동일.
5. **Given** `run_framework_per_node_analysis`의 cycle/round, **When** 제어문 PARENT_OF 엣지 존재, **Then**
   분석 순서(Tarjan SCC, Kahn)는 제어문 도입 전과 동일한 함수 순서를 낸다(오염 0).
6. **Given** 검색색인·`summarize_module`·`health_metrics`·component 등, **When** 그래프에 구조 노드 존재,
   **Then** 이 노드들은 함수처럼 검색·요약·집계에 섞이지 않는다(전수 감사).

---

### User Story 3 - Rules/Examples 반복·환각 이상이 감지되고 결정론적으로 복구된다 (Priority: P1)

함수 전체 분석 결과가 반복·환각 이상을 보이면 시스템이 결정론적으로 감지하고, 의미 AST 서브트리를 예산
기반으로 분할해 조각별 재분석 후 병합 LLM 1회로 함수 의미를 복원한다. 정상 결과는 폴백 없이 저장.

**Why this priority**: 사용자가 실제 겪은 핵심 장애를 해결하는 본체.

**Independent Test**: `_runs/prompt-iteration-3/`의 `load_target_orders`(반복 이상)와 정상 사례를 각각
재실행 — 이상 사례만 폴백을 타고 정상 사례는 폴백 0회로 통과.

**Acceptance Scenarios**:

1. **Given** 정상 함수 결과, **When** 품질검사, **Then** 이상 없음 → 폴백 없이 저장.
2. **Given** 동일 `(given,when,then,affected_tables)`가 다수 반복, **When** 품질검사, **Then** 이상 판정
   → 폴백 시작.
3. **Given** 폴백 분할, **When** AST 예산 분할, **Then** if 조건과 branch body가 분리되지 않고 하나의
   완전한 서브트리 조각으로 묶인다. 예산 안 상위 제어문은 통째로 한 조각, 초과할 때만 한 단계 아래 재귀.
4. **Given** 조각들, **When** 조각별 rules/examples LLM 실행, **Then** 각 조각은 함수 시그니처·공통 문맥·
   조상 조건 경로를 함께 받는다.
5. **Given** 조각별 결과, **When** 병합 LLM 1회, **Then** 조각 경계 넘는 동일 Rule이 합쳐지고, 다른
   정책/분기 결과는 보존되며, 원문에 없는 규칙은 발명되지 않는다.
6. **Given** 병합 결과, **When** 재검사, **Then** 이상이면 실패로 명시(무한 재시도 없음: 최초→폴백 1회→
   재검사가 경계), 통과면 원래 함수에만 저장.
7. **Given** 폴백 임의 단계 실패(조각/병합/재검사), **When** 실패, **Then** 로그+스트림에 실패가 명시되고
   조용한 성공으로 위장하지 않는다.

---

### User Story 4 - 폴백과 전체 파이프라인이 로그·스트림에 드러난다 (Priority: P2)

폴백의 모든 단계와, 조사에서 약했던 스트림 구간(색인 사전 생성·후처리 등)이 `analyzer.log`와 NDJSON
스트림에 나타난다.

**Why this priority**: 검증(원칙 6)과 신뢰의 전제. 폴백이 있어도 안 보이면 없는 것과 같다.

**Independent Test**: 이상 함수 재분석 1건의 NDJSON 스트림을 캡처해 폴백 각 단계 이벤트가 순서대로
존재하는지, coverage matrix의 약한 구간이 보강됐는지 확인.

**Acceptance Scenarios**:

1. **Given** 정상 경로(폴백 0회), **When** 분석, **Then** "정상 경로" 요약이 최종 1회 나타난다.
2. **Given** 폴백 경로, **When** 분석, **Then** 감지→분할(조각 수·범위)→조각별 시작/완료/실패→병합
   시작/완료/실패→재검사 결과→최종 요약이 순서대로 스트림에 나타난다.
3. **Given** §handoff에서 약하다고 지목된 구간(색인 사전 생성/index wait/fingerprint, 샘플 fetch chunk,
   `build_glossary`/`health_metrics`/`enrich_lineage_from_catalog`), **When** coverage matrix 작성, **Then**
   각 구간이 "충분/보강필요"로 표기되고, 보강필요 항목엔 최소 start/done/failed 이벤트가 추가된다.

### Edge Cases

- **작은 함수(제어문 0~1개)**: 폴백 분할에서 제외 — 분할할 서브트리가 없으면 폴백은 전체 재분석 1회로 수렴.
- **폴백 후에도 이상**: 무한 재시도 금지. 최초→폴백 1회→재검사가 경계. 재검사 실패는 결정론 중복정리 +
  이상 플래그 + 스트림 error(run은 죽이지 않되 조용한 성공도 금지).
- **의미 중복(문장 다르나 뜻 같음)**: 결정론 정규화만으로 완전 검출 불가 인정. 병합 LLM이 2차 방어선이며
  재검사도 완전하지 않을 수 있음을 최종 보고에 명시(완벽이라 말하지 않음).
- **조각 실패**: 한 조각 LLM 실패 시 해당 조각은 실패 기록, 나머지 조각은 계속. 병합 단계에서 실패
  조각이 있었음을 알 수 있어야 한다(부분 실패 은폐 금지).
- **DBMS 기존 구조 노드(DECLARE/TRY/TRIGGER_BLOCK)**: 이미 노드로 보존 중 — Framework에 도입하는 "보존 +
  분석 제외" 원칙을 DBMS와 어긋나지 않게 같은 분류 단일 진실로 정렬(중복 정책 생성 금지).

## Requirements *(mandatory)*

### FR — 프롬프트 재구성 (US1, 우선)

- **FR-001**: `prompts/framework/code_rules_examples.yaml`(현재 WIP 포함 전체)을 감사해 중복·상호 충돌
  지침을 제거하고 하나의 우선순위 체계로 재구성한다. 삭제 없이 또 추가하는 방식 금지.
- **FR-002**: 재구성 프롬프트는 원문 사실·Rule 직접 매핑·테스트 가치 게이트, 반복·표현만 바꾼 중복 강한
  금지, good/bad 예시, 우선순위·게이트 1회 명시, 특정 테스트데이터·함수명 하드코딩 금지를 포함한다.
- **FR-003**: 재구성 프롬프트는 Example 개수의 절대 상한 문구를 포함하지 않는다. "많다"만으로 실패 판정
  하지 않고 반복·무근거·Rule 비매핑 등 품질 신호와 결합해 판단하도록 기술한다.
- **FR-004**: `prompts/dbms/code_rules_examples_batch.yaml`도 감사 대상이며 Framework 공통 원칙을 DBMS 표현
  (배치 특성 고려)에 충돌 없이 반영한다.
- **FR-005**: 조각 분석·병합 프롬프트는 기존 rules 지침을 재사용하고 병합 전용 지시만 얇게 얹는다(지침
  중복 금지). KV-cache 공통 prefix(`_common.yaml`, 원칙 V) byte-identical 유지.
- **FR-006**: 프롬프트 변경 후 `cli_scripts/prompt_hashes.json`의 해당 해시를 갱신하고 `byte_identical`
  가드를 재확인한다.

### FR — 의미 AST 뼈대 보존 (US2)

- **FR-007**: `shared/lang/ontology_roles.yaml`에 C의 `IF/ELSE/LOOP/SWITCH/CASE`를 새 role(예:
  `control_structure`)로 추가한다. role 문자열은 구현 시 파일 컨벤션에 맞춰 확정하되, 아래 행동 계약을
  만족해야 한다. (어휘/분류의 단일 진실은 `ast-node-vocabulary.md`.)
- **FR-008**: `_walk_framework`는 `control_structure` role 자식을 만나면 (a) 그래프 노드로 생성하고
  (b) 자식을 계속 재귀 순회한다(현재 "미지정 role → 스킵"에서 이 role만 예외). 순회 시 두 컨텍스트를 분리
  스레딩한다: `structural_parent_id`(PARENT_OF용, 바로 위 노드) / `owner_routine_id`(CALLS/READS/WRITES용,
  가장 가까운 FUNCTION/METHOD — 제어문을 내려가도 불변).
- **FR-009**: 제어문 노드 아래 있는 `FUNCTION_CALL`/`NEW_INSTANCE`/`IMPORT`(기존 internal 수집,
  `_fw_internal`/`_collect_internal`)는 `structural_parent_id`가 아니라 `owner_routine_id` 기준으로 CALLS/
  READS/WRITES 엣지를 만든다.
- **FR-010**: 구조 노드(제어문 + FUNCTION_CALL/NEW_INSTANCE/IMPORT)는 결정론 필드만 채운다 — `code_text`(자기
  라인 범위 원문), `name`, `start/end_line`, `owner_id`, `parent_id`, `label`. LLM 필드(`summary`/
  `domain_terms`/`logical_name`/`search_text`/`stereotype`/`rules`/`examples`/`questions`/`embedding`)는
  채우지 않는다(`ast-node-vocabulary.md` §3).
- **FR-011**: 그래프 저장 대상(`graph_nodes`)과 LLM 분석 대상(`analysis_targets`)을 코드 레벨에서 명시적
  분리 집합으로 둔다. `analysis_targets`가 "실제 함수" 단일 진실이며 모든 분석·보강·순서 계산이 이를 참조.
- **FR-012**: `analyze_code_step.py`의 분석 대상 선정과 `run_framework_per_node_analysis`의 `all_ids`/cycle/
  round 계산은 `analysis_targets`만 사용한다. 제어문 노드가 `contexts`/`all_ids`에 들어가는 경로가 하나라도
  있으면 결함.
- **FR-013**: 검색색인·`summarize_module`·`health_metrics`·component 클러스터링 등 함수/모듈을 순회하는 모든
  downstream 소비자가 `analysis_targets`만 순회하도록 **코드 감사로 전수 확인**한다(추정 금지). 소비자마다
  개별 `if type==IF skip` 분기를 흩뿌리지 않고 분류 단일 진실을 참조한다(땜빵 금지).
- **FR-014**: `PARENT_OF`는 구조 포함 전용으로 유지. 제어문 자식 간 순차 실행을 암시하는 `NEXT` 엣지를
  새로 만들지 않는다(향후 CFG는 별도 스펙).
- **FR-015**: 제어문이 전혀 없는 기존 함수는 분석 호출 횟수·CALLS/READS/WRITES 결과가 이번 변경 이전과
  100% 동일해야 한다(회귀 0).

### FR — Rules/Examples 폴백 (US3)

- **FR-016**: 정상 경로는 지금처럼 함수 전체를 1회 분석한다(성능/캐시 유지, 변경 없음).
- **FR-017**: 결정론 품질 검사기(신규, LLM 미사용)가 최초 분석 결과에 대해 계산한다: (a) 정규화(공백/
  대소문자/구두점 무시) 후 동일 `(given,when,then,affected_tables)` 튜플 중복 수/비율, (b) Rule 내부/간
  반복, (c) Rule.statement 중복, (d) `examples` 배열 길이가 스키마 `max_length`(FR-022 값)에 정확히
  도달했는지(잘림 의심), (e) 원문에 없는 식별자/값 등장에 대한 best-effort 신호(완전 환각 검출 아님을 명시).
- **FR-018**: 이상 판정 시작값: 정규화 중복 비율 **20% 이상** 또는 동일 의미 그룹 **3회 이상** 또는
  examples가 스키마 캡에 정확히 도달 → 이상. 지연시간(100초+ 등)은 로그에 관찰 신호로만 남기고 단독으로
  이상 판정하지 않는다. 이 값들은 실측 후 조정 대상임을 명시한다.
- **FR-019**: 이상 판정 시 결정론 AST 예산 분할기(신규, LLM 미사용)가 함수의 제어문 서브트리를 분할한다.
  분할 계획에 LLM을 쓰지 않는다 — AST 라인 범위와 문자 길이 근사 예산만 사용.
- **FR-020**: 분할 규칙 — 상위 제어문 서브트리가 예산 안이면 통째로 한 조각, 초과할 때만 한 단계 아래
  자식 서브트리로 재귀. if 조건과 branch body를 다른 조각으로 찢지 않는다. 배타적 then/else를 하나의
  순차 조각으로 합치지 않는다. 예산 안에 들어오는 작은 함수는 분할하지 않는다(폴백=전체 재분석 1회로 수렴).
  분할에 필요한 서브트리는 step2에서 owner 함수의 컨텍스트에 실어 전달한다(그래프 재조회 없이).
- **FR-021**: 각 조각은 함수 시그니처, 공통 선행/후행 문맥, 조상 조건 경로(자신이 어떤 조건들 아래
  있는지)를 함께 받아 기존 rules/examples LLM 채널로 분석된다. 조각별 결과는 병합 전용 LLM 1회로 합쳐진다.
  병합 프롬프트는 조각 경계 넘는 동일 Rule 합침, 표현만 다른 동일 Example 합침, 다른 정책/분기 결과 보존,
  원문에 없는 규칙 발명 금지, 개수 상한을 목표로 채우거나 자르지 않음을 지시하고 공개 `RulesExamplesOutput`
  형태로 반환한다.
- **FR-022**: `RuleItem.examples`의 `max_length`를 30 → **80**으로 올린다(관측 정상 범위 21~47 수용, 수백
  개 폭주는 차단). 최상위 `RulesExamplesOutput.rules`/`BatchRulesItem.rules`의 `max_length=30`은 유지(Rule
  개수 폭주는 관측된 적 없음). 두 캡이 "정상적으로 많은 것"을 막는 비즈니스 규칙이 아니라 provider 출력
  폭주 안전망임을 스키마 docstring에 명시한다. 실제 품질 판단은 FR-017/FR-018 결정론 검사기 전담.
- **FR-023**: 폴백은 무한 반복하지 않는다. "최초 → 폴백 1회(분할→조각분석→병합) → 재검사"가 경계. 재검사
  이상 시 run을 죽이지 않고, 결정론으로 지울 수 있는 완전중복만 정리한 "부분 결과 + 이상 플래그"를 함수에
  저장하고 로그/스트림에 error로 명시한다(silent success 금지).
- **FR-024**: 최종 rules/examples 결과(정상/폴백 무관)는 항상 원래 함수 노드에만 저장한다. 제어문 노드에
  Rule/Example/Question을 저장하지 않는다.

### FR — 스트림 가시성 (US4)

- **FR-025**: 폴백 각 단계(품질검사 시작/결과, 분할 시작/조각 수·범위, 조각별 시작/완료/실패, 병합
  시작/완료/실패, 재검사 결과, 최종 정상/폴백 경로 요약)는 `processing_progress`의 기존 스키마를 재사용해
  `track_processing()`으로 emit한다. 새 `entity_kind`(예: `Fragment`)를 추가한다.
- **FR-026**: 조용한 실패 금지(원칙 IV) — 조각/병합/재검사 실패는 `log_error` + `stream.emit(error)` +
  (raise 또는 명시적 실패 결과 반환) 중 하나.
- **FR-027**: §handoff 지목 약한 구간(색인 사전 생성/index wait/fingerprint, 샘플 fetch chunk,
  `build_glossary`/`health_metrics`/`enrich_lineage_from_catalog`)에 대해 coverage matrix(문서)를 작성하고,
  "다건 loop/외부 HTTP/LLM/DB index wait" 구간은 최소 start/done/failed 이벤트를 추가한다. 짧은 결정론
  단계는 phase+완료 message로 충분함을 matrix에서 밝히고 이벤트를 억지로 늘리지 않는다.
- **FR-028**: 동일 이벤트 과도 방출 금지 — 다건 loop는 throttle/dedup 계약(예: N개마다 1회)을 Analyzer
  emit 지점에도 적용(프론트가 이미 throttle해도 서버 과도 emit은 대역폭·로그 낭비).

### Key Entities

- **StructuralNode(구조 노드)**: `FUNCTION_CALL`/`NEW_INSTANCE`/`IMPORT`/제어문 7타입. 결정론 필드만 채운
  `CodeNode`(LLM 필드 None). `graph_nodes`에만, `analysis_targets`엔 없음. `structural_parent_id`와
  `owner_routine_id`를 구분해 만들어진다.
- **AnalysisTargetSet**: Framework FUNCTION/METHOD만의 명시적 집합. 검색/요약/health/component/분석순서가
  참조하는 단일 진실.
- **RuleQualityReport**: 결정론 품질검사 결과 — 중복 비율, 중복 그룹 최대 크기, 캡 도달 여부, 이상 여부,
  사유 목록.
- **CodeFragment**: 폴백 분할 산출 — `fragment_id`, `ancestor_condition_path`, `source_line_range`, 조각별
  rules/examples 후보.
- **FallbackRun**: 한 함수 폴백 전체 기록 — 원인(RuleQualityReport), 조각 목록, 병합 결과, 재검사 결과,
  최종 상태(정상경로/폴백성공/폴백후이상/실패).

## Success Criteria *(mandatory)*

- **SC-001**: 재구성 프롬프트 정적 감사에서 중복/상충 문구 0건, 절대 개수 상한 문구 0건, `byte_identical`
  가드 통과.
- **SC-002**: 제어문 있는 C 함수 재분석에서 IF/LOOP가 Neo4j에 PARENT_OF로 나타나고 그 아래 FUNCTION_CALL
  소실 0건. 구조 노드의 LLM 필드는 모두 비어 있고 `code_text`는 채워져 있다.
- **SC-003**: 제어문 도입 전후로 동일 함수 집합의 LLM 분석 호출 횟수 동일(제어문으로 인한 추가 호출 0).
- **SC-004**: `load_target_orders`(보존 이상 run)를 새 파이프라인으로 재실행 시 폴백을 타고 최종 Example
  정규화 중복 비율이 재검사 통과 기준 미만. 정상 함수(회귀표본 포함)는 폴백 0회 통과.
- **SC-005**: 폴백 1건에서 감지→분할→조각분석→병합→재검사→요약 NDJSON 이벤트가 순서대로 전부 존재(누락 0).
- **SC-006**: coverage matrix에 §handoff 지목 구간이 모두 "충분/보강완료"로 표기되고 미보강 항목은 사유와
  함께 [[todo]]에 기록(누락을 완료로 위장하지 않음).

## Assumptions

- Parser spec 007의 어휘/분류/필드 계약(`ast-node-vocabulary.md`)이 입력 계약이다. 바뀌면 이 스펙도 갱신.
- 품질검사 임계치(중복 20%, 반복 3회)와 조각 예산은 시작값이며 C 실측 후 §handoff 검증 계획대로 조정한다
  (최종 보고에 조정 여부 명시).
- AST 예산은 문자 길이 근사(외부 tokenizer 의존 없음) — 목적은 "상대적 분할 크기 판단".
- DBMS 배치 프롬프트는 공통 원칙 반영(FR-004)만 다루고, DBMS 자체 폴백 분할 신설은 범위 밖.
- 의미 중복 완전 자동 검출은 달성되지 않는다 — 결정론 검사 + 병합 LLM + 재검사의 다층 방어이며 최종
  보고에서 "검증됨"과 "best-effort"를 구분해 명시한다.
