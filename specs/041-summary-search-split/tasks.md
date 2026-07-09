# Tasks: 요약·검색 서술 분리 (041-summary-search-split)

**Input**: plan.md · research.md · data-model.md · quickstart.md
**순서 강제**: Phase A(더하기) → B(게이트) → C(빼기). B 게이트 통과 전 C 착수 금지(FR-005).

## Phase A — search_text 필드 신설 (US1)

- [x] T001 스키마: `data_models/code_analysis_output.py` — SummaryOutput·BatchSummaryItem에 `search_text: str = ""` 추가(Field description 포함, 검증은 Phase C까지 없음)
- [x] T002 dataclass: `data_models/graph_nodes.py` — `CodeNode.search_text: Optional[str] = None`(분석 결과 구역)
- [x] T003 fan-in(framework): `analysis_flow/step6_analyze_code/three_split_llm_fanout.py` — `fn.search_text = summary_out.search_text or None`
- [x] T004 fan-in(dbms): `analysis_flow/step6_analyze_code/batch_analyze.py` `_apply_node_analysis` — 동일 적용
- [x] T005 저장: `shared/neo4j/nodes/code_node.py` `to_summary_item` — `"search_text": fn.search_text` 키 추가
- [x] T006 프롬프트(framework): `prompts/framework/code_summary.yaml` — 분할 책임 카드 4필드로 갱신 + `[4] search_text` 지침 신설(검색 관점·분기/상태전이/코드값 누락0·코드 어휘 병기·복붙 금지·단순노드 빈값 OK·좋은/나쁜 예)
- [x] T007 프롬프트(dbms): `prompts/dbms/code_summary_batch.yaml` — 동일 `[4]` 신설(배치 노드별)
- [x] T008 해시 장부: `python cli_scripts/check_byte_identical.py --update` 후 재검증 exit 0
- [x] T009 프론트 숨김: `robo-data-frontend .../node-detail/FallbackCard.vue` — HIDDEN_KEYS에 `'search_text'`
- [x] T010 놀이터 모드: `module_test/임베딩서칭/engine/_common.py` — fetch_corpus Cypher에 `coalesce(n.search_text,'') AS search_text` + `_compose_embed_text` searchfield 모드(코드 제외, `검색:` 파트)
- [x] T011 심볼 전수 grep: `search_text`·`SummaryOutput(`·`BatchSummaryItem(`·`to_summary_item` — 참조 일관·잔재 0 확인

## Phase B — 파일럿 + 위험질의 게이트 (US2)

- [x] T012 실행 전제 정독: 메모리 manual.md·shopmall-demo-testdata.md → 두 데이터셋 재분석 레시피 확정
- [x] T013 위험질의 세트 확정: cluster_gold.yaml·rewrite_gold.yaml·measure_log.md 대조 → id 목록(5~8개, 선정 근거 기록)
- [x] T014 파일럿: SKT(test DB) 재분석 → search_text 산출 원문 대조(표본 정독: 분기·코드값·테이블 누락0, 복붙0)
- [x] T015 SKT ablation: template vs searchfield 위험질의 측정(`cluster_eval.py --ids`) — 유실 0 판정. 미달 시 프롬프트 개선 반복(T006-7 재작업)
- [x] T016 쇼핑몰(neo4j DB) 재분석 + 동일 ablation — 유실 0
- [x] T017 측정 기록: `eval/measure_log.md` Session 신규(패배 포함) + `ROBO_NEO4J_DATABASE=neo4j` 원복

## Phase C — summary 다이어트 + 마커 제거 (US3, 게이트 통과 후에만)

- [x] T018 validator 교체: `code_analysis_output.py` — `[핵심]` 강제 → 새 검증(`[핵심]` 등장 거부 + 비어있지 않은 줄 ≤3), BatchSummaryItem에도 동일 신설. docstring 갱신
- [x] T019 프롬프트 다이어트(framework): code_summary.yaml `[2]` 전면 개정 — 마커 폐지·1~3줄·세부는 search_text 이관·중복 금지·좋은 예 교체
- [x] T020 프롬프트 다이어트(dbms): code_summary_batch.yaml 동일 개정
- [x] T021 해시 장부 재갱신 + `[핵심]` 전수 grep(프롬프트·코드·주석 잔재 정리 — module_summary 등 타 소비처 영향 확인)
- [x] T022 재분석: SKT → 쇼핑몰
- [x] T023 길이·품질 검증: Cypher 전수(줄≤3·마커0) + 표본 원문 대조(파악성·한줄달랑0) + 상향 분석 표본 대조(FR-010)
- [x] T024 위험질의 재게이트(searchfield) — 유실 0 유지 + measure_log 기록
- [ ] T025 프론트 확인: 노드 상세(코드·구문·Fallback) search_text 비노출 + 요약 표시 정상

## 마무리

- [ ] T026 스펙 정합: spec.md Assumptions([핵심] 제거 확정 반영)·checklist 갱신 + speckit-analyze 교차검증
- [ ] T027 메모리 갱신: req-cluster-retrieval.md 진행 상태 + todo.md


