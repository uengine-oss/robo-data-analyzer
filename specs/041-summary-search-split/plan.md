# Implementation Plan: 요약·검색 서술 분리 (summary 다이어트 + 검색 전용 서술 신설)

**Branch**: `041-summary-search-split` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/041-summary-search-split/spec.md`

## Summary

코드노드 1분할(요약) LLM 호출에 **검색 전용 서술 필드 `search_text`** 를 추가 생성한다(호출 횟수 +0). 조건분기·상태전이·상태코드·테이블 상호작용을 "의미 검색에 걸리는" 자연어로, 코드 어휘(테이블명·코드 리터럴)는 병기 보존. 프론트 비노출(FallbackCard 숨김 1줄). 놀이터 위험질의 ablation으로 [코드180자 목발 vs 요약+search_text]를 수치 검증하고, 통과 시 summary를 1~3줄로 다이어트하며 `[핵심]` 마커를 제거한다.

## Technical Context

**Language/Version**: Python 3.11+ (analyzer) · Vue 3/TS (frontend 1줄) · 놀이터 Python(.venv-play)

**Primary Dependencies**: LangChain `with_structured_output`(Pydantic 강제) · Neo4j bolt · 놀이터 BGE-M3/CE/BM25 스택(기존)

**Storage**: Neo4j 코드그래프 — 코드노드에 `search_text` 속성 1개 추가(additive). DB: `neo4j`(쇼핑몰 124노드)·`test`(SKT 38노드)

**Testing**: ① 파일럿 — 소수 대표노드 재분석 후 산출 원문 대조 ② 놀이터 위험질의 재검색(`eval/cluster_eval.py --ids`) ③ 전량 재분석 후 위험질의 게이트 ④ 다이어트 후 재게이트 + 길이 검사

**Target Platform**: 로컬 개발 PC (Windows) — analyzer 실행 + Neo4j 로컬 + 놀이터 측정

**Project Type**: 파이프라인 서비스(analyzer) + 독립 측정 놀이터(module_test/임베딩서칭) + 프론트 1줄

**Performance Goals**: LLM 호출 횟수 증가 0 / 노드당 출력 토큰 증가 수십 수준 / 측정 1회 수초(놀이터 헌법 VII)

**Constraints**: KV캐시 byte-identical(analyzer 헌법 V — `user_template_specific`만 수정, `_common.yaml` system 불변) / Neo4j 놀이터 읽기전용(쓰기는 analyzer run) / 도메인 하드코딩 0 / 편애0(새 임베딩 모드 기본 off, ablation 승자 채택)

**Scale/Scope**: 재분석 대상 = 두 데모 그래프(124+38노드). 프롬프트 2 + 스키마 2 + dataclass 1 + 저장 1 + 프론트 1줄 + 놀이터 모드 1

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ | LLM 추가 호출 없음 — 기존 의미 추출 호출에 출력 필드만 추가. 저장·숨김·측정은 결정론 |
| II. Zero-Branch Strategy | ✅ | 전략 분기 코드 추가 0 — framework/dbms 각자의 분할1 yaml·스키마에 같은 필드. 파이프라인 조립 무변경 |
| III. Single Source of Truth | ✅ | 새 속성 이름은 dataclass·Pydantic·Cypher item 키로만 흐름(문자열 리터럴 Cypher 없음 — `SET node += item`) |
| IV. No Silent Failure | ✅ | search_text 빈 값 = 명시적 허용 정책(단순 노드) — validator가 다이어트 형식은 강제(재시도 유도), 조용한 빈 결과 신설 없음 |
| V. KV-Cache-Friendly | ✅ | 수정은 분할1의 `user_template_specific`(분할 고유 suffix)만 — 공통 system·user_template_inputs 바이트 불변. 해시 장부 `--update` + CI 가드 유지 |
| VI. Schema Restraint | ✅ | `search_text: str` 단일 자유 문자열 — 중첩/반복 구조 아님(폭주 면적 최소) |
| VII. Submit-Tool Wrap | N/A | Agent 경로 아님(단순 `response_format` 호출 — 기존 방식 그대로) |
| VIII. Cohesion | ✅ | 저장은 기존 `to_summary_item` 한 곳에 키 추가 |

**Cross-Service Contracts**: Neo4j 출력 스키마에 속성 1개 **additive** — robo-architect·frontend 계약 변경 없음. 단 frontend `FallbackCard.vue`가 속성을 일반 나열하므로 **비노출 요구(FR-003) 충족을 위해 HIDDEN_KEYS 1줄 추가 필수**(계약 변경이 아니라 표시 정책). Phase C의 `[핵심]` 마커 제거는 frontend가 마커를 파싱하지 않으므로(첫 문단 분리) 계약 무영향 — 표시 내용만 짧아짐.

**놀이터 헌법(8원칙)**: I 객관측정(위험질의 node-id 회수) · II 편애0(searchfield 모드 기본 off, ablation 승자) · VII 빠른측정(기존 캐시 인프라) · VIII 전략무관(도메인 하드코딩 0) 준수.

## Project Structure

### Documentation (this feature)

```text
specs/041-summary-search-split/
├── plan.md              # 이 파일
├── research.md          # Phase 0 — 전수조사 확정 사실 + 결정
├── data-model.md        # Phase 1 — search_text 필드 정의·흐름
├── quickstart.md        # Phase 1 — 파일럿·게이트·전량 검증 절차
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
project/robo-data-analyzer/
├── data_models/
│   ├── code_analysis_output.py   # SummaryOutput·BatchSummaryItem에 search_text 추가 (+Phase C: validator 교체)
│   └── graph_nodes.py            # CodeNode.search_text 필드
├── analysis_flow/step6_analyze_code/
│   ├── three_split_llm_fanout.py # fan-in: fn.search_text = summary_out.search_text (framework)
│   └── batch_analyze.py          # _apply_node_analysis 동일 (dbms)
├── shared/neo4j/nodes/
│   └── code_node.py              # to_summary_item에 search_text 키
├── prompts/
│   ├── framework/code_summary.yaml   # [4] search_text 지침 추가 (+Phase C: [2] summary 다이어트 개정)
│   └── dbms/code_summary_batch.yaml  # 동일
├── cli_scripts/prompt_hashes.json    # check_byte_identical.py --update
└── module_test/임베딩서칭/
    ├── engine/_common.py             # fetch_corpus Cypher + EMBED_TEXT_MODE="searchfield" 모드
    └── eval/cluster_eval.py          # 위험질의 --ids 측정 (기존 도구 재사용, 수정 없음 목표)

