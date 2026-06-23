# Phase 1 Data Model: 호출 그래프 계약 변화

데이터 모델 자체(dataclass 필드)는 **무변경**. 바뀌는 것은 DBMS에서 `Calls` 엣지 **끝점의 의미**와, 그로부터 파생되는 루틴 단위 관계의 도출 방식이다.

## 엔티티 (기존 — 무변경 필드)

### CALL 노드 (CodeNode, `own_ast_type()=="CALL"`)
- PL/SQL 호출 구문. id = `{parent}:CALL:{line}`(+uniqueness 접미).
- `signature` = 콜리 이름(빌트인 prefix 판정·단순명 추출에 사용).
- PARENT_OF로 소유 루틴(부모 체인 최상위 = 루트 PROCEDURE/FUNCTION/TRIGGER)에 연결됨.
- 이번 정정에서 `Calls` 엣지의 **출발점(caller_id)** 역할을 맡는다.

### 호출 대상 (CodeNode 정의 루트 또는 외부 stub)
- internal: 분석된 PROCEDURE/FUNCTION/TRIGGER 정의 노드(parent_id None).
- external: `:FUNCTION:EXTERNAL` stub(+`:MODULE:EXTERNAL`+`:PACKAGE`), `upsert_calls`가 생성.

### Calls 엣지 (`data_models/graph_edges.py`) — 필드 무변경, 의미 변경
- `caller_id` / `callee_id` / `scope(internal|external)` / `callee_*`(external 메타).
- **DBMS 정정 후**: `caller_id = CALL 노드 id`, `callee_id = 호출 대상 id`.
- **FRAMEWORK**: 변동 없음 — `caller_id = 함수 id`, `callee_id = 함수 id`.
- 정체성 = (caller_id, callee_id). CALL 노드 id가 호출 지점마다 유일하므로 호출 지점 단위로 저장된다(저장 단계 collapsing 없음).

## 저장 진실 vs 파생물

| 관계 | 저장? | 출처 |
|---|---|---|
| CALL 노드 → 대상 (DBMS) | ✅ 저장 (CALLS) | `CallLinker.link()` → `upsert_calls` |
| 함수 → 함수 (FRAMEWORK) | ✅ 저장 (CALLS) | `classify_call_sites` (무변경) |
| 프로시저 ↔ 프로시저 | ❌ 저장 안 함 | `routine_call_pairs` 파생 |
| 모듈 ↔ 모듈 | ❌ 저장 안 함 | 프론트 `resolveModule` 파생(별도 스펙) / 백엔드 미사용 |

## 파생 관계 — routine_call_pairs (백엔드 단일 해소기)

- 입력: `code_nodes`(부모 체인용), `calls`(저장된 Calls).
- 규칙: internal Calls마다 `(_root_of(caller=CALL노드), callee)`. dedup. self(루트==callee) 제외. 해소 실패 건 집계.
- 출력: `[(루트루틴 id, 대상루틴 id)]` — 옛 proc→proc 집합과 동일.
- 소비처: `build_components._cluster`, `health_metrics._compute_metrics`, `batch_analyze`(DBMS 정렬).

## 상태 전이 / 생명주기

- 호출 링킹은 매 실행 전량 재빌드(증분 없음). 그래프는 실행 시작 시 DETACH DELETE.
- 순서: step2(노드·PARENT_OF 저장) → step4(CALL→대상 Calls 저장) → step6(정렬·분석, routine_call_pairs) → step7(컴포넌트·건강, routine_call_pairs).

## 불변식 (검증 가능)

- INV-1: DBMS internal Calls의 모든 caller_id는 `own_ast_type()=="CALL"` 노드다.
- INV-2: DBMS Calls에 caller가 프로시저 루트인 엣지가 없다.
- INV-3: 빌트인 prefix 콜리에 대한 Calls·대상 노드가 없다.
- INV-4: `routine_call_pairs` 출력에는 self(루트==대상) 쌍이 없다.
- INV-5: FRAMEWORK Calls 집합은 정정 전과 완전히 동일.
