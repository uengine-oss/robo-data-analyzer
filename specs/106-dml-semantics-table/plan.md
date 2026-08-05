# Implementation Plan: DML 문법 의미표 — R/W 판정의 단일 진실

**Branch**: `main` (브랜치 미분기 — 사용자 WIP 보존, 커밋은 사용자 지시 시) | **Date**: 2026-07-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/106-dml-semantics-table/spec.md`

## Summary

DML 구문 → (relation, dml_type, 대상 추출) 지식이 3곳에 중복 하드코딩되어 TRUNCATE 누락(AMS 40여 건)·FROM 생략 DELETE 오분류(GIOS)가 조용히 발생한다. 이 지식을 **단일 데이터 표**(`pipeline/dml_semantics.yaml`) + **단일 파생 모듈**(`pipeline/dml_semantics.py`)로 옮기고, DBMS 링커·C 리터럴 SQL 판정·dml_type 표준화가 전부 이 표에서 파생하게 한다. LLM 0, 그래프 스키마 변경 0.

## Technical Context

**Language/Version**: Python 3.12 (기존 파이프라인 동일)

**Primary Dependencies**: 표준 `re`, `yaml`(기존 ontology_roles.yaml 로딩과 동일 스택). 신규 외부 의존 0

**Storage**: Neo4j — 스키마 변경 없음 (READS/WRITES 관계 타입·속성 구조 불변, dml_type 값 집합에 TRUNCATE·CREATE 추가)

**Testing**: pytest (기존 tests/ 관례), 대표 코퍼스 전후 엣지 스냅샷 diff

**Target Platform**: 기존 analyzer 런타임 동일

**Project Type**: 단일 서비스 내부 리팩토링 + 결함 수리

**Performance Goals**: 정규식은 로딩 시 1회 컴파일(lru_cache) — 런타임 판정 비용 종전 동일

**Constraints**: 판정 전 과정 결정론(LLM 0), 기존 정상 판정 회귀 0

**Scale/Scope**: 생산 파일 4개 수정 + 데이터 표 1개 + 모듈 1개 신설, 소비자 코드(그래프·스트림·컨텍스트)는 무변경

## Constitution Check

| 조항 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ 합치 | 표 + 정규식 파생, LLM 호출 추가 0. TRUNCATE silent 누락은 "결정론이 덜 돼서"가 아니라 "결정론 지식이 흩어져서" 생긴 결함 — 본 작업이 강화 |
| II. Zero-Branch | ✅ 합치 | 방언 차이는 표의 `dialects:` 섹션 데이터로만. 판정 로직에 언어 `if` 분기 추가 0 |
| III. Single Source of Truth | ✅ 목적 그 자체 | 3곳 중복(`_DML_KW_RE`·`_WRITE_TARGET_RE` / `_C_SQL_*` / `derive_dml_type` 화이트리스트) → 표 1곳. 표 밖 키워드 나열 grep 0 게이트(SC-003) |
| IV. No Silent Failure | ✅ 합치 | 표 로딩·형식 오류는 명시 raise (FR-009). 조용한 기본값 폴백 금지 |
| V. KV-Cache Prompts | N/A | 프롬프트 무변경. 단 권위 op 맵(`table_op`)에 TRUNCATE 값이 새로 흐를 수 있음 — 렌더 검증 항목에 포함 |
| VI. Schema Restraint | N/A | LLM 출력 스키마 무변경 |
| VII. Submit-Tool Wrap | N/A | 에이전트 무변경 |
| VIII. Ownership & Dependency | ✅ 개선 | `derive_dml_type`의 진실이 graph/schema(저수준 계약)에 있으나 호출자 전원이 pipeline 계층 — 진실을 pipeline 의미표 모듈로 이사, graph→pipeline 역참조 없이 정리 (research.md D2) |

**Cross-Service Contracts**: 건드리지 않음. Catalog HTTP 계약 무변경 (`enrich_lineage_from_catalog`는 import 경로만 변경). Architect는 관계 타입 `READS|WRITES`만 소비(전수조사 확인) — 불변.

## Project Structure

### Documentation (this feature)

```text
specs/106-dml-semantics-table/
├── spec.md
├── plan.md              # 이 파일
├── research.md          # 결정 D1~D7 + 근거
├── data-model.md        # 의미표 YAML 스키마 + ReadsWrites 불변 확인
├── contracts/
│   └── dml-semantics-contract.md   # 표→소비자 파생 계약, spec 006 FR-013 대체 명시
├── quickstart.md        # 검증 절차 (테스트·코퍼스 대조 명령)
└── tasks.md             # /speckit-tasks 산출 (이후 단계)
```

### Source Code (repository root)

```text
pipeline/
├── dml_semantics.yaml               # [신설] 의미표 — 공통 코어 + dialects
├── dml_semantics.py                 # [신설] 로더 + 파생(키워드 regex·대상 추출 regex·dml_type 표준화)
└── steps/
    ├── step5_linking/link_tables/
    │   ├── link_dbms_tables_step.py # [수정] _DML_KW_RE·_WRITE_TARGET_RE 제거 → 표 파생 소비
    │   └── regex_table_linker.py    # [수정] _C_SQL_*·_SQL_KEYWORD_RE 제거 → 표 파생 소비, derive import 경로 변경
    └── step8_post_processing/
        └── enrich_lineage_from_catalog_step.py  # [수정] derive_dml_type import 경로만 변경
