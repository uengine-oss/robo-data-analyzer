# Implementation Plan: MCP 레거시 검색 결과 정예화

**Branch**: `057-cluster-result-refinement` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/057-cluster-result-refinement/spec.md`

## Summary

아키텍트 스킬이 받는 cluster_retrieve 결과를 "유관한 것의 전문"으로 만든다.
세 층을 각자 제자리에서 고친다(책임 직선화):

1. **선정(검색 코어)** — `search/retriever.py::_retrieve_global_anchors` 의 앵커 선발을
   점수 구조 적응형 게이트(상대 격차)로, support root 자격을 게이트 연동으로 변경.
   코어 반환 **형태** 불변(REST 프론트 무영향). 상수·정책은 골드 전수 A/B 로 채택.
2. **내용(MCP 표면)** — `api/mcp_app.py` 에서만 계층화 변환: 앵커=rule·example 전량 +
   `code_text`, evidence=요약·관계근거·`code_text`(rules 제외), TABLE=컬럼 전량 부착,
   `search_text`/`element_id` 제거, 동일 statement rule 병합. REST 라우터 무변경.
3. **직렬화(MCP 반환형)** — 도구가 dict 대신 compact `ensure_ascii=False` JSON **문자열**을
   반환해 SDK 의 escape+indent 텍스트화·structuredContent 이중 전송 경로를 우회
   (실측 139.9KB→67.3KB, 여기에 계층화로 추가 감축).

## Technical Context

**Language/Version**: Python 3.12 (analyzer venv)

**Primary Dependencies**: FastAPI + FastMCP(mcp SDK, streamable-http), neo4j driver,
rank_bm25, numpy (모두 기존 — 신규 의존성 0)

**Storage**: Neo4j (기존 그래프 — `code_text` 노드 속성, `HAS_COLUMN`→COLUMN 기존 수집분 재사용)

**Testing**: unittest(`search/test_global_anchor.py` 관례) + `search/eval/anchor_ab_eval.py`
골드 전수 A/B + 실API(REST 15502) 검증 질의 + architect verify_052 e2e

**Target Platform**: 로컬 Windows(robo-workspace 러너), 배포 Linux 동형

**Project Type**: 기존 단일 서비스 내부 변경 + MCP 표면 계약 변경(소비자=robo-architect 스킬)

**Performance Goals**: 검색 지연 기존 동등(추가 쿼리는 TABLE 컬럼 1회뿐), MCP 도달 크기
검증 질의 기준 ≤ 원내용 1.1배

**Constraints**: 골드 recall 기존 이상(하락 시 불채택), REST 계약 불변, 사용자 WIP 보존,
커밋 금지(워킹트리 유지), 서비스 재시작은 순차

**Scale/Scope**: retriever 선발부 ~40줄, mcp_app 변환부 ~80줄, 테스트 ~150줄, eval 스윕 스크립트

## Constitution Check

*GATE: pass (Phase 0 전·Phase 1 후 재확인 완료)*

- **I. Determinism-First**: 게이트는 순수 점수 산술(절벽 검출)·정렬 — LLM 0. 계층화 변환도
  결정론 재구성. ✅
- **II. Zero-Branch Strategy**: 파이프라인 전략 무관(검색 경로만). detail 프로파일 분기는
  MCP 표면 1곳의 명시적 계약 분기(전략 분기 아님). ✅
- **III. Single Source of Truth**: 게이트 상수는 `search/config.py` 에만(env 오버라이드 관례
  유지). 관계명은 `rel_types.HAS_COLUMN` 사용(리터럴 금지). 프로파일 필드 목록은 mcp_app
  변환기 한 곳. ✅
- **IV. No Silent Failure**: SearchUnavailable 표면화 불변. 변환기는 필드 부재 시 빈 값
  명시(누락 은폐 금지). 컬럼 쿼리 실패는 로그+오류(조용한 [] 위장 금지 — 단 "컬럼 미수집
  테이블"은 정상 빈 배열로 구분). ✅
- **V. KV-Cache Prompts / VI. Schema Restraint / VII. Submit-Tool**: 프롬프트·LLM 출력 스키마
  무변경 — 해당 없음. ✅
- **VIII. Cohesion**: 신규 합본 없음. ✅
- **Cross-Service Contracts**: MCP 도구 출력이 계약 — 소비자는 robo-architect
  `skill_runner`(LEGACYREF 마커)와 `legacy_provenance._compact_nodes`. 문자열 반환 후에도
  tool_result 텍스트 = compact JSON 이므로 기존 `json.loads` 소비 그대로 동작. evidence 노드의
  `rules` 는 `[]` 로 유지(형태 안정 — rulesCount=0). **아키텍트 코드 변경 0** 을 계약 검증
  태스크로 확인한다. ✅ (하류 영향 플래그: robo-architect — 무변경 소비 확인 필수)

## Project Structure

### Documentation (this feature)

```text
specs/057-cluster-result-refinement/
├── spec.md
├── checklists/requirements.md
├── plan.md              # 이 문서
├── research.md          # Phase 0 — 게이트 방식·직렬화 우회 결정
├── data-model.md        # Phase 1 — 계층화 페이로드 형태
├── contracts/
│   └── mcp-cluster-retrieve.md   # MCP 도구 응답 계약(소유: analyzer, 소비: architect)
├── quickstart.md        # 검증 러너(단위→A/B→실API→e2e)
└── tasks.md             # /speckit-tasks 산출(후속)
```

### Source Code (repository root)

```text
search/
├── config.py            # [수정] 게이트 상수 추가(anchor_gap, support root 정책)
├── retriever.py         # [수정] _retrieve_global_anchors 앵커 게이트·root 자격
├── test_global_anchor.py# [수정] 게이트 단위 케이스 추가
└── eval/
    └── anchor_ab_eval.py# [재사용] 골드 전수 A/B (스윕 러너에서 호출)

api/
└── mcp_app.py           # [수정] 계층화 변환기 + 컬럼 부착 + 문자열 직렬화 반환
                         #        (_slim_clusters 는 summary 옵션으로 존치)

shared/neo4j/rel_types.py# [재사용] HAS_COLUMN
```

**Structure Decision**: 선정은 코어(retriever), 내용·직렬화는 MCP 표면(mcp_app) — REST
라우터·프론트·아키텍트는 무변경 소비자. 각 책임이 자기 파일 한 곳에서 끝난다.

## Complexity Tracking

위반 없음 — 기존 구조 안에서의 국소 변경.
