# Plan 078 — 구현 설계와 슬라이스 순서

근거: `spec.md`(계약), `research.md`(기술 선정). 디렉토리 구조는 현행 유지 —
owner 파일 내부 교체 + 소수 파일 추가/삭제만 한다.

## 1. 현재 흐름 → 목표 흐름

```text
현재: inventory → parse(xml_universal/config_parser/markdown_doc/document_adapters)
      → make_external_chunks(1200 split, anchor+parts) → upsert(구 라벨 7종)
      → scan_external(Agent regex, 합의 승인) → link_external(관계 7종+unresolved)
      → step6(양방향, 절단 허용) → step7 prompt → step8 document candidates

목표: inventory(유지) → 형식별 span 어댑터(순수 Python, in-process)
      → SemanticChunk(char span) → 결정론 verifier(slice/hash/overlap)
      → upsert(PROJECT_FILE + EXTERNAL_CHUNK, HAS_CHUNK)
      → scan_external(Agent span 제안 + verifier/held-out 승인)
      → link_external(REFER_TO만: exact+유일성+1단계 상수)
      → step6(outgoing만, 통째-or-skip) → step7 prompt(user suffix)
      → step8 문서 후보 임베딩 제거
```

병렬 구조(step2 ∥ step3)와 step 순서는 불변.

## 2. Owner별 변경 지도

### Analyzer — 변경

| owner | 변경 |
|---|---|
| `graph/schema/graph_nodes.py` | `ExternalChunk` 재정의(§spec 3.1: keys/semantic_kind/char span/provenance/source_hash, 1200 제한·anchor/part 제거). `DocumentNode`·`UnresolvedRef`·`CandidateLink` dataclass 삭제. Literal 타입 정리 |
| `graph/schema/graph_edges.py` | `ReferTo`에 `matched_key`·`via_symbol` 추가. `CallsSql`/`UsesConfig`/`Mentions`/`HasPart`/`HasSection` 삭제. `ExternalEdge` = ReferTo\|HasChunk |
| `graph/schema/rel_types.py` | `CALLS_SQL`/`USES_CONFIG`/`MENTIONS`/`HAS_PART`/`HAS_SECTION` 상수 삭제 |
| `graph/schema/constraints.py` | 구 라벨 제약 → `EXTERNAL_CHUNK` 제약으로 교체 |
| `graph/schema/node_ids.py` | `external_chunk_id`를 span 기반으로 변경, `unresolved_ref_id` 삭제 |
| `graph/nodes/external.py` | 라벨 집합 = {PROJECT_FILE, EXTERNAL_CHUNK}. 고정/동적 타입 정리 |
| `graph/edges/external_links.py` | `_EDGE_SPECS` = ReferTo/HasChunk만 |
| `step3/parsers/xml_universal.py` | expat 기반 span 어댑터로 교체(최외곽 key 요소, defusedxml 사전검사 유지) |
| `step3/parsers/config_parser.py` | PyYAML mark/json-source-map/논리 레코드 스캐너 기반 span 어댑터로 교체 |
| `step3/parsers/markdown_doc.py` | markdown-it-py 기반 heading 분할 어댑터로 교체(DOCUMENT/DOC_SECTION 노드 폐기) |
| `step3/parsers/document_adapters.py` | 변환 로직 유지, 산출 세그먼트를 `derived_document` 청크로 반환 |
| `step3/chunking.py` | 1200 split·anchor/parts 삭제 → `verifier.py` 책임(slice/hash/overlap 검증)으로 교체. 파일명은 `chunk_verifier.py`로 개명 검토(소비자 함께) |
| `step3/load_external_step.py` | 새 어댑터 라우팅·verifier 게이트·UNRESOLved 노드 생성 제거(ProjectFile.status로 일원화) |
| `step4/scan_external_step.py`·`external_skills.py`·`agents/external.py` | skill 산출을 span으로, verifier(slice-exact/no-overlap/held-out/negative) 승인 게이트. 합의는 보조 신호로 강등 |
| `step5/link_external/linker.py` | REFER_TO 전용: exact+유일성+1단계 상수. FQN 역링크·SQL table 파생·unresolved 생성 삭제 |
| `step5/link_external/document_mentions.py`·`sql_tables.py` | 파일 삭제 (소비자 0 확인 후) |
| `step5/link_external/key_index.py`·`const_propagation.py` | 유지(어휘 경계 스캐너 재사용). 다중 키/유일성 판단은 linker에서 |
| `step6/external_evidence.py` | outgoing REFER_TO만, 통째-or-skip(절단 삭제), matched_key/provenance 포함 |
| `step7` prompt 값 빌더·`_common.yaml` | evidence 스키마 갱신(구 kind 서술 제거). system prefix는 병렬 간 byte-identical 유지 |
| `step8/embed_step.py` | `_build_document_candidates`·CandidateLink 생산 삭제. EXTERNAL_CHUNK 임베딩 여부는 기존 검색 계약 확인 후 최소 유지 |
| `pipeline/execution/workflow_state.py`·`graph_delta_stream.py` | 구 노드/엣지 타입 스트림 제거, 새 타입 반영 |
| `tests/**` | step3/4/5/6/8·graph·execution 외부 관련 테스트 전면 갱신 + gold/adversarial 신설 |
| `README`·`.specify/feature.json` | 078로 전환(058 기록 보존) |

