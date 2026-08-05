# Research: DML 문법 의미표 — 결정 기록

모든 결정은 2026-07-30 현재 코드·`분석대상모음` 실측·벤치마킹 딥리서치 2회에서 나왔다.
리서치 원문 결론 중 채택·기각 경위는 spec.md 근거 절 참조.

## D1. 의미표 위치 — `pipeline/dml_semantics.yaml` + `pipeline/dml_semantics.py`

- **대안 A**: ontology_roles.yaml에 섹션 추가 — 기각. ontology_roles는 "AST 노드 타입 → 역할" 표,
  이건 "SQL 텍스트 구문 → R/W 의미" 표. 변경 이유가 다르다(헌법 VIII: 같은 이유로 바뀌는 것끼리).
- **대안 B**: languages.py에 로더 추가 — 기각. languages.py는 확장자 정책 소유자. 정규식 파생
  빌더까지 넣으면 책임 비대.
- **채택**: ontology_roles.yaml과 같은 "pipeline 소유 언어 데이터 표" 관례로 YAML 신설,
  로더+파생은 전용 모듈. 소비자 3곳(step5 링커 2, step8 lineage 1)이 공유하는 최협소 소유자.

## D2. `derive_dml_type` 이사 — graph/schema → pipeline/dml_semantics.py

전수조사: 호출자 4곳 전원이 pipeline 계층(regex_table_linker ×2, link_dbms_tables,
enrich_lineage_from_catalog). graph/schema는 정의만 하고 스스로 안 쓴다.
화이트리스트를 표에서 파생시키려면 graph→pipeline import가 필요해지는데 이는 계층 역방향.
→ 함수 본체를 의미표 모듈로 이사, graph_edges.py에는 dataclass와 주석만 남긴다.
alias·재export 금지(원칙 §3 회귀금지) — 옛 import 경로 grep 0으로 마감.
`normalize_relation`은 relation(READS/WRITES) enum 정규화로 이번 범위와 무관 — 잔류.

## D3. WITH의 dml_type — 현행(빈 문자열) 보존

현행: `_first_dml`이 "WITH" 반환 → `derive_dml_type("WITH")` → 화이트리스트 밖 → `""`.
즉 WITH-선두 문은 relation=READS, dml_type="" 이다. SELECT로 매핑하면 개선처럼 보이나
FR-008(기존 정상 판정 불변)과 충돌하는 diff를 만든다. → 표에 `WITH: {relation: READS,
dml_type: null}`로 명시해 **현행 보존을 의도로 기록**. 개선은 별도 spec.

## D4. FROM 생략 DELETE — 코어 배치

Oracle·MySQL·SQLite 모두 합법, 표준만 FROM 요구. 방언 섹션에 넣으면 C 리터럴 SQL
(Pro*C 계열, 방언 특정 불가)에서 못 쓴다. FROM 필수 방언에서도 `DELETE <식별자>` 텍스트가
합법 소스에 나타날 수 없어 과대매칭 위험 없음 → 코어 배치. 대상 추출 구문은
긴 구문 우선(`DELETE FROM` 먼저, `DELETE` 나중) 정렬로 이중 매칭 방지.
PL/SQL 컬렉션 메서드 `v_tab.DELETE(i)`는 `DELETE` 뒤 공백+식별자가 없어 대상 추출 불발 —
기존에도 키워드 감지는 됐으므로 회귀 아님(테스트로 고정).

## D5. CTAS·CREATE TABLE — dml_type `CREATE`, 대상 WRITES

현행은 CREATE 미인식이라 `CREATE TABLE t AS SELECT … FROM s`에서 본문 내 SELECT만 감지 →
t가 READS로 오분류(생성 대상인데). 표에 `CREATE: {relation: WRITES, target_after:
["CREATE TABLE"]}` 추가. AS SELECT 소스는 기본 READS 규칙이 그대로 처리.
순수 DDL `CREATE TABLE t`도 WRITES — TRUNCATE와 같은 "DDL이지만 데이터 영향" 취급.
실측 증거는 TRUNCATE(46건)·FROM 생략 DELETE(GIOS)이고 CTAS는 문법 사실 기반 예방 —
spec FR-002에 사용자 승인된 코어 목록.

## D6. C 경로 read 절에 USING 추가 — 의도된 개선으로 명시

DBMS 경로는 "write 대상 아닌 본문 테이블 = READS" 기본값이라 MERGE USING 소스가 이미 READS.
C 경로는 read 절이 `FROM|JOIN` 열거라 USING 소스 누락. 표의 read 절 데이터에 USING을 넣어
두 경로가 같은 표에서 파생 → SC-005(두 경로 판정 일치). SC-004 스냅샷 diff에서
"의도된 추가"로 분류한다.

## D7. 표 표현력 경계 — 미니 언어 금지

표가 담는 것: 키워드, relation, dml_type, 대상-직후 구문 목록, read-절 구문 목록, 방언 델타.
표가 담지 않는 것: 임의 정규식, 조건 로직, 콜백. 정규식 조립은 파생 모듈의 단일 빌더
(`\b(?:<구문>)\s+(?:[\w$]+\.)?(<식별자>)` 골격)만 수행한다.
근거: 사용자 지적 — "표라고 부르면서 로직이 표 안에 숨으면 미니 언어가 되고 유지보수 지옥".
표에 안 들어가는 미래 구문(예: PostgreSQL COPY의 특수 형태)이 나타나면 표 스키마를
버전업하는 별도 spec으로 다룬다. 조용한 확장 금지.

## 실측 근거 재확인 명령 (검증 시 재사용)

```bash
# TRUNCATE 실측 (46회, 주석 포함)
grep -c "EXECUTE IMMEDIATE 'TRUNCATE" "분석대상모음/dbms/oracle/AMS/AMS_procedures.sql"
# FROM 생략 DELETE 실측
grep -rn "EXECUTE IMMEDIATE 'DELETE SGIOS" "분석대상모음/dbms/oracle/수자원/"
```
