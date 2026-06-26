# Phase 1 Data Model: 026-scan-contract-realign

신규 노드/관계/Neo4j 스키마 **없음**. 변경은 **에이전트 출력 모델의 필드명 1개**뿐.

## 변경: ScanResult 필드 리네임

| 항목 | Before | After |
|---|---|---|
| 필드명 | `framework_code_nodes` | **`framework_runtime_functions`** |
| 모델 | `ScanResult` (data_models/agent_output.py) | 동일 |
| 타입 | `list[str]` | 동일 |
| 의미 | 호출 분류 시 드롭할 프레임워크/런타임 함수 이름(글로브 패턴) | 동일 |
| description | "프레임워크/런타임 함수 이름 (업무 로직 아님 — 호출 분류 시 드롭)" | 동일 의미 유지(+"프로젝트 함수 아님" 명시 강화 가능) |

- **상호 배타 불변식**: `framework_runtime_functions ∩ project code_nodes(bare name) = ∅` 이어야 정상. 위반 시 FR-003 가드가 표면화.
- 소비처(키/속성 접근)도 동일 신규명으로 갱신: `scan_agent.py`, `link_calls_by_name_step.py`(`.get(...)` 키), `residual_pattern_loop.py`.
- 내부 분류기 파라미터 `framework_names`(call_classifier) 는 **불변**(계약 아님).

## 비대상
- Neo4j 노드/엣지/라벨/속성: 불변.
- 분류 알고리즘 출력(Calls 엣지 scope internal/external): 불변.
