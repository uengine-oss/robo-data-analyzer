---
name: table-tracking
description: >
  **언제**: 카테고리 = `ddl` 또는 `ddl_tables`. data 에 DDL 테이블 메타 (이름·컬럼·PK·FK) 포함.
  **작업**: 각 DDL 테이블을 코드에서 역추적 → 사용하는 **모든 함수** 에 READS/WRITES (정확한 CRUD op 명시) 등록 +
  코드에서 새로 발견된 FK 등록.
  **트리거 키워드**: DDL 테이블 목록, CREATE TABLE, 테이블 역추적, columns/primary_keys/foreign_keys 메타.
  **사용 금지**: DDL 없이 엔티티 클래스 / 프로파일 힌트만 → `entity-analysis`. SQL 키 → `sql-tracking`,
  설정 키 → `config-tracking`.
  **주의**: 컬럼은 DDL 에서 이미 확보됨 — `graph_create_column_tool` 호출 X.
---

# Table Tracking Skill

DDL 테이블 메타에서 코드 사용처를 역추적해 **READS / WRITES (정확한 CRUD op) + FK** 등록. 컬럼 등록 X.

> 공통 절대 원칙·4-trail 탐색 원리·공통 안티패턴은 `prompts/linking/system.md` §3·§4·§5 참조. 이 SKILL 은 **이 카테고리 고유 사항** 만 명시.

---

## 1. 입력 데이터

- **DDL 테이블 목록**: `[(name, meta), ...]` 또는 `[{name, columns, primary_keys, foreign_keys}, ...]`.
- **`[기존 FK 목록]`** 있으면 → 중복 등록 X.
- **`[XML / 어노테이션 엔티티 힌트]`** → DDL ↔ 엔티티 매핑 참고.
- **profile (`[Project Framework · DB Access Pattern Analysis — Search Map]`)** — 있으면 DB 접근 패턴 / 명명 규칙 / 비-테이블 객체 식별 단서를 그대로 활용. 본 SKILL 은 언어/프레임워크 비종속 — 구체 패턴은 profile 가 결정.

---

## 2. 등록 엣지

### 2.1 READS / WRITES — 정확한 CRUD op 명시

각 DDL 테이블을 **모든 사용 함수** 에 등록:

- `table_name` = **DDL 정의 이름 그대로** (이미 정규화 상태).
- `source_function_id` = `bundle_search` 결과 실제 chunk_id.
- `evidence` = SQL 라인 / 호출 라인 정확한 인용.
- `access` = **정확한 CRUD op** — 통합 라벨 X. 다음 5 값 중 하나로 명시:
  - `"SELECT"` → READS 자동 유도
  - `"INSERT"` / `"UPDATE"` / `"DELETE"` / `"MERGE"` (UPSERT) → WRITES 자동 유도

  CRUD op 결정 근거는 **profile 의 Naming Rule** — DB 접근 식별자 suffix / ORM 메서드명 / SQL keyword 등. 프로젝트별로 다름. 코드 단서 0 이면 통합 라벨 `"READS"` / `"WRITES"` 호환.

### 2.2 비-테이블 객체 식별 (등록 X)

DB 접근 호출 (raw SQL · 매크로 인자 · ORM 메서드) 이 항상 테이블을 가리키는 건 아님. 다음 객체는 **테이블 등록 X**:

| 객체 | 일반 식별 단서 | 처리 |
| --- | --- | --- |
| **sequence** | DDL 테이블 메타에 없음 + DB 시퀀스 호출 패턴 (예: 입력 비어있고 출력은 단일 numeric / 명시적 시퀀스 함수 호출) | Table 노드 X |
| **stored procedure / function** | 식별자가 함수·프로시저 호출이고 DDL 테이블 메타에 없음 | Table 노드 X |
| **view** | 이름이 DDL 메타에 없고 SELECT-only 컨텍스트 | (별도 처리 — 본 SKILL 책임 X) |
| **임시·세션 객체** | 명명/메타에 임시·세션 마커 (TMP/TEMP/SESSION 등) — profile 단서로 결정 | profile 결정 따름 |

