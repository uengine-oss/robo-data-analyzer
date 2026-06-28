# Tasks: 테이블 논리명 품질 향상 + 스키마 화면 표시 배선

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

진행표기: `[x]` 완료 · `[ ]` 미완 · `[>]` 타 세션(프론트) 위임. 검증=[[principles]]§6(재분석·실데이터 대조).

## Phase A — 표시 배선 (US1, 계약: 응답 `logical_name`)

### A1. catalog producer (이 세션 — 백엔드)
- [x] T001 `service/schema_query_service.py` `fetch_schema_tables` RETURN 에 `t.logical_name AS logical_name` 추가
- [x] T002 `api/request_models.py` `SchemaTableInfo` 에 `logical_name: Optional[str] = ""` 추가
- [x] T003 `api/catalog_router.py` `list_schema_tables` 응답 구성에 `logical_name=r.get("logical_name") or ""` 추가
- [x] T004 [검증] `SchemaTableInfo`/`fetch_schema_tables` 소비처 전수 grep — 라우터 1곳·옵셔널 필드라 회귀 0 확인
- [x] T005 [검증] 수정본 checkout 을 5599 에 띄워 `GET /robo/schema/tables` 응답에 `logical_name` 실값 확인 ✅ (zngm="결제 인증 공통 코드 기준", zord_acnt="계정 정보"). 전 구간(Neo4j→쿼리→모델→JSON) 흐름 확인, 5599 정리완료. ★주의: 기존 5503 프로세스는 수정 전 코드라 logical_name 미포함 — 프론트 검증 전 catalog **재기동 필요**.

### A2. frontend consumer (협업 세션 — 프론트)
- [>] T006 `features/schema/api.ts` `RoboSchemaTableInfo` 에 `logical_name?: string` 추가
- [>] T007 `features/schema/schemaCanvas.ts` 노드 data 에 `logical_name` 전달
- [>] T008 `features/schema/components/nodes/TableNode.vue` `cardTitle` = 논리명 우선+물리명 병기, 논리명 없으면 물리명 폴백, stale 주석 제거, 논리/물리 토글 일관
- [>] T009 [검증] Playwright 스샷 — 논리명 보유 테이블이 논리명 제목+물리명 병기, 없는 테이블 물리명 폴백, 회귀 pageerror 0

## Phase B — 논리명 품질 (US2, analyzer 프롬프트, 전략무관)

- [x] T010 `prompts/framework/table_description.yaml` logical_name 섹션 재설정 — "짧은" 프레이밍 제거, 일반어단독 금지 + 끝맺음 다양 예시. (1차 후 "매핑" 편향 발견 → 디바이어스 재설정: 같은 꼬리말 기계반복 금지·매핑 남발 금지)
- [x] T011 `prompts/dbms/table_description.yaml` 동일 재설정(전략무관 일관) — KV: table_description 은 3분할 밖 독립 rule, 공통 system 불변
- [x] T012 [검증] framework 재분석 2회(1차→디바이어스→2차) 후 논리명을 `description`·원문 대조 완료. **결과: "매핑" 3/7→0/7, 6/7 설명적·정확·끝맺음 다양, 물리명/코드토큰 0건(SC-002✅).** 1건(zord_acnt "계정 정보" 5자)은 candidates 빈약(함수1·컬럼2)이라 짧음 = 날조금지 준수한 정직한 결과(SC-001 합격, 억지 연장 안 함). US2 완료.

## Phase C — 통합 검증 & 마감

- [ ] T013 US1 라이브 통과(현재 DB) + US2 재분석 통과 동시 확인
- [ ] T014 커밋(사용자 확인 후): analyzer(프롬프트 2 + specs/038) refactor / catalog(3파일) 서브모듈 / frontend 는 협업 세션
- [ ] T015 [[todo]] 갱신 — 038 상태 기록, 표시배선 정리대상 줄 제거

## 의존
- Phase A1(완료) → A2(프론트) 가 계약 소비. A1 없이도 A2 는 graceful 폴백으로 깨지지 않음(독립 가능).
- Phase B 는 A 와 독립(재분석 후 반영). US1·US2 병행 가능.