project/robo-data-frontend/
└── src/features/code-analysis/components/graph/node-detail/FallbackCard.vue  # HIDDEN_KEYS + 'search_text'
```

**Structure Decision**: 기존 파일에 녹이기(수정 사다리 1순위) — 새 파일 0. 전략 분기 0(각 전략의 분할1 yaml이 자기 지침 보유, 스키마·저장은 공용).

## Phase 순서 (스펙 FR-005 순서 강제의 구현 매핑)

- **Phase A (더하기)**: 스키마(SummaryOutput·BatchSummaryItem·CodeNode) + 프롬프트 2(yaml [4] 신설) + fan-in 2곳 + 저장 1곳 + 해시 장부 + FallbackCard 숨김 + 놀이터 searchfield 모드. summary 지침 불변.
- **Phase B (게이트)**: 파일럿(소수 대표노드 산출 검사) → 전량 재분석(쇼핑몰→SKT) → 위험질의 ablation [template vs searchfield] → 유실 0 판정. 미달 시 프롬프트 개선 반복(Phase C 착수 금지).
- **Phase C (빼기)**: summary 다이어트 프롬프트 개정([2] 전면) + `[핵심]` 마커 제거(validator 교체: 마커 강제 → 3줄 상한 검증) + 재분석 + 위험질의 재게이트 + 길이·품질 표본 검수 + 상향 분석 표본 대조.

## Complexity Tracking

> 위반 없음 — 표 생략.
