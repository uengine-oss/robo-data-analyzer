# Contract: 호출 그래프 (CALLS 엣지 + routine_call_pairs)

## 1. ↓ 출력 계약 — CALLS 엣지 (robo-architect/프론트행 Neo4j 스키마)

관계 타입명 `CALLS`(`shared/neo4j/rel_types.py`)는 **불변**. 끝점 의미만 정정.

| 전략 | caller (출발) | callee (도착) | scope |
|---|---|---|---|
| DBMS | **CALL 노드**(`own_ast_type==CALL`) | 대상 PROCEDURE/FUNCTION/TRIGGER 또는 `:FUNCTION:EXTERNAL` stub | internal / external |
| FRAMEWORK | 함수/메서드 | 함수/메서드 또는 stub | internal / external (무변경) |

- internal: `(caller)-[:CALLS {scope:'internal'}]->(callee)` — 양 끝 기존 노드 MATCH.
- external: stub(`:FUNCTION:EXTERNAL`)+컨테이너(`:MODULE:EXTERNAL`)+스키마(`:PACKAGE`) 생성 후 연결. 출발점은 CALL 노드.
- 다중성: 호출 지점(CALL 노드)마다 1 엣지. 같은 대상 N회 호출 = N개 CALL 노드 = N개 엣지.
- `upsert_calls`(`shared/neo4j/edges/calls.py`) 쿼리 **무변경** — id로 MATCH/MERGE하므로 끝점 id만 달라짐.

### 다운스트림 영향 (헌법 Cross-Service Contracts)
- **robo-data-frontend**: 영향 검토 완료 → **무변경(라이브 검증)**. 보기모드(모듈간 `resolveModule`·건강 `resolveRoutine` 등)는 끝점을 소유 모듈·루틴으로 해소하므로 무회귀. 일반 탐색기는 원본 그대로 표시가 정상(hop-over는 보기모드 전용). 후속 스펙 불필요.
- **antlr-code-parser**(↑입력), **catalog**(↔): 무영향.

## 2. 내부 계약 — routine_call_pairs (백엔드 단일 해소기)

```
routine_call_pairs(code_nodes: list[CodeNode], calls: list[Calls]) -> list[tuple[str, str]]
```

- **목적**: 저장된 CALL→대상 Calls를 프로시저 단위 (루트루틴, 대상루틴) 쌍으로 해소. 호출을 proc 단위로 계산해야 하는 모든 백엔드 소비처의 **유일한** 출처.
- **규칙**:
  1. `scope=="internal"` Calls만.
  2. caller(CALL 노드)를 부모 체인 최상위 루트루틴으로 해소(`_root_of`).
  3. callee는 이미 대상 루트 정의 노드.
  4. self(루트==대상, 재귀) 쌍 제외.
  5. (루트, 대상) 중복 제거.
  6. 해소 실패(부모 체인 끊김·callee 부재)는 드롭하되 건수 관측 가능(silent fail 금지).
- **결정론**: 순수 함수, 입력 같으면 출력 같음(LLM 0). 정렬로 재현성.
- **불변식**: FRAMEWORK calls(이미 함수↔함수)에 적용 시 항등(루트가 자기 자신) — 호출해도 안전.

### 소비처 (이 계약을 쓰는 곳)
| 소비처 | 용도 |
|---|---|
| `build_components_step._cluster` | Leiden 군집 입력 엣지 |
| `health_metrics_step._compute_metrics` | fan_in/out·cycle 입력 엣지 |
| `batch_analyze`(DBMS) | 바텀업 분석 순서(cycle/ordering) 입력 엣지 |

## 3. 검증 계약 (acceptance와 연결)

- DBMS 그래프 덤프: caller가 CALL 노드인 internal Calls 비율 100%(SC-002), caller가 프로시저 루트인 Calls 0건(SC-001).
- 빌트인 콜리 Calls/노드 0건(SC-005).
- 정정 전/후 동일 샘플: 컴포넌트 구성 동등(SC-003), fan/cycle 동등(SC-004), FRAMEWORK Calls 동일(SC-006).
