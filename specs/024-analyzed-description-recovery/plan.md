# Implementation Plan: 테이블·컬럼 분석설명 유실 복구 + 식별자 빈값 정규화

**Branch**: `refactor` (spec dir `024-analyzed-description-recovery`) | **Date**: 2026-06-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/024-analyzed-description-recovery/spec.md`

## Summary

테이블·컬럼 analyzed_description 유실(6/16, 61/153)의 근본 = **식별자 형식 불일치(빈 db 선두 점)** + **id 조립 DRY 위반**(Table.id property `.RWIS.X` vs step7/insights fqn `RWIS.X`). 단일 FQN 헬퍼(빈 구성요소 생략)로 통일해 생성·후처리가 같은 키를 내게 하고, MERGE 저장에 빈값미저장 적용, id-match 갱신 0건 시 표면화. 재분석으로 16개 전부 채워짐 + 회귀0 검증.

## Technical Context

**Language/Version**: Python 3.x (analyzer, branch refactor) · LangGraph 파이프라인 · Neo4j

**Primary Dependencies**: Neo4j(MERGE/SET), 사내 LLM(frentis-ai-model, table_description 생성), DDL 파서

**Storage**: Neo4j 그래프 (TABLE/COLUMN 노드 id = `{db}.{schema}.{name}[.{col}]`)

**Project Type**: 분석 백엔드(마이크로서비스). 산출물 소비처 = catalog·frontend·architect(graph read), text2sql(별도 스키마)

**Performance/Constraints**: 결정론 우선(헌법 I) · 빈값미저장(spec 020) · 전략무관(DBMS/framework 공통)

**Scale/Scope**: 현 RWIS 16테이블/153컬럼/426코드노드. db 빈값(오라클). db 있는 환경은 구조 일반화.

## Constitution Check

*GATE: Phase 0 전 통과. Phase 1 후 재검.*

| 원칙 | 준수 | 근거 |
|---|---|---|
| I. 결정론 우선 | ✅ | 식별자 정규화·빈값미저장·no-op 표면화 전부 결정론(LLM 무관). analyzed_description 생성 LLM은 불변. |
| II. 분기0 전략 | ✅ | FQN 헬퍼는 db 유무로 자연 적응(if-strategy 분기 없음). 특정 DBMS 하드코딩 0. |
| III. id 단일·DRY | ✅ | ★핵심 — id 조립을 단일 헬퍼로(현재 ≥2곳 중복=버그원인). spec 017(id 단일화) 후속. |
| IV. 빈값미저장 | ✅ | MERGE writer에 nonempty 적용 — spec 020 정책을 테이블/컬럼에도 일관. |
| V. 조용한실패 표면화 | ✅ | id-match 갱신 0건이면 경고(현재 조용한 no-op = 유실 은폐). |
| VI. 회귀금지·실행검증 | ✅ | 재분석 실행으로 16개 채움 + 기존 6개·소비처 회귀0 확인(헌법). |

**게이트 통과**: 위반 0. 이 작업은 헌법(특히 id 단일·빈값미저장·조용한실패)을 **위반한 현재 상태를 헌법에 맞추는** 수정.

## 블래스트 레이디어스 (전수조사 — 사실)

**식별자 조립/소비 지점 (실측 grep)**:
- `data_models/graph_nodes.py:78` — `Table.id = f"{db}.{schema}.{name}"` ★빈 db→선두점 출생지(단일진실 후보).
- `data_models/graph_nodes.py:91` — `Column.id` (DDL 파싱이 `{db}.{schema}.{table}.{col}`로 생성).
- `step7 enrich_table_description_step.py:117` — `f"{r.db}.{r.schema_}.{r.name}"` (table write id, **DRY 위반 중복**).
- `step7:124` — `f"{table_fqn}.{cname}"` (column write id, insights fqn 기반).
- `step6 table_insights` table_fqn = `RWIS.X`(2조각) ← reads/writes fqn 원천(step4 링킹). ★Table.id(3조각)와 불일치 = 유실 직접원인.
- `step5 fetch_table_samples_step.py:51` — `t.id` 사용(정식 Table.id).
- `embed_step.py:145` — `c.id.split(".")` + `parts[-2]` (뒤에서 세기 → **선두 변경에 안전**).

**판정**: 선두점 제거(빈 db 생략)는 embed_step(음수 인덱스) 안전. 외부 소비처(catalog/frontend/architect)는 id를 **불투명 키로 read** → 재분석이 새 id 생성, 신선하게 읽음(재구성·점위치 의존 없음 — text2sql은 별도 스키마라 무관). architect ingestion은 이미 깨짐(별개 todo). → **근본수정(B) 블래스트 관리가능.**

**결정: A(표면정합) 기각, B(근본) 채택** — 단일 FQN 헬퍼(빈 구성요소 생략)를 id 조립 전 지점(Table.id·Column id·step7 write·insights fqn 원천)에 적용. DRY 복원이 곧 버그 제거(원칙 §3 탈바꿈).

## Project Structure

```text
specs/024-analyzed-description-recovery/
├── plan.md · research.md · data-model.md · quickstart.md · contracts/ · tasks.md(다음)

[수정 대상 — 구현 시]
shared/neo4j/props.py 또는 신규 fqn 헬퍼   # ★단일 fqn(db,schema,name[,col]) — 빈 구성요소 생략
data_models/graph_nodes.py                  # Table.id·Column id → 헬퍼 사용
shared/neo4j/nodes/table.py·column.py       # MERGE에 nonempty 적용 + update_analyzed 0건 경고
analysis_flow/step7_post_processing/enrich_table_description_step.py  # write id를 헬퍼로(중복 제거)
analysis_flow/step6.../ (insights fqn 원천)  # table_fqn도 같은 헬퍼로 정규화(구현 시 원천 확정)
```

**Structure Decision**: 기존 구조 유지. 핵심 = **fqn 헬퍼 신설(단일진실) + 전 조립지점 위임**. 새 단계·분기 없음.

## Complexity Tracking

> 위반 없음 — 비움. (오히려 헌법 위반 상태를 정상화)
