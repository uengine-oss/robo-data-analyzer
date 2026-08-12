# tasks 117 — DBMS rule 의무 행 소유권

각 task = `red 반증 → 구현 → 표적 검증 → 전체 회귀 → diff 감사`. 코드 작성만 끝난 task 는 진행 중이다.

---

- [x] **T1. red — 결함을 고정한다**
  `tests/pipeline/step7_analyze_code/test_rule_row_ownership.py` 신규.
  S1(배분 합 == 루틴 행, 중복 0) · S5(framework 는 전체 행) 을 먼저 실패시킨다.
  **완료 증거**: 구현 전 실행에서 두 케이스가 실패하는 출력.

- [x] **T2. 배분 규칙 구현**
  `pipeline/steps/step7_analyze_code/rule_row_ownership.py` 신규 —
  `owning_routine(node, code_by_id)` · `assign_rows(rows, units, code_by_id)`.
  "가장 안쪽 포함 노드 하나" + 소유자 없는 행은 좌표 담은 `ValueError`.
  **완료 증거**: T1 green.

- [x] **T3. 분기표 조회 진입점 전환**
  `control_prompt.py` — 소유 루틴의 fn/ctx 로 `build_inventory` 를 호출하는 `routine_inventory()`.
  `control_index()` 도 루틴 분기표를 쓰도록.
  **완료 증거**: S6(경로 조건 보존) green.

- [x] **T4. 세 소비 경로를 같은 답으로 통일**
  `batch_analyze.py` 의 ①프롬프트 조립(`concern == "rules"`) ②`ModelOutputError` finding 좌표
  ③`_persist` 채점 — 전부 `routine_inventory` + `assign_rows` 결과를 쓴다.
  **완료 증거**: 세 경로가 같은 행 집합을 보는 계약 테스트.

- [x] **T5. 빈 의무 배치 가드 (헌법 IV)**
  배분 행 0개인 노드는 rule 요청을 만들지 않는다.
  **완료 증거**: S3 green — 호출 0, 예외 없음, 회계에 `0행 배정`으로 드러남.

- [x] **T6. 소유자 없는 행 실패 계약**
  **완료 증거**: S4 green — 좌표(루틴 id·행 번호)가 메시지에 있다.

- [x] **T7. 1층 전체 회귀**
  `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .` (`-t .` 필수)
  `.venv\Scripts\python.exe scripts\check_byte_identical.py`
  **완료 증거**: 885+ OK · 28 byte-identical.

- [ ] **T8. framework 무변경 증명**
  기존 run `logs/20260807_020513_7ca69135e7ba` 를 `judge_run.py` 로 재판정해 `[A] 5210` 유지 확인
  (코드 변경이 산출물을 바꾸지 않았는지는 **다음 framework run 에서만** 최종 확인 가능 — 그 전까지 `미검증`).

- [ ] **T9. DBMS 실행 (상한 4회 중 1회)**
  코퍼스를 `분석대상모음\dbms\oracle\rwis` → `project\data` 로 교체(이전 코퍼스는 보관소로 되돌린다).
  DDL 은 `분석대상모음\dbms\oracle\수자원\ddl\RWIS.sql` 하나만.
  `--database neo4j` 로만 실행.
  **완료 증거**: `judge_run.py [A]` 의무 행 > 0, 회계 잔차 0.

- [ ] **T10. 2층 — 결정론 정답지 대조**
  cond/then 채점 통과율, rwis gold(`분석대상모음\dbms\oracle\rwis\gold\`) 구조 사실 대조.
  **완료 증거**: 축별 수치. 안 잰 축은 `미검증`.

- [ ] **T11. 3층 — 직접 의미 판정**
  04 §4 절차. 표본은 결과 열람 **전에** 고정한다.
  **완료 증거**: 판정표 발행.

- [ ] **T12. 문서 누적**
  10 §6.C·§8·§17 에 결과를 그때그때 반영. 09 에도 차이를 적는다.

---

## 진행 원장

| 시각 | task | 결과 |
|---|---|---|
| 2026-08-07 | 원인 확정 | 10 §6.C **F-C5** 로 정정 누적. 09 에도 정정 삽입 |
| 2026-08-07 | 설계 확정 | **C2** 채택 (사용자). C1·B 폐기 이유는 spec §2 |
| 2026-08-07 | T1 red | `ModuleNotFoundError: rule_row_ownership` — 결함 고정 |
| 2026-08-07 | 계약 보정 | 소유자 집합에 **루틴 자신을 항상 포함**해야 entry 행 잔차가 0이 된다. spec §3.1 에 반영 |
| 2026-08-07 | T2~T6 green | 신규 계약 10/10. `rule_row_ownership.py` 신설, `control_prompt.routine_inventory` 추가, batch_analyze 세 경로 통일 + 빈 의무 가드 |
| 2026-08-07 | T7 통과 | **895 OK**(skipped 5, 종전 885 + 신규 10) · **28 byte-identical** · 죽은 import `required_rule_rows` 제거 |
| 2026-08-07 | T9 실행 | 코퍼스 교체(framework → 보관소에 바이트 동일 보존, rwis 9소스 + RWIS.sql 1) · parser 재실행(정확 9 · 실패 0 · 1,802라인) · run `20260807_085619_d4e5633b96bb` `--database neo4j` exit 0. **DBMS 상한 4회 중 1회 소진** |
| 2026-08-07 | **T9 결과** | **의무 행 0 → 21 · 응답 21(100%) · 행 있는 노드 15/15 · 빈 의무 배치 0 · findings 5.** 헌법 IV 위반 상태 해소 |
| 2026-08-07 | 검사기 결함 수리 | `judge_run [A]` 가 DBMS 에서 항상 0이던 원인은 **측정 부재**(branch-tables 를 framework 엔진만 기록). `branch_audit.py` 신설로 이관 + DBMS 루틴당 1회 기록. 회귀 **897 OK** · 28 byte-identical |
| 2026-08-07 | 후속 결함 3종 발견 | F-C8(parser 가 `EXIT WHEN` 조건 유실) · F-C9(조건 없는 LOOP 을 decision 강제 → findings 5의 진짜 원인) · F-C7(ELSIF·EXCEPTION·EXIT 미분류). **전부 spec 117 범위 밖**, 10 §6.C-4 · §17 순서 2-b |
| 2026-08-07 | 미검증 | T8(framework 무변경 — 다음 framework run 에서만 확인 가능) · T10(2층 정답지 대조) · T11(3층 의미 판정) · `[A]` 실측(수리 후 재실행 필요) |
