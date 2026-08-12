# tasks 118 — 조건 없는 행의 역할과 PL/SQL 분기 커버리지

각 task = `red 반증 → 구현 → 표적 검증 → 전체 회귀 → diff 감사`. 코드 작성만 끝난 task 는 진행 중이다.

---

- [x] **T1. red — 조건 없는 행 결함을 고정한다**
  `tests/pipeline/quality_gates/test_conditionless_row_role.py` 신규.
  S1(sql bare LOOP) · S2(c `for(;;)`) · S4(조건 있는 LOOP 불변) · S5(CASE default 불변).
  **완료 증거**: 구현 전 S1·S2 실패.

- [x] **T2. `prompt_role()` 언어 중립 규칙**
  `branch_inventory.Branch.prompt_role()` — 조건이 빈 decision → `controller`.
  CASE caseless → complement 판정이 먼저다.
  기존 `FinallyWithConsumptionTest`(S3)의 기대값을 갱신하고 **사유를 주석에 남긴다**.
  **완료 증거**: T1 green + S3 green.

- [x] **T3. 온톨로지 PL/SQL 편입**
  `pipeline/ontology_roles.yaml` — `roles.control_structure.sql: [IF, ELSE, ELSIF, LOOP, TRY, EXCEPTION]`,
  `control_roles.decision` 에 `ELSIF`·`EXCEPTION` 추가. `FINALLY` 주석 정정.
  **완료 증거**: S6·S7 green.

- [x] **T4. 평면 여집합 사슬**
  complement 행이 소유자 조건 + **선행 형제 decision 전부**를 부정한다.
  **완료 증거**: S8 green **그리고** S9(c 중첩 사슬)가 기존과 완전 동일.

- [x] **T5. parser — `EXIT WHEN` 조건 보존 (F-C8)**
  `antlr-code-parser` `PlSqlAstListener.enterExit_statement` 가 `enterElsif_part` 와 같은
  계약으로 조건식 원문을 `expression` 에 싣는다. parser 테스트 S10·S11 추가.
  **완료 증거**: `mvnw.cmd test` green, rwis AST 재생성 후 `EXIT.expression` 5/5 채워짐.

- [x] **T6. `EXIT` 분류**
  T5 로 조건이 실재하는 것을 확인한 뒤에만 `roles.control_structure.sql` 과
  `control_roles.decision` 에 `EXIT` 추가.
  **완료 증거**: rwis 오프라인 하네스에서 EXIT 행 5개가 cond 를 갖고 생성됨.

- [x] **T7. 1층 전체 회귀 + 오프라인 결정론 대조**
  `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -t .` (`-t .` 필수)
  `.venv\Scripts\python.exe scripts\check_byte_identical.py`
  오프라인 하네스로 의무 행 **전(25) → 후** 를 루틴별로 대조. **run 예산 0 소진.**
  **완료 증거**: 897+ OK · 28 byte-identical · 루틴별 증감표.

- [ ] **T8. DBMS 실행 (상한 3회 중 1회)**
  `--database neo4j` 로만. 코퍼스는 `project\data`(rwis 현행 유지).
  **완료 증거**: `judge_run [A]` 의무 행 실측(branch-tables 기록됨), 회계 잔차 0,
  `decision 행은 cond가 null일 수 없음` finding **0**.

- [ ] **T9. 2층 — 결정론 정답지 대조**
  cond/then 채점 통과율, rwis gold(`분석대상모음\dbms\oracle\rwis\gold\`) 구조 사실 대조.
  **완료 증거**: 축별 수치. 안 잰 축은 `미검증`.

- [ ] **T10. 3층 — 직접 의미 판정**
  04 §4 절차. 표본은 결과 열람 **전에** 고정한다.
  **완료 증거**: 판정표 발행.

- [ ] **T11. 문서 누적**
  10 §6.C·§8·§17 에 그때그때 반영. 09 에도 차이를 적는다.

---

## 진행 원장

| 시각 | task | 결과 |
|---|---|---|
| 2026-08-08 | 착수 전 실측 | spec 117 은 **미커밋 WIP** 로 존재. 전체 테스트 **897 OK** · 28 byte-identical · `project\data` = rwis 9소스 |
| 2026-08-08 | 오프라인 하네스 확립 | 생산 경로 그대로 태워 rwis 의무 행 **25** 산출 — 실제 run 의 21+4 와 정확히 일치. **run 예산을 쓰지 않고 전후 대조 가능** |
| 2026-08-08 | 결함 ① 언어 중립 확인 | framework findings 14 중 1건이 같은 사유(`zapamcom10200.c:1948` = `for(;;)`). **DBMS 5 + framework 1** |
| 2026-08-08 | 설계 확정 | **controller** 채택(사용자). 폐기 대안은 spec §2.1 |
| 2026-08-08 | T1 red | S1(sql bare LOOP)·S2(c `for(;;)`) 실패, S4·S5 통과 — 결함 고정 |
| 2026-08-08 | T2 green | `prompt_role()` 언어 중립 규칙 1개. 기존 `FinallyWithConsumptionTest` 를 정정하고 **사유를 독스트링에 남김**(채점기와의 모순 해소) |
| 2026-08-08 | 사용자 정정 | 내가 "controller 와 증거가 같다"고 설명한 것은 **오답**. controller 행은 의무가 아니라 규칙 0건이 된다 → **EXCEPTION 은 `handler` 역할**로 확정(사용자) |
| 2026-08-08 | T3 green | 온톨로지 `sql` 편입 + `control_roles.handler: [EXCEPTION]` 신설. `_condition_path` 가 handler 에도 조상 경로를 준다 |
| 2026-08-08 | T4 green | **새 결함 발견·수리**: 평면 ELSIF 사슬의 경로가 정반대였다(F-C21). `alternative_clause_types` 데이터 신설 + 보정 1패스. C 중첩 형상 불변(S9) |
| 2026-08-08 | T5 green | parser `enterExit_statement` 조건 보존. `mvnw test` **135 tests 0 failures**. AST 재생성 후 EXIT 조건 5/5 채워짐, **라벨 계수는 재생성 전후 동일** |
| 2026-08-08 | T6 green | `EXIT` → control_structure + decision. 의무 52 → 57 |
| 2026-08-08 | **새 결함 수리** | 트리거가 규칙 0건이던 원인은 `build_control_subtrees` 가 `parent_id` 를 직접 키로 써 `TRIGGER_BLOCK` 아래 IF 를 잃은 것(F-C28). 소유자 해소를 `build_statement_facts` 와 같은 계약으로 통일 → 의무 57 → **59** |
| 2026-08-08 | T7 통과 | 전체 회귀 **913 OK** · 28 byte-identical · **framework 증거 무변경**(4,100행 중 의도한 1행) |
| 2026-08-08 | 파생 트랙 | 효과 축이 32%뿐인 것이 드러나 **spec 119** 신설(사용자 승인: run 전에 배선). 의무 59 → 60, 효과 83% |
