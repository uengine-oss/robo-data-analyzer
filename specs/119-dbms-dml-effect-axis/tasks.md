# tasks 119 — DBMS DML 효과 축

각 task = `red 반증 → 구현 → 표적 검증 → 전체 회귀 → diff 감사`.

---

- [x] **T1. 결함 계량** — framework 저장 프롬프트 전수와 DBMS 오프라인 실측을 같은 축으로 비교.
  **완료 증거**: framework 3,398/4,100(83%) vs DBMS 19/59(32%).

- [x] **T2. `dml_effect_ops` 데이터 선언** — `ontology_roles.yaml`.
  미선언 `dml_type` 은 좌표와 함께 실패(헌법 IV).
  **완료 증거**: S6 green.

- [x] **T3. step6 생산자** — `_table_op_lines(rw_edges, code_nodes)` 가 소유 routine 상대 줄로
  `(줄, op, 테이블)` 을 만든다. `ctx.extra["ast_table_ops"]`.
  **완료 증거**: 오프라인 165건 좌표 산출.

- [x] **T4. inventory 소비** — `build_inventory(..., table_ops=)`, `_ast_line_facts` 귀속,
  `_ast_entry_line` 후보 편입, `direct_statement_effects` 세 경로.
  **완료 증거**: S1~S4 green.

- [x] **T5. `CALL` 호출 좌표** — `callsite_labels` 데이터 선언 + step6 파생.
  reader 에 call_site 를 **추가하지 않는다**(CallLinker 와 생산자 중복 방지).
  **완료 증거**: 효과 보유율 48% → 83%.

- [x] **T6. framework 무변경** — `table_ops` 미제공 시 증거 완전 동일.
  **완료 증거**: S5 green + `evidence_preflight diff` 차이 1건(spec 118 의 의도한 행).

- [x] **T7. 상설 도구 승격 (04 §4.5)** — `scripts/evidence_preflight.py` (`measure` / `diff`).
  **완료 증거**: 두 모드 실행. framework 재현 4,099행·83%.

- [x] **T8. 1층 전체 회귀** — **913 OK** · 28 byte-identical.

- [ ] **T9. DBMS 실행** — spec 118 T8 과 **같은 run 으로** 처리(상한 절약).
  **완료 증거**: 효과 있는 규칙 비율, 채점 통과율, 회계 잔차 0.

- [ ] **T10. 2층·3층** — spec 118 T9·T10 과 함께.

- [ ] **T11. 문서 누적** — 10 §6.C-6.

---

## 진행 원장

| 시각 | task | 결과 |
|---|---|---|
| 2026-08-08 | 착수 | spec 118 수리 후 의무 59행 중 효과 보유 19행(32%)뿐임이 드러남. framework 는 83% |
| 2026-08-08 | 설계 | **새 판정 기계 0.** step5 링커가 DML 노드마다 이미 만든 `(테이블, dml_type)` 을 `ast_call_lines` 와 같은 경로로 잇는다. `EFFECT_FUNCTIONS` 에 select/insert/update/delete/upsert 가 **이미 선언**돼 있던 것이 설계 의도의 증거 |
| 2026-08-08 | T2~T4 | 효과 보유 32% → **48%**. 트리거 최상위 INSERT 가 entry 행을 만들어 의무 59 → 60 |
| 2026-08-08 | T5 | 남은 구멍의 정체가 `CALL` 59개임을 확인(handler 20행이 `DBMS_OUTPUT.PUT_LINE` 뿐). 온톨로지 `callsite_labels` 선언으로 **83% — framework 동률**. handler 는 28/28 로 framework(0/67)보다 낫다 |
| 2026-08-08 | 지뢰 발견 | 상설 도구를 인자 없이 돌리다 `.env` 의 `ROBO_DATA_DIR` 이 **보관소**를 가리키는 것을 발견(F-C29). `project\data` 로 되돌림 |
| 2026-08-08 | T6~T8 | framework 증거 무변경 재확인. 913 OK · 28 byte-identical |
