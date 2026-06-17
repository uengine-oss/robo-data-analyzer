# Feature Specification: DBMS 노드 라벨 = antlr type (FUNCTION 뭉개기 폐지) + name·code_text 속성 + ID 파싱 종식

**Feature Branch**: `016-node-label-from-antlr-type`

**Created**: 2026-06-17

**Status**: Draft (합의 전 — 코드 착수 금지)

**Input**: 사용자 확정 규칙 — ① **레이블(primary)은 antlr JSON `type`을 따라간다 — 전략 무관.** 지금 DBMS가 SELECT·IF·PROCEDURE를 전부 `:FUNCTION`으로 뭉개는 건 **완전 잘못된 로직**(antlr type을 버림). ② **sub-label = 온톨로지 = Framework에서만**. ③ **EXTERNAL은 두 전략 다** 쓰임. ④ 프론트가 `function_id`/`signature`를 파싱해 타입·이름을 뽑던 건 폐지 — 라벨(=타입)·속성(=이름·원문) 직독. ⑤ 모든 코드 노드는 start_line·end_line·원문코드 보유.

> 노드 스키마 정의처 = [004-ast-code-structure-graph](../004-ast-code-structure-graph/spec.md). 본 스펙은 004의 **DBMS 라벨 도출 로직을 정정**(antlr type 보존)하고 표시 속성을 가산한다. 라벨 도출 = `shared/lang/ontology.py`(Framework 전용 roles)·`analysis_flow/step2_load_ast/antlr_json_reader.py`(DBMS 경로). 관련 엣지 = `shared/neo4j/edges/*`. 헌법: 결정론 · 단일진실 · 계약경계 · 추측금지.

## 사실 확정 (2026-06-17, 코드 직접 확인 — 추측 아님)

- **antlr JSON은 모든 노드에 `type`을 준다**(원문 `data/analysis/RWIS/FN_EDIT_YN.json`): 루트 `FUNCTION`(name="FN_EDIT_YN") 아래 `SELECT`·`IF`·`ASSIGNMENT`·`UNION_ALL`·`RETURN`… 각각 `type` 보유. 구문 노드는 `name` 없음(정상).
- **Framework는 antlr type을 라벨에 보존**: `ontology.py sub_label_of()`가 antlr type이 primary와 다르면 그 type을 그대로 반환(`:70 return node_type`) → `:FUNCTION:METHOD`·`:MODULE:CLASS`. roles는 `ontology_roles.yaml`에 java/c/python **전용**으로 명시(`:10` "DBMS는 sub-label 없음 — roles는 Framework 전용").
- **DBMS는 antlr type을 버린다(버그)**: `antlr_json_reader.py _walk_dbms`(334-340)가 PROCEDURE·FUNCTION·TRIGGER·SELECT·IF·LOOP·DML 등 **DECLARE/VAR/CONSTANT 빼고 전부** `Function`으로 만들고, `_make_function`을 **sub_label=""**로 호출(339). `function.py upsert_functions`는 sub 없으면 라벨 `:FUNCTION`(95). → **모든 DBMS 노드가 `:FUNCTION`**, antlr type은 `function_id` 문자열 안에만 남음. SELECT문이 "함수"로 표시되는 'F'배지 사태의 뿌리.
- **EXTERNAL은 두 전략 공용**: `calls.py:35` external 콜리 stub `:FUNCTION:EXTERNAL`, `:39` `:MODULE:EXTERNAL`. DBMS·Framework 외부호출 모두 사용.
- **`:FUNCTION` 라벨에 결합된 곳(blast radius 원천)**: 엣지 Cypher가 `MATCH (f:FUNCTION {function_id})`로 라벨 바인딩 — `calls.py:24-25`(CALLS), `reads_writes.py`(READS/WRITES FUNCTION→TABLE), `parent_of.py`(PARENT_OF), `has_function.py`(HAS_FUNCTION MODULE→FUNCTION). DBMS 노드가 `:SELECT` 등으로 바뀌면 이 MATCH가 0매치.
- **catalog는 라벨 무관 통과**: `graph_query_service.py:141` `labels(n)`·`n{.*}` 전부 반환 → 새 라벨도 자동 전달(읽기 무수정). 단 catalog의 일부 쿼리가 `:FUNCTION`을 명시하면 점검 필요.
- **저장 정본**: FUNCTION 노드엔 현재 `name`·`code_text` 없음(`function.py:24-39`).