graph/schema/
└── graph_edges.py                   # [수정] derive_dml_type 본체 제거(이사), ReadsWrites 주석 갱신

tests/
├── pipeline/dml_semantics/          # [신설] 표 로딩·파생·3소비처 일치 계약 테스트
├── pipeline/step5_linking/…         # [수정] TRUNCATE·FROM 생략 DELETE·CTAS·MERGE USING 케이스 추가
└── (기존 전량 회귀)
```

**Structure Decision**: 의미표는 `ontology_roles.yaml`과 같은 "pipeline 소유 언어 데이터 표" 관례를 따른다. 파생 모듈을 별도 파일로 두는 이유: languages.py(확장자 정책)와 책임이 다르고, 소비자 3곳이 공유하는 최협소 안정 소유자가 필요(헌법 VIII).

## 현재 흐름 → 목표 흐름 (배선 전수)

### 생산자 (ReadsWrites를 만드는 모든 곳 — 전수)

| 생산자 | 현재 | 목표 |
|---|---|---|
| `link_dbms_tables_step._link` | `_DML_KW_RE`(6키워드)·`_WRITE_TARGET_RE`(DELETE FROM만) 하드코딩 | 표 파생 `detect_regex()`·`write_target_regex()` 소비. TRUNCATE·FROM 생략 DELETE·CTAS 자동 포함 |
| `regex_table_linker` C 경로 (`_c_sql_semantic_edges`) | `_C_SQL_START_RE`·`_C_SQL_WRITE_TARGET_RE`·`_C_SQL_READ_TARGET_RE` 하드코딩 | 동일 표 파생. read 절에 USING 추가(MERGE 소스 READS — 의도된 개선) |
| `regex_table_linker` 스캔패턴 경로 | `op_mappings`(에이전트 발견 op) → `_op_to_relation` + `derive_dml_type` | 무변경 — op_mappings는 프로젝트 커스텀 영역(에이전트 관할). `derive_dml_type` import 경로만 변경 |
| `enrich_lineage_from_catalog_step` | catalog `/lineage` 응답 → `derive_dml_type`·`normalize_relation` | 무변경 — import 경로만. catalog가 TRUNCATE를 보내면 이제 강등 없이 보존(개선) |

### 소비자 (dml_type·relation을 읽는 모든 곳 — 전수조사 결과)

| 소비자 | dml_type 취급 | TRUNCATE·CREATE 신규 값 영향 |
|---|---|---|
| `graph/edges/reads_writes.py` (upsert) | pass-through (dml_type·dml_types[]·locations) | 없음 — 열거 제한 없음 |
| `graph_delta_stream.py:99` (frontend 스트림) | pass-through | 없음 — frontend 실코드에 dml_type 필터 없음(전수 grep: spec 문서·evidence만) |
| `build_context_step` 권위 op 맵(`table_op`) | 값 그대로 LLM 컨텍스트로 (SELECT→READ 치환 외) | TRUNCATE가 권위 op로 노출 — 정확한 정보이므로 허용. 검증 단계에서 렌더 1건 육안 확인 |
| `enrich_table_description_step` | evidence identity 구성요소로 pass-through | 없음 |
| `search/ranking/graph_access.py` | relation 타입만 소비 | 없음 |
| architect `traceability.py`·`bpm_context_builder.py` | 관계 타입 `READS\|WRITES`만 소비, dml_type 미사용(전수 grep) | 없음 |
| `normalize_relation` | READS/WRITES 정규화 — 이번 작업과 무관 | 없음 — enum 불변 |

### 대체되는 과거 계약

- **spec 006 FR-013** ("본문 첫 DML 키워드 SELECT/WITH=READS · INSERT/UPDATE/DELETE/MERGE=WRITES, write 대상은 INSERT INTO/UPDATE/DELETE FROM/MERGE INTO 직후"): 키워드 집합과 대상 추출 규칙의 **출처**가 의미표로 대체된다. "자기 줄 스캔·주석 제거·문자열 보존" 계약은 유지. contracts/ 문서에 명시.

## 마이그레이션 / 롤백

- 데이터 마이그레이션 없음 — 매 run이 전량 재구축(헌법 Cost & Safety: destructive run)이므로 다음 분석 실행 시 새 판정이 그대로 반영된다.
- 롤백 = 코드 revert. 그래프는 다음 run에서 원상.

## Complexity Tracking

위반 없음 — 표 + 파생 모듈 1개 신설이 전부이며, 제거되는 하드코딩(정규식 상수 6개 + 화이트리스트 1개)이 신설보다 많다.
