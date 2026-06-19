# Phase 0 Research: 노드 속성 정체화

## A. Blast Radius 전수 (코드 grep 확정 2026-06-19)

- **코드노드 속성 저장 단일점**: `shared/neo4j/nodes/function.py` `to_item`(id·name·signature·file_path·start/end_line·annotations·comment·return_type·params) + `to_summary_item`(summary·stereotype·logical_name·table_insights_json·code_text). 둘 다 **명시 `SET node.x = item.x`** 라 빈값도 박힘.
- variable/constant/data_type/module `to_item` 동일 패턴(빈값 박힘).
- **health**: `health_metrics_step.py` `_SET_QUERY` = fan_in·fan_out·loc·in_cycle·table_coupling·god_flag·risk 를 **모든 코드노드**(`MATCH (node {id})`)에 SET → 구문도 fan_in=0 등 받음.
- **table_insights 분배는 이미 존재**: `enrich_table_description_step.py`(step7)가 `fn.extra["table_insights"]` 누적 → `table_description.yaml` LLM 1회 → `update_table_analyzed`/`update_column_analyzed`로 **Table/Column `analyzed_description` SET**(`nodes/table.py`·`column.py`). ∴ 인사이트는 이미 제자리(컬럼/테이블)에 있고, 코드노드 `table_insights_json` 은 **중복 블롭**.
- **소비처**(grep): frontend `NodeDetailBase.vue`(risk·fan_in·fan_out·loc·table_coupling·god_flag·in_cycle 표시), `NvlGraph.vue`(`properties.risk` 색). catalog = `labels(n)`·`n{.*}` 무관통과.

## B. 결정 (Decision / Rationale / Alternatives)

### D1. 저장 규칙 = "빈값 미저장" (`SET n += {비어있지 않은 키}`)
- **Decision**: 각 nodes/*.py 의 속성맵을 **비어있지 않은 키만** 담아 `SET n += map`. Neo4j 스키마리스라 SET 안 한 속성은 노드에 없음 → 구문 노드는 framework/health 속성을 자연히 안 가짐.
- **Rationale**: 종류별 하드 분기 없이 "종류에 맞는 속성만" 달성. constitution II(분기 0)·VI(스키마 절제) 강화.
- **Alternatives**: 종류별 dataclass/Cypher 분기 → 분기 증가, 기각. `SET n.x=item.x`(현행) → 빈값 박힘, 기각.

### D2. signature 저장 미포함(필드 유지) → params·return_type 저장
- **Decision**: `signature`를 **Neo4j 저장에서 제외**(루틴은 params·return_type granular 저장이 대체). ★**dataclass 필드 `signature`/`comment`/`annotations`는 유지** — 전수 grep 결과 call_linker `_proc_name(fn.signature)` 호출매칭·three_split/batch/build_context LLM 컨텍스트·module_pre_analysis(Module.comment/annotations)·컴포넌트/모듈 표시명이 사용. 필드 제거는 회귀(persistence-only로 한정).
- **Rationale**: 저장 속성만 정리하면 노드는 깔끔해지고 파이프라인은 무손상. signature⊇params+return(저장 중복).

### D3. annotations + comment → `notes` 단일
- **Decision**: framework 전용 `annotations`와 `comment`를 범용 `notes` 하나로 통합.
- **Rationale**: annotations 가 DBMS 에 빈값으로 남는 문제 해소. 둘 다 "원본 부가 텍스트"라 의미 통합. (사용자 확정 이름 `notes`.)

### D4. health — 계산은 전 지표, 저장은 risk_level(+in_call_cycle)만, 루틴만
- **Decision**: `health_metrics_step` 계산(loc·fan·god·table_coupling·cycle)은 **메모리 유지**(risk 산출 입력). **저장은 `risk_level`(rename of risk)·`in_call_cycle`(rename of in_cycle)만**, 루틴 라벨에만. `loc`·`god_flag`·`table_coupling`·`fan_in`·`fan_out` SET 폐기. fan 은 필요시 CALLS 관계로 즉석 파생.
- **Rationale**: 위험도 품질 불변(계산 입력 그대로) + 잡지표 저장 제거. 사용자 결정(loc·god_flag·table_coupling 불필요).
- **이름**: risk→`risk_level`, in_cycle→`in_call_cycle` (사용자 확정 "명확하게").

### D5. table_insights_json 블롭 폐기 (분배는 이미 존재)
- **Decision**: 코드노드 `table_insights_json` 저장 폐기. 분석 인사이트는 `enrich_table_description_step`이 이미 Table/Column `analyzed_description` 으로 분배(제자리) → 블롭은 중복.
- **Rationale**: 그래프=진실(constitution III). 같은 정보 두 곳(블롭+컬럼) 두면 드리프트.
- **★주의**: 블롭이 보유하던 "**함수별** 컬럼 사용 디테일"은 enrich 합성(테이블/컬럼 레벨)엔 없음. 필요하면 R/W **엣지 속성**으로 두는 게 제자리 — 본 범위 밖(현재는 컬럼 analyzed_description 이 진실). 컬럼이 빈 건 그 컬럼에 인사이트가 없어서(정상).
- **Alternatives**: 블롭 유지 → 중복·드리프트, 기각.

## C. 미해결 (NEEDS CLARIFICATION) — 없음
이름(notes·risk_level·in_call_cycle)·폐기 목록·분배 방침 사용자 확정. 함수별 컬럼 디테일의 R/W 엣지 이전은 별도(범위 밖) 명시.
