# Quickstart: Data-Folder-Only Consumer 검증

이 기능이 동작함을 증명하는 실행 시나리오. (검증 가이드 — 구현 코드 아님.)

## 전제

- antlr 006이 `data/{source, ddl, analysis}`를 생성한 상태(특히 `data/ddl`에 표 정의만).
- analyzer 기동: `.venv/Scripts/python -m uvicorn main:app --port 5502`. Neo4j 접속 가능.

## 시나리오 1 — data/만으로 완료 (SC-001, US1)

```
POST /robo/analyze   { "strategy": "dbms" }     // project_root 없음
```
- 기대: run이 `data/source`·`data/ddl`·`data/analysis`만 읽고 그래프 생성, `complete`.
- 확인: 로그에 외부 로컬 경로 참조 없음. 그래프가 Neo4j에 적재됨.

## 시나리오 2 — 프로시저 포함 프로젝트가 중단 없이 완료 (SC-002, US2) ★핵심

- antlr 006이 rwis를 처리해 `data/ddl`엔 표 16개만, 함수/프로시저는 `data/source`에 둔 상태.
- analyze 실행.
- 확인: step1 parse_ddl이 `data/ddl`만 읽어 **ParseError 없이 완료**(과거: 함수 .sql이 sqlglot에서 ParseError로 전체 중단). 그래프 생성.

## 시나리오 3 — 입력 모드 분기 없음 (SC-003, US3)

```
POST /robo/analyze   { "strategy": "dbms", "project_root": "<아무 로컬경로>" }
```
- 확인: `project_root`를 줘도 안 줘도 **동일 동작**(소스는 항상 `data/`). 옛 클라이언트가 보내도 오류 없음.

## 시나리오 4 — 동작 보존 (SC-004)

- 같은 `data/` 내용으로 변경 전(업로드 경로) vs 변경 후 산출 그래프 비교.
- 확인: 노드·관계·요약이 동일(이 변경은 그 경우 순수 단순화).

## 데이터 정확도 검증 (★ principle-verify)

"안 터짐"으로 끝내지 않는다. rwis 실행 후 **AST·LOGS·그래프를 원문과 대조**:
- TABLE 마킹·R/W·FK·call·reference 가 원문과 일치하는가(6대 체크리스트).
- `data/ddl`만 읽어 누락된 표 정의는 없는가(섞인 파일의 표는 antlr 006 파생으로 들어와 있어야 함).
