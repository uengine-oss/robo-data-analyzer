# Feature Specification: 스테레오타입(역할)을 노드 라벨로 부여 (속성 → 라벨)

**Feature Branch**: `032-stereotype-as-label`

**Created**: 2026-06-27

**Status**: ⛔ SUPERSEDED / REVERTED (2026-06-27) — 유지보수 판단으로 철회. stereotype 은 **속성 유지**(spec 020)가 결론.

> **왜 철회**: stereotype 을 레이블로 올리면 노드가 멀티레이블이 되어 **모든 라벨 소비처**(프론트 타입표시·색상·필터칩, catalog `labels()[0]`, architect)가 "종류 vs 역할"을 매번 분류해야 함 → 하드코딩 역할집합(드리프트) 또는 라벨순서 의존(★Neo4j 라벨 순서 비보장 = catalog `labels()[0]` 버그 부류 재유발). 디버깅·유지보수 비용 큼. 반면 **속성 유지** 시: 라벨=종류 단일(깨끗·결정적), 역할은 속성 1곳에서만(프론트가 `node.properties.stereotype` 로 필터/색상). neo4j 반영도 속성으로 충족. 레이블의 유일 장점(`MATCH (:Command)`)은 현재 소비처 0.
> **조치**: 본 spec 코드(stereotype_label.py·code_node/module 호출) 되돌림(커밋). 현재 그래프 마이그레이션 라벨도 REMOVE(속성만 유지). 역할 UI 는 프론트가 속성 기반으로 처리(후속 frontend spec). 본 문서는 결정 역사 기록용으로 보존.

---

**Feature Branch**: `refactor`

**Created**: 2026-06-27

**원안 Status**: Draft (Phase 1 — additive, 무중단)

**Input**: 사용자 결정 — stereotype(역할: Service/Command/Query/Adapter/Validator/…)을 노드 **속성**이 아니라 **레이블**로 두자. 그래야 프론트 필터/색상·neo4j 쿼리에서 역할로 필터/색칠이 일급으로 쉽고 유지보수가 낫다. (spec 020의 "stereotype=속성" 결정을 역할 가시성 위해 전환.)

## 배경 (왜)

- 현재: 노드 라벨 = antlr 타입 단일(spec 019), **stereotype = 노드 속성**(spec 020, `code_node`/`module`이 SET).
- stereotype은 **전략무관 분석 결과**(LLM이 본문 동작 판단 → `ontology_roles.yaml` 풀에서 선택, `coerce_stereotype` 검증). framework·dbms 양쪽 풀 존재.
- 소비처가 역할로 **필터/색칠**하려면 속성보다 **레이블**이 일급(프론트 라벨 필터 재사용, neo4j `MATCH (:Service)`).

## 무중단 전략 (회귀 0)

stereotype 속성을 **즉시 제거하면** 그걸 읽는 소비처(architect `module_retriever`, 프론트 노드패널 "분류")가 동시에 깨진다. 그래서:
- **이번(032, Phase 1)**: stereotype을 **라벨로 추가** + 속성도 **유지**(additive). 무중단.
- 후속: 소비처를 라벨 기반으로 이관(프론트 spec 036 / architect·catalog spec 037) → 그 다음 stereotype **속성 제거**(analyzer 후속 spec 038).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 역할이 노드 라벨로 존재 (Priority: P1)

분석 후 그래프에서, stereotype을 가진 노드는 그 역할을 **라벨**로도 갖는다(예: `(:FUNCTION:Command)`, `(:FILE:Service)`). 소비처가 `MATCH (:Command)` 또는 라벨 집합으로 역할 필터 가능.

**Independent Test**: framework 재분석(또는 현재 그래프 마이그레이션) 후, stereotype 보유 노드의 `labels(n)`에 그 stereotype이 포함되는지 확인.

**Acceptance Scenarios**:
1. **Given** 함수 노드 stereotype="Command", **When** 분석 적재, **Then** `labels(n)` ⊇ `{FUNCTION, Command}`.
2. **Given** FILE 노드 stereotype="Service", **When** 적재, **Then** `labels` ⊇ `{FILE, Service}`.
3. **Given** stereotype 빈값(미판정), **When** 적재, **Then** 역할 라벨 미부여(antlr 타입만).
4. **Given** stereotype 속성 소비처(아직 이관 전), **When** 조회, **Then** 속성도 그대로 있어 동작(무중단).

### Edge Cases
- stereotype 값 = 검증풀(`coerce_stereotype`) 통제값(Service/Command/DataMutator…) → 식별자 형태라 라벨 안전. 백틱(``:`X` ``)으로 추가 방어.
- dbms 루틴도 stereotype 있음(dbms 풀) → 라벨 부여. dbms 구문(SELECT/IF)은 stereotype 없음 → antlr 타입만(정상).
- 외부 stub(:FUNCTION:EXTERNAL)은 stereotype 없음 → 역할 라벨 없음(정상).

## Requirements *(mandatory)*

- **FR-001**: stereotype 보유 노드(code node·module)는 그 stereotype을 **노드 라벨**로 가져야 한다.
- **FR-002**: stereotype 값별로 묶어 `SET node:`<값>`` (Cypher 동적 라벨 불가 → 값별 배치). 값은 검증풀 통제값 + 백틱 방어.
- **FR-003**: **무중단** — stereotype 속성은 이번 단계에서 유지(소비처 이관 후 별도 spec 제거).
- **FR-004**: 빈 stereotype은 라벨 부여 안 함.
- **FR-005**: framework·dbms 공통(양쪽 풀). 외부 stub·구문 노드는 stereotype 없으면 미부여.

## Success Criteria *(mandatory)*

- **SC-001**: stereotype 보유 노드 100%가 그 stereotype을 라벨로 가진다.
- **SC-002**: 기존 stereotype 속성 소비처 회귀 0(속성 유지).
- **SC-003**: 라벨 부여로 antlr 타입 라벨·식별 id·기존 관계 불변.

## Assumptions
- stereotype 값은 `coerce_stereotype`로 풀 검증됨 → 라벨 안전.
- 현재 그래프(stereotype 속성 보유)는 마이그레이션 쿼리로 즉시 라벨 부여 가능(재분석 없이 검증/활용). 코드 변경은 향후 분석에 영구 적용.
- 소비처(프론트·architect·catalog) 정합·속성 제거는 후속 spec(036/037/038).
