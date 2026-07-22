# 078 Traceability — spec ↔ plan ↔ tasks ↔ contracts 교차 점검

작성일: 2026-07-22. Analyze 단계 산출물. 구현 착수 게이트.

## 1. 요구 → 설계 → 작업 → 게이트 대응

| Spec 요구 | Plan owner | Tasks | Acceptance gate |
|---|---|---|---|
| §3.1 EXTERNAL_CHUNK 단일 모델 | graph_nodes/node_ids/external.py | T301,T303 | G2 fidelity 100% |
| §4 형식별 의미 단위 + 불변식 | step3 parsers/verifier | T201,T202,T204,T302 | G1 boundary 100%, G2 |
| §5 REFER_TO 규칙(유일성·1단계 상수) | step5 linker/key_index/const_propagation | T305,T309 | G3 FP 0, G4 |
| §5-6 미연결=정상 (unresolved 폐기) | graph_nodes/step3/step5 | T301,T302,T305 | G6 grep 0 |
| §6 context 통째-or-skip·outgoing만 | step6 external_evidence | T306 | G5 |
| §6 prompt byte-identical·user suffix | step7·_common.yaml | T401-T403 | G5 |
| §7 Agent verifier/held-out/negative | step4·agents/external | T307 | G10 |
| §2 제외: derived 재분류 | document_adapters | T302 | G2(derived 제외 명시) |
| 옛 관계·노드 제거 | schema/rel_types/constraints/writers/stream/state/step8 | T301,T303,T304,T308,T310 | G6 |
| AST 비접촉 | (변경 없음) | T310 | G7 10/10 |
| Catalog/Fabric 회귀 | (코드 무변경) | T602,T603 | G8 |
| Frontend 표시·숨김 | graphVisibility+카드 4파일+contract.mjs | T501-T504 | G9 |
| E2E skill DB 격리 | robo.cmd+graph owner | T701-T707 | G1-G5 실물 재검 |
| 보고·커밋 | docs/작업보고내용 | T801-T805 | — |

## 2. 소비자 전수 확인 (2026-07-22 grep 실측)

- 구 심볼 소비자 26파일 = 전부 `graph/`·`pipeline/`·`tests/` — plan §2 표와 일치.
  `api/`·`search/`·`llm/`·`shared/`·`agents/`(step4 제외)에는 소비자 0 실측.
- Frontend 소비자 7파일: analyzer.ts, NonCodeCard.vue, SqlStatementCard.vue,
  templateRouter.ts, graphVisibility.ts, nodeRelationships.ts,
  external-graph-contract.mjs — **전부 WIP 6건 목록 밖** (비접촉 충돌 없음).
- Catalog: `REFER_TO`는 TABLE-target 쿼리만 — 계약 분리 유지(graph-contract 명시).
- Fabric: 소비자 0 (S6에서 재확인).

## 3. 발견된 불일치와 해소

1. **TOML**: research.md는 "Phase 1 gold에서 결정", spec §2 지원 목록에는 부재.
   → **해소**: 실물 코퍼스 0건 + 위치 API 부재이므로 078에서는 `.toml`을
   `unanalyzed`(no span adapter) 정직 분류로 확정한다. spec §2 제외 목록 해석에 포함.
   향후 수요 발생 시 tomlkit/자체 스캐너 gold로 재평가.
2. **chunking.py 파일명**: plan은 `chunk_verifier.py` 개명 검토 — 개명은 소비자
   grep과 함께 S3에서 결정(강제 아님). tasks T302에 귀속.
3. **step8 임베딩**: 외부 청크 임베딩의 검색 소비자는 S3 착수 시 embed_step 정독으로
   확정(T308). 문서 후보(CandidateLink) 제거는 확정.
4. 그 외 spec↔plan↔tasks 간 계약 충돌 없음 확인.

## 4. 착수 판정

- research(실측 기반) ✅ / spec ✅ / plan ✅ / tasks ✅ / contracts 4종 ✅ /
  소비자 전수 ✅ / 불일치 해소 ✅ → **S1(gold corpus)부터 구현 착수 가능.**
