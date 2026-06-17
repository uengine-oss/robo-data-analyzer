# 017 — 노드 식별자 전면 `id` 통일 (흔적 0, 전 서비스)

상태: 구현 완료(analyzer+catalog+frontend) · architect/text2sql=TODO · 사용자 검증 대기 · 2026-06-18

## 구현 결과 (2026-06-18)
- **analyzer**: 데이터모델(graph_nodes/edges)·neo4j writer 24개·schema_constraints·step1~7·shared·agent_tools·프롬프트 전부 `id`/역할명. 필드접근 `.function_id`/`.fqn` 등 잔재 0(grep). 소속(module_id/package_id)·parent_id **노드속성 제거→엣지**. references/init_by label별 키분기 죽은코드 삭제. py_compile 통과.
- **catalog**: dw_schema·fk_inference·schema_query·sample_context·graph_query 의 `.fqn`→`.id`(ENDS WITH 값불변). `.fqn` 잔재 0.
- **frontend**: graphDisplay·navigator·analyzer.ts·ColumnCard·PackageCard·NodeDetailPanel 의 그래프속성 `.fqn`/`.rule_hash`/`.question_id`/`.example_id`→`.id`. 식별=elementId(불변). schema feature(catalog 응답기반)·로그 라벨 무관.
- **정책(의도된 유지)**: ⒜ 로그 필드 라벨(`function_id=`/`module_id=` in log_error)·`logger.py` 라벨맵 = 관측용, 값은 `.id`. ⒝ three_split 조회 param `function_id`·`table_fqn` 프롬프트 라벨 = 내부/LLM-facing, banned-7 아님. ⒞ `rule_hash()` 해싱 함수명(필드 아님). → **그래프·데이터 계약은 100% `id`/역할명**, 위는 contract 밖.

## 왜 (문제)
노드 종류마다 식별 키 이름이 달랐다(`function_id`/`module_id`/`variable_id`/`constant_id`/
`datatype_id`/`package_id`/`fqn`/`rule_hash`/`example_id`/`question_id`). 소비자(프론트·
catalog·architect)가 "이 노드의 식별 속성이 무엇인지"를 종류별로 알아야 했고, 프론트가
`function_id`를 파싱해 타입/이름을 도출(='F' 배지 사태)하는 군더더기를 낳았다.

## 무엇 (계약)
**모든 노드의 자기 식별 속성 = `id` 하나.** 값은 기존과 동일(예 Table.id = `"db.schema.name"`,
Rule.id = 기존 해시). 다른 노드를 가리키는 참조/엣지필드는 **관계 역할명**(`source_id`/
`target_id`/`caller_id`/`callee_id`/`parent_id`/`child_id`/`owner_id`)을 쓰고, 금지 문자열
(`function_id`·`module_id`·`variable_id`·`constant_id`·`datatype_id`·`package_id`·`fqn`)을
**코드·그래프·프롬프트 어디에도 남기지 않는다(흔적 0)**.

### 노드 자기 식별자 → `id`
| 노드 | 기존 키 | → | 비고 |
|---|---|---|---|
| Package | package_id | id | |
| Module | module_id | id | |
| Function | function_id | id | (그래프 속성은 이미 id) |
| Variable | variable_id | id | |
| Constant | constant_id | id | |
| DataType | datatype_id | id | |
| Table | fqn(@property) | id(@property) | 값 불변 `db.schema.name` |
| Column | fqn | id | 값 불변 |
| Schema | (db,name) 복합 + fqn | id(@property `db.name`) | 제약 복합→`s.id UNIQUE`. db/name=표시속성 유지 |
| Rule | rule_hash | id | 값=해시 불변 |
| Example | example_id | id | |
| Question | question_id | id | |

### 참조/엣지 endpoint → 역할명 (금지문자열 0)
| dataclass | 기존 | → |
|---|---|---|
| ReadsWrites | function_id, table_fqn | source_id, target_id |
| Calls | caller_function_id, callee_function_id, callee_module_id, callee_package_id, callee_name | caller_id, callee_id, callee_module, callee_package, callee_name |
| ParentOf | parent_function_id, child_function_id | parent_id, child_id |
| HasFunction | module_id, function_id | parent_id, child_id |
| HasVariable | module_id, variable_id | parent_id, child_id |
| HasMember | datatype_id, variable_id | parent_id, child_id |
| BelongsToPackage | module_id, package_id | child_id(module), parent_id(package) |
| References | function_id, target_id | source_id, target_id |
| InitBy | from_id, source_id, *_label | **불변**(금지문자열 없음) |
| HasRule | function_id, rule_hash, (local_rule_id/flow_id/coupled_domains) | source_id, target_id, (메타 불변) |
| HasExample | rule_hash, example_id | source_id, target_id |
| HasQuestion | function_id, question_id | source_id, target_id |
| Next/Branch | function_id, from_rule_id, to_rule_id | owner_id, from_rule_id, to_rule_id |
| AffectsTable | example_id, table_name, op | source_id, table_name, op (table_name=이름매칭 유지) |
| HasColumn | table_fqn, column_fqn | parent_id, child_id |
| BelongsTo | table_fqn, schema_fqn | child_id(table), parent_id(schema) |
| Fk | from_table_fqn, to_table_fqn, from_column, to_column,… | from_id, to_id, from_column, to_column,… |

