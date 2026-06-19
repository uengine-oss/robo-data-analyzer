# Data Model: 실시간 스트림 + 일관 보기모드

본 기능은 **새 영속 엔티티 0** — 기존 노드/관계/이벤트의 *표시·전송 시점*만 바꾼다. 관련 엔티티 정리.

## 스트림 이벤트 (NDJSON, 전송 단위)

| 이벤트 | 형식 | 본 변경 |
|---|---|---|
| `node_event` | `{type, op:"merge", label, id, props}` | **더 이른 step**에서 골격 노드도 emit (형식 불변) |
| `relationship_event` | `{type, op:"merge", rel_type, from:{id,label?}, to:{id,name?,label?}, props}` | 골격 관계도 emit (형식 불변) |
| `message` | `{type, content}` | **각 analyzer step 완료 시** emit (신규 호출처, 형식 불변) |
| `phase_event` | `{type, event, node?}` | 불변 |
| `processing_event` | `{type, step, entity_kind, entity_id, status}` | (옵션) 프론트 콘솔 핸들러 추가 검토 |

## 그래프 노드 (표시 대상) — 변경 없음, 참고

- **골격(step1~5 생산, 라이브 emit 추가 대상)**: TABLE·COLUMN(step1) · FILE·CLASS·PROCEDURE·METHOD·구문(SELECT/IF/…)·VARIABLE·PARAMETER(step2) · 코드노드(step3 framework).
- **LLM(step6 생산, 현행 emit)**: 분석된 코드노드 보강 + RULE·EXAMPLE·QUESTION.
- 식별: `id` 단일(017). 라벨: 자기 antlr type 단일(019). 표시속성: 종류맞춤·빈값 미저장(020).
- **숨김(프론트 미표시)**: RULE·EXAMPLE·QUESTION (semanticLinkMap/패널 전용).

## 그래프 관계 — 변경 없음, 참고

- **골격 표시(라이브 emit 추가)**: HAS_COLUMN(step1) · HAS_MEMBER·PARENT_OF·BELONGS_TO(step2) · READS·WRITES·CALLS·INIT_BY·FK(step4).
- **숨김(emit해도 미표시, 일관)**: REFERENCES·NEXT·BRANCH·AFFECTS_TABLE·HAS_RULE·HAS_EXAMPLE·HAS_QUESTION (프론트 `HIDDEN_LINK_TYPES`/`SEMANTIC_LINK_TYPES`).
- 끝점: 역할명 id(017/018). Table 끝점=id(골격), name(step6 AFFECTS_TABLE만).

## 보기모드 (프론트 표시 상태) — 변경 없음, 참고

| 모드 | 기준 속성/관계 | 전략 무관 |
|---|---|---|
| 영역(area) | `component_id`(그룹) + `component_name`(라벨) | ✅ build_components 공통 |
| 건강(health) | `risk_level`(색) | ✅ health_metrics 공통 |
| 모듈간(module) | HAS_MEMBER + PARENT_OF + READS/WRITES → 가상링크 | ✅ 타입집합+PARENT_OF |
| 기본(default) | 라벨별 색 | — |

## 병합 규칙 (라이브 중복 방지)

- 같은 `id` 노드가 골격(step1~5)·LLM(step6)에서 두 번 와도 프론트 `nodeMap.set(id)` = 덮어쓰기 1개. 골격이 구조 먼저, step6가 summary/logical_name 보강. 깜빡임·중복 0.
- 카운트 = `nodeMap`/`linkMap` 고유 size(숨김 제외).