**환각 방지**: profile 또는 라이브러리 SKILL 이 "비-테이블 식별 단서" (구체 마커·접두/접미·호출 패턴) 명시돼 있으면 그것을 우선 적용.

### 2.3 FK — 코드에서 새로 발견 (DDL 외 추적)

DDL 의 PK/FK 외에 **코드에서 발견되는 FK 패턴** 도 등록.

**탐색 키워드 / 패턴 (언어 비종속)**:

| 패턴 | 의미 | 단서 |
| --- | --- | --- |
| SQL `REFERENCES <PARENT_TABLE>(<col>)` | DDL 외부 SQL 의 FK 정의 | `bundle_search("REFERENCES", kind="text")` |
| SQL `JOIN <TABLE> ON <a>.<col> = <b>.<col>` | JOIN ON 조건 | `bundle_search("JOIN", kind="text")` |
| **cross-table 컬럼 동등성** — 한 테이블 접근 결과 (출력) 가 다른 테이블 접근 입력 (인자 / 구조체 / 변수) 으로 흘러가 같은 컬럼명에 대입 | 코드 흐름 상의 PK↔FK 매핑 단서 | 변수 흐름 추적 (입력/출력 구조체 멤버 비교) |
| 명명 관례 (`<xxx>_id` / `<xxx>_num` / `<xxx>_key` 등) | 다른 테이블 PK 참조 후보 | 후보일 뿐 — 실제 매핑/JOIN/REFERENCES 단서 추가 필요 |

**등록 규칙**:

- `from_table` = FK 컬럼 쪽 (자식, 참조하는 쪽).
- `to_table` = PK 쪽 (부모, 참조 대상).
- 자기 참조 X (같은 테이블 자기 컬럼 매핑은 의미 X).
- 중복 X. **기존 FK 목록에 있으면 X**.
- 추측 X — 코드 또는 SQL 본문에 실제 단서 있을 때만 (cross-table 컬럼 동등성 / JOIN ON / REFERENCES).

---

## 3. 카테고리 특유 안티패턴

- 🚫 컬럼 등록 — DDL 에 이미 있음. `graph_create_column_tool` 호출 X.
- 🚫 mapper / Repository / 헬퍼 자체에 R/W 등록 — caller 가 등록 대상.
- 🚫 통합 라벨 `WRITES` 만 등록 — 정확한 op (INSERT/UPDATE/DELETE) 같이 명시.
- 🚫 sequence / stored procedure / function 호출을 테이블 R/W 로 등록 — raw 이름이 테이블처럼 보여도 profile/라이브러리 SKILL 의 비-테이블 식별 단서 따라 skip.
- 🚫 같은 테이블이 두 개 노드로 분리 — 정규화 룰이 비-테이블 객체의 raw 이름을 잘못 변환한 결과. profile 단서로 사전 식별 후 등록.
- 🚫 추측 FK — 코드/SQL 단서 없이 명명 관례만 보고 등록 X. `_id` 같은 명명 패턴은 후보일 뿐, 실제 매핑/JOIN/REFERENCES 단서 확인.

---

## 4. 완료 체크리스트 (이 카테고리 고유)

- [ ] DDL 의 모든 테이블 처리.
- [ ] 각 테이블 사용 함수 빠짐없이 등록 (시작 테이블 있는데 등록 0 이면 빠뜨림 의심).
- [ ] R/W 엣지에 **정확한 CRUD op** 명시 (INSERT/UPDATE/DELETE/SELECT — 통합 X).
- [ ] sequence / package 같은 비-테이블 객체는 등록 X (profile 단서로 식별).
- [ ] FK 발견 시 등록: 코드 단서 (cross-table 컬럼 동등성 또는 SQL JOIN/REFERENCES) 있을 때만. 기존 FK 와 중복 X.
- [ ] 컬럼 등록 X (DDL 보유).

전부 YES → 완료.
