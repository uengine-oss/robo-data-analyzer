# Quickstart: 검증 절차 (spec 106)

구현 완료 판정은 아래 전부 통과 + 증거 기록 후에만 한다 (speckit 메모리 §7·§8).

## 1. 단위·계약 테스트

```bash
cd D:/work/robo/project/robo-data-analyzer
# 신설: 표 로딩·파생·오류 raise·3소비처 일치
python -m pytest tests/pipeline/dml_semantics/ -x -q
# 수정 대상 스텝 회귀
python -m pytest tests/pipeline/step5_linking/ tests/graph/ -x -q
# 전량 회귀 (FR-008)
python -m pytest -x -q
```

필수 케이스 (tasks.md에서 각 태스크에 매핑):
- TRUNCATE 리터럴 → WRITES + dml_type=TRUNCATE (US1-1)
- 같은 자기 줄 SELECT+TRUNCATE 분리 판정 (US1-2)
- 주석 처리 TRUNCATE → 엣지 0 (US1-3)
- `DELETE SGIOS.X` (FROM 생략) → WRITES / `DELETE FROM t` 종전 동일 (US2)
- `v_tab.DELETE(i)` 컬렉션 메서드 → 대상 추출 불발 (research D4 회귀 고정)
- CTAS: t=WRITES(CREATE), s=READS (D5)
- C 경로 `MERGE INTO t USING s` → t WRITES, s READS (D6)
- 같은 SQL 텍스트 DBMS/C 경로 relation 일치 (SC-005)
- 표 형식 오류 YAML → 명시 raise (계약 4)
- grep 게이트: 표 밖 DML 나열 0 (SC-003, 계약 1)

## 2. 실코퍼스 전후 대조 (SC-001·002·004)

```bash
# 변경 전 기준선: main 코드로 AMS 분석 → 엣지 스냅샷 저장
# (source_id, target_id, relation, dml_type) 정렬 dump → baseline.tsv
# 변경 후 같은 명령 → after.tsv → diff
```

- AMS_procedures.sql: 활성 TRUNCATE 전건이 WRITES+TRUNCATE로 **추가**됐는가.
  주석 3건은 여전히 0인가. 그 외 diff가 "의도 목록(TRUNCATE·FROM 생략 DELETE·CTAS·C USING)"
  밖에서 0건인가.
- GIOS PRC_KAKAO_MSG_*: `DELETE SGIOS.ARREO_SMS_KAKAO`가 READS→WRITES로 **전환**됐는가.
- RWIS·shopmall(C): diff 0 또는 의도 목록 내인가.

## 3. 실기동 검증 (원칙 §6 — 빌드 통과로 완료 금지)

1. 실제 `/analyze` 실행 (AMS 프로젝트, DBMS 전략) → 완주 확인.
2. Neo4j 직접 조회:
   ```cypher
   MATCH (f)-[r:WRITES]->(t:TABLE) WHERE r.dml_type = 'TRUNCATE'
   RETURN f.id, t.id, r.locations LIMIT 10
   ```
   locations의 file:line을 원문 소스와 개별 대조 (개수만 맞음 ≠ 정확 — 원칙 §6).
3. 권위 op 맵 렌더 1건 육안: TRUNCATE 엣지 보유 함수의 step7 컨텍스트에서
   `table_op`에 TRUNCATE가 자연스럽게 노출되는지 (plan.md Constitution V 항목).
4. 프론트 스트림: 분석 중 graph_delta에 dml_type=TRUNCATE 엣지가 오류 없이 표시되는지.

## 4. 종료 감사 (원칙 §10-9)

- `git diff` 전체 재독 — 계약 밖 변경 0, 사용자 WIP(README.md) 미접촉.
- 옛 심볼 grep 0: `_DML_KW_RE`, `_WRITE_TARGET_RE`, `_SQL_KEYWORD_RE`, `_C_SQL_START_RE`,
  `_C_SQL_WRITE_TARGET_RE`, `_C_SQL_READ_TARGET_RE`, `from graph.schema.graph_edges import derive_dml_type`.
- 미검증 항목은 검증됨과 분리해 tasks.md에 기록.