## 결정 (Decisions — 사용자 확정)

1. **DBMS primary 라벨 = antlr `type` 그대로**(뭉개기 폐지): PROCEDURE→`:PROCEDURE`, TRIGGER→`:TRIGGER`, 콜러블 FUNCTION→`:FUNCTION`, 구문 SELECT→`:SELECT`·IF→`:IF`·UPDATE→`:UPDATE`·UNION_ALL→`:UNION_ALL`… DBMS는 **sub-label 없음**(온톨로지 미적용 — antlr type이 곧 라벨).
2. **Framework 현행 유지**: 온톨로지 roles로 primary(MODULE/FUNCTION/…) + antlr type을 sub-label로 보존(`:FUNCTION:METHOD`). 이미 antlr type을 라벨에 살리므로 무변경. (※ Framework primary도 antlr type으로 바꿀지는 별도 결정 — 본 스펙은 DBMS 버그 정정에 집중, Framework는 회귀 0 유지.)
3. **EXTERNAL 유지**: 두 전략의 외부 호출 stub에 `:…:EXTERNAL` 마커 — 무변경.
4. **`ast_type` 속성 ❌**: 라벨이 곧 타입이므로 별도 타입 속성 불필요(이전 초안 폐기).
5. **표시 전용 속성 가산** — 라벨(=타입)과 별개:
   - **`name`**: antlr `node.name`(콜러블=루틴명, 구문=""). function_id 파싱 대체.
   - **`code_text`**: 본문 원문 슬라이스(file_path+start~end). step2는 소스 무파싱 계약이므로 **step6**(본문 읽는 단계)에서 SET.
6. **★function_id = Framework 전용, DBMS 완전 제거(사용자 확정, 반발 금지)**: SELECT/IF/PROCEDURE는 "함수"가 아니므로 `function_id`(function 개념)를 붙이지 않는다. DBMS 노드 식별 = **중립 노드 id**(AST 기반 — file:type:line 류, function 명칭 무관). DBMS 엣지(READS·WRITES·PARENT_OF·CALLS)는 이 중립 id로 **재배선**(function_id 키 제거). elementId=프론트 표시 식별. 역할 키(function_id/module_id/variable_id)·역할 라벨은 Framework에만.
7. **엣지 라벨 바인딩 정정 — ★공유 라벨 없음(사용자 확정)**: `:FUNCTION` 전제 MATCH를 정정하되 **공유 기술 라벨(:CodeNode 등) 만들지 않는다.** 노드는 **자기 antlr type 라벨만**. 엣지는 linker가 각 노드의 타입을 이미 알므로(parent/child·source/target = reader 생성 노드) **엣지 item에 endpoint 라벨을 담아 그 자기 라벨로 MATCH**(라벨-쌍으로 그룹). 인덱스는 **나타난 antlr type 라벨별로**(파싱 결과서 도출) `id` 인덱스 생성. schema_constraints의 `:FUNCTION` UNIQUE/인덱스(function_id·parent_id·component_id) → 새 모델(id·타입라벨)로 갱신. wipe+recreate(매 run)라 cross-run idempotency 불요 — id는 run 내 dedup·엣지연결용.

8. **★속성 정리 — 전 노드·두 전략 (사용자 확정: 노드=`id`+라벨+표시속성, 식별자 분리 0·관계 속성 0·중복 0)**:
   - **식별자 단일화**: `function_id`·`module_id`·`variable_id`·`constant_id`·`datatype_id` → 전부 **`id` 하나**(타입은 라벨이 말함). DB노드 `fqn` = 자연키 유지 or `id` 통일(결정 대기).
   - **관계를 속성에 중복한 것 제거 → 엣지로만**: Function `module_id`(HAS_FUNCTION)·`package_id`(모듈→PACKAGE 도출)·`parent_id`(PARENT_OF) / Variable·Constant·DataType `module_id`. (만들 때 지역변수로만 쓰고 노드에 저장 안 함.)
   - **도출가능 중복 제거**: `signature`(=name+params+return; ★Java `parameters` 채워짐 확인 후, 미충족 시 signature에서 params 1회 추출 후 제거) / Table `db`·`schema`·`name`(`fqn`=db.schema.name에서 도출).
   - **유지**: id·라벨(antlr type)·name·code_text·start_line·end_line·comment·annotations·return_type·params·var_type·init_value·value·summary·stereotype·logical_name·sub_label(Framework)·dtype/nullable/is_primary_key(Column)·description/analyzed_description/description_source·risk 등 건강지표·component_id.

