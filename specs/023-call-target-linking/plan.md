# Implementation Plan: DBMS CALL 노드 → 호출 대상 링킹 계약 정정

**Branch**: `023-call-target-linking` | **Date**: 2026-06-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/023-call-target-linking/spec.md`

## Summary

DBMS 호출 링킹의 저장 진실을 "프로시저↔프로시저"에서 "CALL 노드 → 호출 대상"으로 정정한다. 두 축: ① `CallLinker`가 호출 엣지 출발점을 프로시저 루트가 아니라 **CALL 노드 자신**으로 내보낸다. ② 호출을 프로시저 단위로 계산하던 백엔드 소비처는 CALL 노드를 소유 루틴으로 거슬러 해소하는 **단일 결정론 해소기 `routine_call_pairs`**를 공유한다. 이 해소기는 옛 `CallLinker`가 만들던 proc→proc 집합을 정확히 재현하므로 컴포넌트·건강·분석순서 결과가 정정 전과 동등(parity)하다. 프론트는 보기모드가 이미 hop-over(resolveModule/resolveRoutine)하고 일반 탐색기는 원본을 표시하므로 무변경.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: LangGraph(StateGraph), neo4j 드라이버, igraph/leidenalg(컴포넌트 군집)

**Storage**: Neo4j (그래프 단일 진실, 매 실행 DETACH DELETE 후 전량 재빌드)

**Testing**: 실제 실행 검증 — antlr 파싱 → analyzer 풀 실행 → 그래프 덤프 원문 대조(헌법/원칙 §6). DBMS 샘플 = `data/source/RWIS`.

**Target Platform**: 서버(로컬 파이프라인), 단일 동시 실행

**Project Type**: 데이터 분석 파이프라인 (single project, `analysis_flow/step*`)

**Performance Goals**: 링킹·해소 모두 O(노드+엣지). 해소기는 노드당 부모체인 1회 walk.

**Constraints**: 링킹·컴포넌트·건강은 LLM 0(결정론) — 헌법 I. 새 의존성 0.

**Scale/Scope**: 레거시 PL/SQL 수천~수만 노드. CALL 노드 = 호출 구문 수.

## Constitution Check

*GATE: Phase 0 전 통과 필수, Phase 1 후 재확인.*

| 원칙 | 평가 |
|---|---|
| **I. 결정론 우선** | ✅ CallLinker·routine_call_pairs·컴포넌트·건강 전부 순수 함수, LLM 0. |
| **II. 분기 0 전략** | ✅ 새 전략·`if strategy` 분기 추가 없음. 변경은 기존 DBMS step 내부 + 소비처. 파이프라인 선언 무변경. |
| **III. 단일 진실** | ✅ 해소 규칙을 `routine_call_pairs` 한 곳에 두고 소비처 공유. 관계명 `CALLS`는 `rel_types.py` 그대로(끝점 의미만 정정). |
| **IV. silent fail 금지** | ✅ 소유 루틴 해소 실패 호출은 집계·로그로 표면화(FR-011). |
| **VIII. 응집 합본** | ✅ 호출 그래프 토폴로지 로직(`_root_of`+`routine_call_pairs`)을 호출 링킹 권위 모듈(`call_linker.py`)에 합본. |
| **Cross-Service Contracts** | ⚠️ ↓출력(robo-architect/프론트행 Neo4j 스키마)의 **CALLS 끝점 의미** 변경 → 프론트 영향 검토 완료 → **무변경(라이브 검증)**: 보기모드 hop-over(resolveModule/resolveRoutine) 강건, 일반 탐색기는 원본 표시 정상. antlr 입력·catalog 계약 무영향. |

**Gate 결과**: PASS (위반 0).

## Project Structure

### Documentation (this feature)

```text
specs/023-call-target-linking/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — 전수조사 결과·결정 근거
├── data-model.md        # Phase 1 — 노드/엣지 계약 변화
├── quickstart.md        # Phase 1 — 실행 검증 가이드
├── contracts/
│   └── call-graph.md    # CALLS 엣지 계약 + routine_call_pairs 내부 계약
└── checklists/
    └── requirements.md  # 품질 체크리스트
```

### Source Code (수정 대상 — 구현 완료)

```text
analysis_flow/step4_linking/stage2_extract_calls/
└── call_linker.py            # CallLinker.link() 출발점=CALL 노드 / +routine_call_pairs 신설

analysis_flow/step6_analyze_code/
└── batch_analyze.py          # DBMS 분석순서: calls→ordering 엣지를 routine_call_pairs 로
    # analyze_code_step.py    # 무변경 — :145 per-node 경로는 Framework 전용(DBMS early-return)

analysis_flow/step7_post_processing/
├── build_components_step.py  # _cluster 의 internal_edges → routine_call_pairs (자기루프 != 유지)
└── health_metrics_step.py    # _compute_metrics 의 internal_edges → routine_call_pairs

data_models/graph_edges.py            # Calls docstring 갱신 (끝점 의미)
analysis_flow/.../link_calls_by_ast_step.py  # docstring 갱신

# 무변경(영향 없음): shared/lang/call_classifier.py (FRAMEWORK) / shared/neo4j/edges/calls.py
#   (id MATCH라 쿼리 그대로) / Calls dataclass 필드 / rel_types.py / robo-data-frontend 전체
```

**Structure Decision**: 기존 `analysis_flow/step*` 유지. 새 파일·모듈 없음. `routine_call_pairs`는 `_root_of`가 사는 `call_linker.py`에 합본(헌법 VIII).

## 설계 핵심 — routine_call_pairs (parity 근거)

```text
옛 CallLinker:  CALL 발견 → caller=_root_of(CALL)=루트루틴 → Calls(루트루틴 → 대상)  [+self drop, +dedup]
새 CallLinker:  CALL 발견 → Calls(CALL 노드 → 대상)                                    [self/dedup 불필요]

routine_call_pairs(code_nodes, calls):
  internal Calls 마다 → (root_of(caller), callee)
  중첩 CALL이 거슬러 올라가 만난 재귀(caller!=root, root==callee) 드롭 / 직접 자기참조(caller==root==callee) 보존 / dedup
  = 옛 CallLinker proc→proc 집합과 동일 → cycle·정렬·Leiden·fan 입력 동일 → 결과 동등(SC-003·004)
```

## Phase 0 / 1 산출물

- **Phase 0** → [research.md](research.md): 4 소비처 전수조사·전략 라우팅·parity 근거·프론트 무변경 근거.
- **Phase 1** → [data-model.md](data-model.md), [contracts/call-graph.md](contracts/call-graph.md), [quickstart.md](quickstart.md).
- **Agent context**: CLAUDE.md SPECKIT 마커 = 이 plan (이미 023).

## Complexity Tracking

위반 없음 — 비움.