### Frontend — 최소 변경 (WIP 6건 비접촉)

| owner | 변경 |
|---|---|
| `src/shared/utils/graphVisibility.ts` | 구 라벨/관계 → `EXTERNAL_CHUNK`·`REFER_TO`(외부 endpoint만) 기본 숨김 갱신 |
| `NonCodeCard.vue` 등 카드 | `text`·provenance(`file_path`·라인·`keys`) 표시. `SqlStatementCard`의 SQL_STATEMENT 라우팅은 라벨 폐기에 맞춰 정리 |
| `tests/external-graph-contract.mjs` | 새 계약으로 갱신 |

### Catalog/Fabric — 코드 무변경 목표

- Catalog `schema_queries.py`의 `REFER_TO→TABLE`은 target 라벨이 TABLE이라 무충돌 —
  전체 테스트로 회귀만 증명.
- Fabric: 활성 소비자 없음 재확인(grep) + 전체 테스트.

### 비접촉 (사용자 WIP·계약)

ANTLR 저장소 전체, Architect 저장소 전체, Frontend WIP 6건, `run-output/`,
AST(step2)·link_calls·link_tables, DDL/TABLE 파이프라인.

## 3. 슬라이스 순서 (각 슬라이스 green 종료 필수)

| # | 슬라이스 | 완료 증거 |
|---|---|---|
| S1 | gold/adversarial fixture + 채점 하네스 (tests 전용, prod 무접촉) | 신규 gold 테스트가 현재 결함을 red로 증명(기대 실패 기록) |
| S2 | span 어댑터·verifier 신규 모듈 + 단위 테스트 (기존 경로 무접촉 추가) | 어댑터 gold 100%, 불변식 테스트 green, 기존 suite green 유지 |
| S3 | **계약 컷오버** — schema/writers/stream/state/step3/step4/step5/step6/step8 + 전 테스트 동시 전환, 옛 심볼 제거 | focused+전체 suite green, 옛 심볼 grep 0, AST 10/10 |
| S4 | prompt 계약(_common.yaml·값 빌더) + prompt record 테스트 | prompt 26/26 계열 green + system prefix byte 검증 |
| S5 | Frontend 갱신 | build+contract mjs+1920×1080 실화면·콘솔·네트워크 |
| S6 | 전수 회귀 | Analyzer 전체 147+ 기준, Catalog/Fabric 전체, grep 게이트 재확인 |
| S7 | 실기동 E2E(skill DB 격리 owner, loan-ejb-app) + held-out(ukesa/ofbiz) | source→chunk 전수 대조, REFER_TO gold 대조, prompt record, UI 캡처 |
| S8 | 작업보고서 + scoped commit/push | `docs/작업보고내용/2026-07-22-*.html`, 저장소별 커밋 |

S3가 가장 크지만 하나의 계약이므로 쪼개면 이중 경로가 생긴다(원칙 위반). S3 내부는
schema→step3→step5→step6→step4→step8→stream 순으로 작업하되 슬라이스 종료 게이트는
전체 suite green + grep 0으로 한 번에 판정한다.

## 4. 마이그레이션·롤백

- 이중 경로·feature flag 없음. 롤백 = git revert (슬라이스별 커밋).
- Neo4j 상수/제약: 구 라벨 제약 제거는 constraints owner의 기존 lifecycle로만.
  기존 DB 데이터 삭제는 하지 않는다(새 분석 실행이 새 그래프를 쓰고, 검증은 skill DB
  격리 owner에서 수행).

## 5. 위험과 대응

| 위험 | 대응 |
|---|---|
| S3 규모 — 중간 상태 장기화 | 착수 전 S1/S2로 신규 코어를 green 확보, S3는 소비자 목록(§2) 순서로 기계적 전환 |
| YAML anchor/alias·multi-doc span 미검증 | S1 gold에 포함, PyYAML mark 실측으로 판정. 실패 시 해당 케이스 fail-closed |
| json-source-map duplicate key 한계 | duplicate 감지 시 파일 fail-closed + gold 케이스 |
| expat 인코딩(비UTF8) offset | 어댑터가 디코딩 텍스트를 UTF-8로 재인코딩해 파싱 후 byte→char 변환, CP949 gold 케이스로 검증 |
| step8 임베딩/검색의 구 라벨 의존 | S3에서 소비자 grep으로 전수 확인 후 동시 정리 |
| Frontend 카드 라우팅이 구 라벨 의존 | S5에서 label 스위치 전수 검색 후 최소 수정 |
