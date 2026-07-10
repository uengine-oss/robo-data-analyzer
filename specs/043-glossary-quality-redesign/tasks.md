# Tasks: 용어사전 품질 근본재설계

**Input**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md)

## Phase 1 — 추출 규칙·저장 (US1·US3 기반)

- [x] T001 `prompts/framework/code_summary.yaml` [5] domain_terms 규칙 개정 — 표제어 자격
      "함수 텍스트(코드+주석) 실재 토큰"·뜻 근거주의·정체 창작 금지·§0.2 동작 한정 정밀화
      (폭주 방어 문구 유지, 무관 도메인 예시만) (FR-001·002·008)
- [x] T002 `prompts/dbms/code_summary_batch.yaml` 동일 개정 (양 전략 동형)
- [x] T003 `shared/neo4j/nodes/code_node.py` to_summary_item 에 domain_terms 추가 +
      SET_SUMMARY_QUERY 경로 확인 (FR-003) / `data_models/graph_nodes.py` 주석 갱신
- [x] T004 `cli_scripts/prompt_hashes.json` 재고정 + byte-identical 검증 통과 (헌법 V)

## Phase 2 — AI 종합 (US1 핵심)

- [x] T005 `data_models/code_analysis_output.py` GlossarySynthesisOutput 신설
      (meaning/aliases/evidence — 평탄 str 3필드, 헌법 VI)
- [x] T006 `prompts/framework/glossary_synthesis.yaml` + `prompts/dbms/glossary_synthesis.yaml`
      신설 — system=종합 규칙(근거주의·변종 지목·정체 창작 금지·무관 도메인 예시),
      user=전체 표제어 목록(공유 접두)+용어별 문맥 (FR-004·007·008)
- [x] T007 `analysis_flow/step7_post_processing/build_glossary_step.py` 재설계 —
      수집(+src_node_ids)→문맥 조립(상한 6·컷 로그·모듈요약 매칭 재사용)→AI 병렬
      (gather_with_warmup·fail=원본 유지)→수용 규칙(evidence 부분열 대조·aliases 목록 대조)
      →union-find 그룹→기존 스키마 저장 (FR-004·005, 헌법 IV·VIII)
- [x] T008 `analysis_pipelines.py` — framework 병렬그룹에서 build_glossary 분리해
      summarize_module 뒤 단독 배치·dbms 위치 확인 (FR-006, 헌법 II)

## Phase 3 — 실측 판정 (SC 전부)

- [x] T009 SKT 전량 재분석(test DB) → 판정: 폭주 0(SC-002)·변종 통합·뜻 전수 대조(SC-001)·
      소음 무증가(SC-004)
- [x] T010 쇼핑몰 전량 재분석(neo4j DB) → 동일 판정
- [x] T011 놀이터 게이트(검색기 수정 0·해피패스 0): SKT ≥6/7·쇼핑몰 ≥9/11 (SC-003)
- [x] T012 SC-005 스모크: 저장 조각 조회로 종합 입력 재구성 가능 확인
- [x] T013 measure_log Session 19 기록(패배 포함)·.env.local DB=neo4j 원복
