# Tasks: 스테레오타입 → 라벨 (Phase 1, additive)

**변경**: `shared/neo4j/nodes/stereotype_label.py`(신규), `code_node.py`, `module.py`

- [x] T001 `stereotype_label.py` 신규 — `apply_stereotype_labels(client, pairs)`: stereotype별 묶어 `SET node:`<값>``(검증풀 통제값+백틱). 빈값 skip.
- [x] T002 `code_node.py`: import + `update_summaries`에서 분석된 노드에 `apply_stereotype_labels((id,stereotype))` 호출(속성 SET 유지=무중단).
- [x] T003 `module.py`: import + `upsert_modules` 끝에 `apply_stereotype_labels((id,stereotype))` 호출.
- [x] T004 py_compile OK (3파일).
- [x] T005 현재 그래프 마이그레이션+검증: distinct stereotype별 SET 라벨 → labels 확인(FUNCTION+{Command/Query/Adapter/Validator/ApplicationService/Helper}·FILE+Service, 카운트 일치). 재분석 없이 즉시 적용.
- [ ] T006 [후속] framework 재분석으로 코드경로 영구 적용 확인(선택 — 마이그레이션으로 현재 그래프 이미 동일 상태). dbms는 데이터 없어 미검증.
- [ ] T007 [후속 spec] 소비처 이관(프론트 036 / catalog·architect 037) 후 stereotype 속성 제거(038).

## MVP
- T001-T003 = 라벨 부여(영구). T005 = 즉시 검증/활용.
