# SKILL: <skill-name>

> 프레임워크/회사별 "함수 ↔ 테이블 접근 패턴 + 프레임워크 함수" 를 설명하는 문서.
> Scan Agent 가 `list_skills()` / `read_skill(name)` 로 읽고 `ScanResult` 로 정규화한다.
> **형식은 자유** — 아래는 권장 구조일 뿐, 코드가 파싱하지 않는다 (에이전트가 읽고 이해).

## 식별 단서

이 SKILL 이 매칭되는 조건 (Scan Agent 가 1단계 관찰값과 대조):
- 주 언어: <예: Java>
- 핵심 라이브러리 / wrapper: <예: ofbiz delegator, mpfmdbio>
- 명명 컨벤션: <예: camelCase 엔티티 ↔ snake_case_upper DDL>
- 매니페스트 단서: <예: pom.xml 에 ofbiz dependency>
- 호출 패턴 예: <예: `delegator.findOne(entityName, ...)`>

## 정규식 패턴

함수↔테이블 R/W 를 잡는 정규식. 명명 그룹 `<entity>`(테이블) + `<op_token>`(프레임워크 raw 매치) 필수.
Scan Agent 가 이걸 읽어 `ScanResult.table_access_patterns` 로 옮겨 담고 검증한다.

`op_mappings` 는 `op_token`(raw) → 표준 SQL `op` (SELECT/INSERT/UPDATE/DELETE) 매핑.
READ/WRITE 는 코드가 자동 매핑 (SELECT=READ, 나머지=WRITE) — LLM 이 박을 필요 X.

```yaml
table_access_patterns:
  - id: <skill_name>_<pattern_name>
    call_regex: '<regex with named groups <op_token> and <entity>>'
    op_mappings:
      - {token: <raw_token>,  op: SELECT}   # 예: findOne, s, selectOne
      - {token: <raw_token2>, op: INSERT}   # 예: save, i, insert
      - {token: <raw_token3>, op: UPDATE}
      - {token: <raw_token4>, op: DELETE}

ddl_transform:
  ddl_naming_rule: <as_is | camel_to_snake_upper | snake_to_camel>
  fallback_prefixes:
    - <T_>
    - <TB_>
```

## 프레임워크 함수 (호출 분류 시 드롭)

업무 로직이 아닌 프레임워크/런타임 함수 이름. Scan Agent 가 `ScanResult.framework_runtime_functions`
에 담고, 호출 분류에서 드롭된다 (호출 그래프 노이즈 제거).

- <예: `PFM_TRY` `PFM_MSGE` ... / ORM 의 `flush` `persist` 등>

## codebase_notes 예시

Scan Agent 가 `ScanResult.codebase_notes` 에 박을 한 단락 예. 이 단락은 함수 분석
LLM 호출마다 prefix 로 반복 주입되므로 — 핵심 사실만 짧게, 그러나 **op 결정 신호는 반드시**:

> 이 프로젝트는 ``<wrapper>`` 패턴으로 DB 접근. 엔티티 이름은 camelCase 로 코드에
> 등장하지만 DDL 은 snake_case_upper. 회사 표준 prefix ``T_`` 가 모든 테이블에 붙음.
> ``<wrapper>.<method>(entityName, ...)`` 형식 호출 87건 확인.
> **메서드명·SQL_KEY ``<op>`` 토큰이 함수의 DB 작업 종류(SELECT/INSERT/UPDATE/DELETE) 결정**
> — 정확한 매핑은 ``table_access_patterns.op_mappings`` 와 일치.

★ **op 의미는 codebase_notes 에 명시 필수** — Agent 가 이 단락만 보고 코드 주석에
끌려 잘못 해석하는 사례 발생. 식별 단서 챕터에 op 의미가 있어도 codebase_notes
단락에 빠지면 다음 단계 LLM 까지 안 전달됨 (단락은 SKILL 본문이 아닌 1단락 추출이라).

## 한계

- 정규식은 본문 그대로 — Scan Agent 가 풀어쓰면 안 됨. `verify_table_regex` 로 검증.
- 회사별 DAO 박스가 다양하면 회사 SKILL 추가 작성 필요.