규칙: 기본 `source_id`/`target_id`. 호출=`caller_id/callee_id`. 트리·포함=`parent_id/child_id`
(parent=컨테이너). 스코프 한정자=`owner_id`. 라인-로컬 라벨(local_rule_id/from_rule_id/
to_rule_id)·이름매칭(table_name)·컬럼명(from_column)은 식별자 아님 → 불변.

### 노드 속성에서 관계속성 제거 (그래프 흔적 0)
- Function 노드: `module_id`·`package_id` **속성 SET 제거**(HAS_FUNCTION/BELONGS_TO_PACKAGE 엣지로 표현). `parent_id` 속성은 유지(트리·own_ast_type·인덱스).
- `Function.package_id` 필드: 노드속성 외 로직 소비처 없음 → **필드 자체 삭제(데드)**.
- `Function/Variable/Constant/DataType.module_id` 필드(소속 모듈 참조, 엣지빌드+그룹핑에 transient 필요) → **`owner_id`로 개명**(흔적0).

### 부수 정리 (id 통일의 직접 귀결)
- `references.py`·`init_by.py`의 target_label별 키분기(`_KEY_OF`/`_TARGET`): 모든 키가 `id`로 수렴 → **죽은 분기 삭제, MATCH `{id: ...}` 단순화**.
- `own_ast_type()`: `self.function_id`→`self.id` (동작 동일, primary_label과 별개 유지).

### 프롬프트 (KV)
- `prompts/framework/_common.yaml`·`prompts/common/large_function_one_liner.yaml`의 템플릿 변수 `{function_id}`→`{id}`, 채우는 dict 키도 동일. prose 언급 정리.
- **KV 골든 재기준 필수**: 사용자 `python check_byte_identical.py --update`.

## 영향 서비스 (blast radius — 전수조사 2026-06-18)
- **analyzer**(producer, project/robo-data-analyzer): function_id 285·module_id계열 207·fqn 189 ≈680곳. 전부 본 스펙대로.
- **catalog**(architect/robo-analyzer/robo-data-catalog, 유일 체크아웃): `fqn`만 사용(~25줄, dw_schema·fk_inference·schema_query·sample_context·graph_query) + `src.id` 1곳. `t.fqn ENDS WITH $table_name`→`t.id ENDS WITH`(값 불변=기계적). function_id/module_id 등 0.
- **frontend**(project/robo-data-frontend): graphDisplay.ts(fqn/rule_hash/question_id/example_id)·navigator.ts(`p['fqn']`)·schemaCanvas 주석 등 소수. 식별=elementId라 식별 안 깨짐, 표시 폴백만 정합.
- **antlr·api-gateway**: 0 (type/name/signature만 생산 / 라우팅만). 무관 — 검증 완료.

## TODO (다운스트림 소비자 — 별도, 사용자 확정)
- **architect/api/features/ingestion**: 그래프 소비 백엔드. `f.function_id`(rule_extractor.py:51)·`Example{example_id}`·`Question{question_id}`(persistence.py)·`m.fqn`/`f.fqn`(module_retriever)·`coalesce(f.procedure_name,f.name)` 다수. id 통일 후 로직 수정 필요. (Electron 껍데기와 별개의 백엔드.)
- **text2sql**: 그래프 소비. **미클론** → clone 후 수정.

## 검증 (사용자)
1. RWIS 재분석 → Neo4j 노드 식별 속성이 전부 `id`, module_id/package_id 노드속성 0.
2. `check_byte_identical.py --update`로 KV 골든 재기준.
3. Electron 빌드 → 그래프/노드패널 정상(타입=라벨직독, 식별=elementId, 회귀0).
4. catalog `/robo/schema/tables` 등 정상 반환(fqn→id ENDS WITH).

## 비목표
- 라벨 체계 변경(016에서 완료). 관계 자체 추가/삭제. architect·text2sql 동시 수정(TODO).
