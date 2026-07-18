# Implementation Plan: MCP 레거시 참조 목록·상세 분리

## 현재 배선

`MCP cluster_retrieve → search.service → Retriever → Neo4j/corpus → api.mcp_app._tiered_clusters → compact JSON → Architect skill_runner → provenance/UI`

현재 `_tiered_clusters`가 검색 결과에 원문과 컬럼을 추가해 목록과 상세 책임을 동시에 가진다. Architect는 전체 응답을 한 마커로 저장해 실제로 상세 검토한 ID를 구분하지 못한다.

## 목표 배선

`cluster_retrieve → Retriever 결과 → list serializer → compact JSON`

`node_detail(id) → 요청 DB → 단일 노드/관계/규칙/컬럼 query → detail serializer → compact JSON`

두 도구는 연결 해석과 오류 직렬화를 공유하지만 검색과 상세 조회 로직은 분리한다. Cypher는 관계 상수를 사용하고, 공개/내부 필드 정책은 한 곳에서 정의한다.

## 변경 슬라이스

1. 계약 테스트로 목록 금지 필드, 상세 필수 필드, 오류, 자기 CALLS, 문자열 한 벌을 먼저 고정한다.
2. `api/mcp_app.py`의 목록 serializer를 단순화하고 `detail`·컬럼 배치·tiered 옛 분기를 제거한다.
3. 검색 owner 아래 상세 조회 모듈을 추가하고 MCP 도구에 연결한다.
4. summary 정책을 framework/dbms 모든 생산 프롬프트와 모델 설명에 단일 문구로 정합하고 hash를 갱신한다.
5. unit→MCP transport→Neo4j 원본 대조→골드→Analyzer UI 무회귀 순으로 검증한다.

## 영향 생산자·소비자

- 생산자: `api/mcp_app.py`, `search/ranking/retriever.py` 결과, Neo4j 코드/TABLE/RULE/EXAMPLE/COLUMN.
- 소비자: Architect `skill_runner.py`, `legacy_provenance.py`, proposal intent skill, PLAN/DDD runners, spec052 검증 러너.
- 관측/검증: `tests/api`, `tests/search`, `goal-verify/runners/graph`, `goal-verify/runners/provenance/spec052`.
- 문서/설정: README MCP 예시, Architect `.mcp.json`은 URL 불변이라 수정 불필요.

## Constitution Check

- Determinism: 상세 조립과 중복 검증은 Cypher/코드로 수행하고 LLM을 추가하지 않는다.
- SSOT: 공개 필드·오류 코드·관계명은 각각 단일 정의를 사용한다.
- No silent failure: 누락/중복/불완전 소스는 오류로 반환한다.
- Prompt cache: 공통 prefix는 직접 중복 수정하지 않고 현재 extends 구조를 보존하며 hash gate를 갱신한다.
- Ownership: API는 transport, 상세 그래프 조회는 search/graph 경계에 둔다.

## 롤백

커밋 단위로 되돌린다. 무기한 `detail=tiered` shim은 만들지 않는다. 전환 커밋은 Analyzer 생산자와 Architect 소비자를 함께 배포하고 두 도구 등록을 readiness gate로 확인한다.