## User Scenarios & Testing *(mandatory)*

### US1 - DBMS 구문 노드가 자기 타입 라벨 (P1)
SELECT/IF/UPDATE 등 구문 노드가 antlr type 그대로 라벨을 받는다(더 이상 `:FUNCTION` 아님).
- **Test**: `FN_EDIT_YN.json` 분석 → line 30 노드 라벨 `:SELECT`, line 35 노드 `:IF`. function_id 파싱 없이 라벨이 타입.

### US2 - DBMS 콜러블 라벨·이름 (P1)
프로시저/함수/트리거가 antlr type 라벨(`:PROCEDURE`/`:FUNCTION`/`:TRIGGER`) + `name`(루틴명)을 받는다.
- **Test**: FN_EDIT_YN 콜러블 라벨 `:FUNCTION`, `name="FN_EDIT_YN"`, `code_text`에 1~125 원문.

### US3 - 엣지 회귀 0 (P1)
라벨이 바뀌어도 PARENT_OF·HAS_FUNCTION·READS/WRITES·CALLS가 DBMS 노드를 그대로 연결한다.
- **Test**: 재분석 후 DBMS 트리(PARENT_OF)·테이블접근(READS/WRITES)·호출(CALLS) 엣지 수가 변경 전과 동일(라벨만 정밀화, 관계 불변).

### US4 - Framework 회귀 0 (P1)
Framework 라벨(`:FUNCTION:METHOD` 등)·sub-label·EXTERNAL이 그대로다.
- **Test**: Java 프로젝트 분석 → 메서드 `:FUNCTION:METHOD`, 클래스 `:MODULE:CLASS` 불변.

### Edge Cases
- antlr type이 다양(SELECT/INSERT/UPDATE/DELETE/MERGE/IF/ELSIF/ELSE/LOOP/CASE/CURSOR/UNION_ALL/WITH/RETURN/ASSIGNMENT…) → 라벨 수 증가(13노드 계약 갱신 필요). 라벨명은 antlr type 그대로(통제값, 주입안전).
- 구문 노드 `name=""`(없는 게 정상). 표시는 프론트가 `라벨[start_line]`로(016/017 frontend).
- 재분석 전 기존 그래프는 옛 `:FUNCTION` — 프론트 폴백 안전 렌더.
- catalog/프론트가 `:FUNCTION`으로 DBMS 전체를 집던 쿼리/분기는 새 라벨 집합에 맞춰 정정.

## Requirements *(mandatory)*

- **FR-001**: DBMS 노드의 primary 라벨은 antlr `type` 그대로여야 한다(PROCEDURE/FUNCTION/TRIGGER/SELECT/IF/…). 더 이상 일괄 `:FUNCTION`으로 뭉개지 않는다. DBMS는 sub-label 없음.
- **FR-002**: Framework 라벨링(roles→primary + antlr type sub-label)·EXTERNAL 마커는 불변(회귀 0).
- **FR-003**: FUNCTION/DBMS 노드에 `name`(antlr node.name; 구문="")을 저장한다 — function_id 파싱 대체.
- **FR-004**: 코드 노드에 `code_text`(본문 원문 슬라이스)를 step6에서 저장한다(step2 소스 무파싱 불변식 보존).
- **FR-005**: `:FUNCTION`에 라벨 바인딩된 엣지 MATCH(CALLS/READS/WRITES/PARENT_OF/HAS_FUNCTION)를 DBMS 새 라벨에서도 동작하도록 정정한다(function_id/fqn 키 매칭). 관계 산출 회귀 0.
- **FR-006**: DBMS 노드는 `function_id`(및 역할 키 module_id 등)를 **갖지 않는다** — function 개념은 Framework 전용. DBMS 식별·엣지 연결은 **중립 노드 id**(AST 기반). `ast_type` 같은 별도 타입 속성도 만들지 않는다(라벨이 타입).
- **FR-006b**: DBMS 엣지(READS·WRITES·PARENT_OF·CALLS)의 MATCH/MERGE를 function_id → 중립 노드 id로 재배선한다(회귀 0). Framework는 현행 키(function_id 등) 유지.
- **FR-007**: 13노드 라벨 계약 문서를 갱신한다(DBMS antlr type 라벨 추가). catalog/프론트가 `:FUNCTION`으로 DBMS를 집던 곳을 새 라벨에 맞춰 정합.
- **FR-008**: 같은 antlr 입력에 대해 라벨·name·code_text가 결정론으로 동일(004 SC-002 유지).

