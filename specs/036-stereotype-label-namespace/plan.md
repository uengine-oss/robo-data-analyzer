# Implementation Plan: stereotype 라벨 네임스페이스 분리 (UPPER_SNAKE)

**Branch**: `036-stereotype-label-namespace` (미생성 — 루트 analyzer WIP 보호, 적용 시 결정) | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)

## Summary

analyzer가 노드에 부여하는 stereotype **라벨**(`:Command` 등 PascalCase)이 robo-architect의 DDD/CQRS 라벨과 전역 네임스페이스를 공유해 충돌 → architect ES 인제스천의 라벨기반 `DETACH DELETE`(`_WIPE_LABELS`의 `Command`)가 analyzer 쓰기 함수 노드를 삭제. **근본 = 네임스페이스 분리**: analyzer가 SET 하는 stereotype 라벨만 UPPER_SNAKE로 변환(`Command`→`COMMAND`, `ApplicationService`→`APPLICATION_SERVICE`). ontology 풀·노드 속성 `stereotype`은 PascalCase 유지 → LLM·architect(속성 읽기)·catalog 무영향. 프론트는 역할 표시를 속성 우선으로 1곳 조정.

## Technical Context

**Language/Version**: Python 3.11 (analyzer) · TypeScript/Vue3 (frontend)

**Primary Dependencies**: Neo4j(라벨=전역 네임스페이스, 대소문자 구분) · LangGraph · Vite

**Storage**: Neo4j (공유 DB — analyzer 생산 / architect·catalog 소비)

**Testing**: analyzer CLI 재분석(`cli_scripts/analyze_data_dir.py zapamcom --strategy dbms`) 후 그래프 vs 원문 대조(§6) · 프론트 노드패널 시각 검증

**Project Type**: 멀티서비스 (analyzer 백엔드 생산자 + robo-data-frontend 소비자). architect 측은 별 레포(spec 044).

## Constitution Check

*GATE: 통과 시 구현 진행.*

- **신규 노드 라벨/엣지 0**: 기존 stereotype 라벨의 **표기만** 변경(re-casing). 신규 스키마 없음 → PASS.
- **LLM/SSE 변경 0**: 프롬프트·이벤트 와이어 무관 → 해당 없음.
- **계약 변경 = stereotype 라벨 표기**: 소비처 알람 필요 → 프론트(본 스펙서 처리) + architect(044 교차 알람). 생산자 변경 단일지점.
- **KV 캐시(§8) 무관**: 프롬프트 system 불변.
- 위반/복잡도 추가 없음 → Complexity Tracking 비움.

## Design — 변경 지점 (전수 확정)

### 1. analyzer (생산자) — 단일 함수

- **`shared/neo4j/nodes/stereotype_label.py` · `apply_stereotype_labels`**: stereotype 값을 **라벨로 SET 하기 직전** UPPER_SNAKE로 변환. camelCase→UPPER_SNAKE 헬퍼 1개(`ApplicationService`→`APPLICATION_SERVICE`, `Command`→`COMMAND`, `DataMutator`→`DATA_MUTATOR`). 그룹핑 키도 변환값 기준.
  - 이 함수가 **코드노드(`code_node.py:83`)·모듈(`module.py:55`) 라벨 적용의 단일 chokepoint** → 한 곳만 고치면 FILE:Service·FUNCTION:Command 등 전부 일괄 UPPER_SNAKE.
- **속성 `stereotype`은 불변**: `code_node.py:61`·`module.py:39`의 `"stereotype": fn.stereotype`(PascalCase) 그대로 — architect/프론트 속성 폴백 무중단.
- ontology_roles.yaml 불변(내부 풀·LLM 검증·속성 원천).

### 2. robo-data-frontend (소비자) — 표시 1파일 + stale 정리

- **`src/shared/utils/nodeIdentity.ts`**: 역할 표시(`buildIdentityRows` 86–89)를 **속성 `p.stereotype`(PascalCase) 우선**으로 조회하도록 조정 → `STEREOTYPE_KO` 맵(PascalCase 키) 무수정으로 한글 정상. 라벨(UPPER_SNAKE)은 색·필터·쿼리용으로 그대로 사용.
  - (대안: 맵 키를 UPPER_SNAKE로 동반 갱신 — FR-004는 둘 중 택1 허용. 속성 우선이 최소터치.)
- **`src/shared/stores/navigator.ts:49–50,87`**: 역할 필터 리스트가 현 ontology 풀과 이미 불일치(stale) → 현행 풀의 UPPER_SNAKE 값으로 정정(별건이지만 동반 처리).
- 위치/대문자정규화 기반(`rolesOf`·`kindOf`·`colorLabelOf`·`buildIdentityRows`의 `has()`·GraphTab)은 무변경.

### 3. robo-architect (교차 알람 — 본 스펙 구현 범위 밖)

- 보조 안전판: ES 인제스천 삭제(`event_storming_bridge/persistence.py` 등)를 **공유 라벨이 아닌 자기 표식**(전용 라벨/`session_id`+`size(labels)=1` 가드 누수경로 차단)으로 → 향후 analyzer가 다른 표기를 써도 무사. **robo-architect spec 044에서 처리**(robo-spec 체계). 본 스펙은 contract 알람만 기록.

## Project Structure

```text
specs/036-stereotype-label-namespace/
├── spec.md      # ✅ 작성됨
├── plan.md      # ← 이 파일
└── tasks.md     # 다음 (speckit-tasks / 수동)

변경 파일:
robo-data-analyzer/shared/neo4j/nodes/stereotype_label.py   # UPPER_SNAKE 변환 (+헬퍼)
robo-data-frontend/src/shared/utils/nodeIdentity.ts          # 역할표시 속성우선
robo-data-frontend/src/shared/stores/navigator.ts            # stale 필터 정정
```

**Structure Decision**: 생산자(analyzer) 단일 함수 + 소비자(frontend) 2파일. 신규 디렉토리·노드·엣지 0. architect는 별 스펙(044).

## 검증 (구현 후 — spec §Success Criteria)

1. `python cli_scripts/analyze_data_dir.py zapamcom --strategy dbms` 재분석.
2. Neo4j: 고아 RULE = 0 / 함수 노드 = AST 정의수 일치(38) / stereotype 라벨이 UPPER_SNAKE.
3. `_WIPE_LABELS` 기반 DETACH DELETE 모의 실행 시 analyzer 함수 삭제 0건 (SC-002).
4. 프론트 노드패널 "역할" 한글 표시 회귀 0 (SC-003) — 자가 시각검증.
5. 그래프 vs 원문 line-by-line 품질 재대조(§6) — 프롬프트 견적 건과 합류.
