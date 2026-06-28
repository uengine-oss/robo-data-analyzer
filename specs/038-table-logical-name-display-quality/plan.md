# Implementation Plan: 테이블 논리명 품질 향상 + 스키마 화면 표시 배선

**Branch**: `038-table-logical-name-display-quality` | **Date**: 2026-06-29 | **Spec**: [spec.md](./spec.md)

## Summary

테이블 `logical_name` 은 이미 LLM 으로 생성·Neo4j 저장된다(plumbing 정상). 두 갈래로 마감한다: (1) **품질** — 논리명이 빈약(4~6자)하지 않게 생성 지침·예시를 재설정(analyzer 프롬프트, 전략무관), (2) **표시 배선** — 저장된 논리명이 화면까지 흐르도록 catalog 응답에 `logical_name` 을 싣고 frontend 가 테이블 제목을 논리명(물리명 병기)으로 표시. 공유 계약 = 테이블 조회 응답의 `logical_name`(옵셔널, 미수신 시 물리명 폴백).

## Technical Context

**Language/Version**: Python 3.11(analyzer·catalog) / Vue3+TS(frontend)
**Primary Dependencies**: analyzer=LangGraph·LLM yaml runner / catalog=FastAPI+Neo4j / frontend=Vue+VueFlow
**Storage**: Neo4j(`TABLE.logical_name` 기보유) — 신규 저장 스키마 없음
**Testing**: 데이터검증=재분석 후 논리명 샘플 대조([[principles]]§6) / 표시=Playwright 스샷 + catalog 응답 확인
**Project Type**: 멀티서비스(analyzer→catalog→frontend), 단일 capability
**Constraints**: 전략무관(DBMS·framework) / 날조금지·물리명금지·빈값유지 / catalog 옵셔널 필드(구버전 응답 호환)

## Constitution Check

- **SDD/회귀금지**: 계약(`logical_name`) 양쪽 동시 정합 — catalog producer + frontend consumer. ✅
- **근본해결(로직 추가 아닌 정리)**: 표시 문제는 분기 추가가 아니라 **버려지던 필드를 잇는 것**(3곳 누락 연결) + 프롬프트는 "짧은" 프레이밍 **교체**(길이 규칙 덧대기 아님). ✅
- **빈값미저장/날조금지**: spec 025 원칙 계승, 폴백 물리명. ✅
- **KV캐시**: table_description 은 3분할 fanout 밖 독립 rule — 공통 system 불변(KV 영향 없음). ✅

## Project Structure

### Documentation (this feature)

```text
specs/038-table-logical-name-display-quality/
├── spec.md
├── plan.md              # 본 파일
├── tasks.md             # /speckit-tasks 산출
└── checklists/requirements.md   # 품질 검증(통과)
```

### Source Code (3 서비스 — 역할분담)

```text
analyzer (robo-data-analyzer)            ← 이 세션(백엔드)
└── prompts/framework/table_description.yaml   # logical_name 지침·예시 재설정
└── prompts/dbms/table_description.yaml         # 동일(전략무관)

catalog (robo-architect/robo-analyzer/robo-data-catalog)   ← 이 세션(백엔드)
├── service/schema_query_service.py     # getTables RETURN + t.logical_name
├── api/request_models.py               # SchemaTableInfo +logical_name (옵셔널)
└── api/catalog_router.py               # list_schema_tables 응답 구성 +logical_name

frontend (robo-data-frontend)            ← 협업 세션(프론트)
├── features/schema/api.ts              # RoboSchemaTableInfo +logical_name
├── features/schema/schemaCanvas.ts     # 노드 data 에 logical_name 전달
└── features/schema/components/nodes/TableNode.vue  # 제목 논리명+물리명, stale 주석 제거
```

**Structure Decision**: capability 단일이나 구현 3서비스. 계약(`logical_name` 응답필드)만 공유, 표시 정책은 frontend 소비측 결정(폴백 물리명). analyzer↔catalog 는 같은 Neo4j 속성(`TABLE.logical_name`)을 producer/reader 로 공유 — 키 불변이라 추가 정합 불필요.

## Complexity Tracking

위반 없음 — 분기·플래그 추가 0, 누락 연결 + 프롬프트 프레이밍 교체.