### Key Entities *(데이터 변경)*

- **DBMS 코드 노드**: primary 라벨 = antlr type(:FUNCTION/:PROCEDURE/:TRIGGER/:SELECT/:IF/…). sub-label 없음. 속성 가산 `name`(콜러블 루틴명/구문 "")·`code_text`(원문 슬라이스). function_id·parent_id·start_line·end_line·기타 분석/건강 속성 불변.
- **Framework 코드 노드**: 불변(roles primary + antlr type sub-label). name·code_text 동일 가산.
- **EXTERNAL stub**: 불변(`:…:EXTERNAL`).
- **엣지**: 타입·속성 불변, MATCH 라벨 바인딩만 정정(function_id 키).

## 영향 범위 (Blast Radius — ★대공사, file:line, 착수 전 확정)

| 영역 | 파일 | 변경 |
|---|---|---|
| DBMS 라벨 도출 | `antlr_json_reader.py _walk_dbms`/`_make_function`(334-340) + `function.py upsert_functions`(89-96) | sub_label="" 하드코딩 → antlr type을 라벨로. DBMS는 type별 라벨, FW는 현행 |
| 속성 가산 | `graph_nodes.py` Function(+name·code_text) · `function.py` SET/to_item · step6 code_text SET | |
| 엣지 MATCH | `shared/neo4j/edges/calls.py`·`reads_writes.py`·`parent_of.py`·`has_function.py` | `:FUNCTION {function_id}` → `{function_id}`(라벨 비결합) 또는 라벨 집합 확장. 회귀 0 검증 |
| 계약 | 13노드 라벨 문서·`reference_robo_contracts` | DBMS antlr type 라벨 추가 반영 |
| catalog | (읽기 `n{.*}`·`labels(n)` 무수정) 단 `:FUNCTION` 명시 쿼리 점검 | `service/*` grep |
| 프론트 | nodeClassify·navigator·graphDisplay 등 `:FUNCTION` 가정·function_id/signature 파싱 | 016/017 frontend에서 라벨 직독으로 전환 |
| 재분석 | 필요 | 라벨·name·code_text 재생성 |

## Success Criteria *(mandatory)*

- **SC-001**: 재분석 후 DBMS 노드가 antlr type 라벨(:SELECT/:PROCEDURE/…)을 갖고, `:FUNCTION` 일괄 뭉개기가 사라진다.
- **SC-002**: name(콜러블 루틴명/구문 "")·code_text(원문) 존재.
- **SC-003**: PARENT_OF·HAS_FUNCTION·READS/WRITES·CALLS 관계 수·연결이 변경 전과 동일(라벨만 정밀화, 회귀 0). Framework 라벨·EXTERNAL 불변.
- **SC-004**: catalog `/robo/graph`가 새 라벨·속성을 무수정 통과. catalog/프론트 `:FUNCTION`-DBMS 가정 정합 완료.
- **SC-005**: 같은 입력 재실행 시 라벨·속성 결정론 동일. ast_type 같은 중복 타입 속성 0.

## Plan (구현 순서 — 합의 후)
1. (확인) `:FUNCTION` 라벨 바인딩 엣지·catalog·프론트 전수 grep으로 영향처 사실확정(회귀 가드 목록).
2. DBMS 라벨 도출 정정(antlr_json_reader + function.py) → 엣지 MATCH 라벨 비결합(function_id 키)으로 정정.
3. name(step2)·code_text(step6) 속성 가산.
4. 재분석 → Neo4j 직접 조회로 라벨=antlr type·name·code_text·엣지 회귀0 검증([[principle-verify]]).
5. 계약 문서(13노드)·catalog/프론트 `:FUNCTION` 가정 정합.
6. 프론트(016/017)는 라벨·name·code_text 직독으로 전환(파싱 삭제) — 본 스펙 구현·재분석 후.
