# Phase 1 Data Model: Data-Folder-Only Consumer

analyzer가 다루는 입력 개념. (신규 영속 모델 없음 — 입력 배선의 단일화가 핵심.)

## Entity: Codebase (분석 입력 — 단일 진실)

run 1회의 입력을 표현하는 기존 모델. 이 기능은 그 **기본값을 고정**해 단일 진실로 만든다.

| 필드 | 변경 전 | 변경 후 |
|---|---|---|
| `root` | `project_root or data/source` | **항상 `data/source`** |
| `ddl_dir` | `None → root`(폴더 전체) | **`data/ddl`**(표 정의만) |
| `strategy` | `framework`/`dbms` | 불변 |
| `has_xml_mapper`·`locale`·`datasource` | — | 불변 |

**불변식**: `root`·`ddl_dir`는 항상 `data/` 하위. 외부 로컬 경로를 가리키지 않는다.

## Entity: Standard Data Set (소비 대상)

analyzer가 읽는 유일한 입력. 파서(antlr 006)가 생성.

```
data/source/    소스 원본 → step3 스캔, step6 grep 링킹
data/ddl/       표 정의만 → step1 parse_ddl(sqlglot)   ← 프로시저 없음(antlr 006 보장)
data/analysis/  AST JSON → step2 load_ast(노드 스키마 불변)
```

## Removed: project_root 경로 모드 (제거 대상)

| 위치 | 현재 동작 | 변경 |
|---|---|---|
| `AnalyzeRequest.project_root` | 소스 루트를 로컬 폴더로 바꿈 | **수용하되 무력화**(역호환) |
| 파이프라인 8지점 | "로컬경로 vs data" 분기 | 단일 경로(`data/`)로 통일, 분기 제거 |

**상태**: run = 파괴적 전체 재빌드(불변) — 시작 시 Neo4j wipe + logs rmtree, `data/`만 읽어 그래프 생성.
