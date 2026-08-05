# Contract: DML 의미표 → 소비자 파생 계약

**소유자**: `pipeline/dml_semantics.py` (표: `pipeline/dml_semantics.yaml`)
**버전**: 1 (spec 106) | **대체**: spec 006 FR-013의 키워드 집합·대상 추출 규칙 출처

## 계약 1 — 파생 유일성

DML 키워드 집합, write 대상 추출 구문, read 절 구문, dml_type 허용 집합은
`pipeline/dml_semantics.yaml`에서만 나온다. 아래 심볼은 **삭제**되며 재도입 금지:

- `link_dbms_tables_step._DML_KW_RE`, `._WRITE_TARGET_RE`
- `regex_table_linker._SQL_KEYWORD_RE`, `._C_SQL_START_RE`, `._C_SQL_WRITE_TARGET_RE`, `._C_SQL_READ_TARGET_RE`
- `graph_edges.derive_dml_type` (본체 이사 — 옛 경로 import 잔존 0)

**집행**: 저장소 grep 게이트 테스트 — 의미표·그 테스트 밖에서
`SELECT|INSERT|UPDATE|DELETE|MERGE` 형태의 DML 대체 나열(regex alternation 정의) 0건.

## 계약 2 — 소비자별 보장

| 소비자 | 받는 것 | 보장 |
|---|---|---|
| `link_dbms_tables_step` | detect_regex, write_target_regex, dml_type_of | 자기 줄 스캔·주석 제거·문자열 보존 계약은 **불변**. 판정 규칙 출처만 교체 |
| `regex_table_linker` C 경로 | start_regex, write_target_regex, read_target_regex, dml_type_of | 같은 SQL 텍스트에 대해 DBMS 경로와 relation 판정 동일 (SC-005 공유 파생 테스트) |
| `regex_table_linker` 스캔패턴 경로 | dml_type_of, relation_of | op_mappings(에이전트 발견 커스텀 op)는 이 계약 **밖** — 프로젝트 관행 영역, 무변경 |
| `enrich_lineage_from_catalog_step` | dml_type_of, (normalize_relation은 graph_edges 잔류) | catalog `/lineage` HTTP 계약 무변경. import 경로만 이동 |

## 계약 3 — dml_type 값 집합 확장의 하위 호환

- 신규 값: `TRUNCATE`, `CREATE`. 기존 값 의미 불변, `WITH` → `""` 현행 보존.
- 소비자는 dml_type을 열거 제한 없는 문자열로 다룬다(2026-07-30 전수조사: upsert·스트림·
  evidence·권위 op 맵·architect·frontend 전부 pass-through 또는 미사용 — plan.md 표).
- 이후 dml_type 값을 **필터·분기 조건으로 쓰는 소비자를 새로 만들 때**는 이 표를 import해서
  집합을 파생할 것 — 리터럴 나열 금지 (계약 1 준용).

## 계약 4 — 오류 처리

- 표 파일 부재·YAML 파싱 실패·relation 미허용 값·target_after 빈 구문: **로딩 시 즉시 raise**.
  조용한 기본값 폴백 금지 (헌법 IV). 파이프라인은 명시 오류로 중단된다.
- `dml_type_of(미등재 토큰)` = `""` — 비표준 op 차단 계약 유지 (구 derive_dml_type 동일).

## 대체 선언

spec 006 FR-013 중 다음 부분이 본 계약으로 대체된다:
- ~~"첫 DML 키워드 SELECT/WITH=READS · INSERT/UPDATE/DELETE/MERGE=WRITES"~~ → 표의 statements
- ~~"write 대상 = INSERT INTO / UPDATE / DELETE FROM / MERGE INTO 직후"~~ → 표의 target_after
유지되는 부분: 자기 줄 스캔 방식, 주석 제거·문자열 보존, 정체성 `(source, target, relation)`,
"DML 키워드 없으면 노드 건너뜀".
