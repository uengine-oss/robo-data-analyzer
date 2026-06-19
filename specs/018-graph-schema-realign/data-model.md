# Phase 1 Data Model: 그래프 스키마 (최종)

식별자 = `id` 단일(refactor 유지). 라벨 = UPPER_SNAKE. **종류 차이 = 노드 라벨, 관계 = 범용.**

## 노드 (라벨 = 실제 정체대로)

| 계층 | 라벨 | 설명 | 전략 |
|---|---|---|---|
| 네임스페이스 | `FOLDER` · `PACKAGE` | 폴더 / Java·PL-SQL 패키지 | 공통 |
| 그릇(컨테이너) | `FILE` · `CLASS` · `PACKAGE` | C/Python/DBMS=FILE, Java=CLASS, 패키지=PACKAGE. **파일은 진짜 그릇일 때만 노드**(Java=클래스가 그릇→파일 생략) | 공통 |
| 멤버(루틴) | `PROCEDURE` · `FUNCTION` · `METHOD` · `TRIGGER` | 호출 가능 단위 | 공통 |
| 멤버(데이터) | `VARIABLE` · `CONSTANT` · `FIELD` · `PARAMETER` | 데이터 선언 | 공통 |
| 구문(DBMS) | `SELECT`·`INSERT`·`UPDATE`·`DELETE`·`MERGE`·`IF`·`ELSIF`·`ELSE`·`LOOP`·`CASE`·`CALL`·`COMMIT`·`RETURN`·`UNION_ALL`·`ASSIGNMENT`·`FETCH`·`OPEN_CURSOR`·`CLOSE_CURSOR`·`EXIT`… | antlr type = 라벨 | DBMS |
| 구문(구조) | `DECLARE`·`TRY`·`TRIGGER_BLOCK` | **노드 유지 + LLM 분석 제외**. 표시 라벨 단일맵: DECLARE="변수 선언부", TRY="예외 처리", TRIGGER_BLOCK="트리거 본문" + "구조" | DBMS |
| DB | `TABLE` · `COLUMN` · `SCHEMA` | DB 구조 | 공통 |
| 의미(EM) | `RULE` · `EXAMPLE` · `QUESTION` | 예제매핑 산출물 | 공통 |

## 관계 (단일 진실 = `rel_types.py`)

| 관계 | 뜻(느낌) | 끝점(예) | 전략 | 변경 |
|---|---|---|---|---|
| `BELONGS_TO` | 소속(네임스페이스) | 그릇→FOLDER/PACKAGE, TABLE→SCHEMA | 공통 | ⬅ BELONGS_TO_PACKAGE 통합 |
| `HAS_MEMBER` | 가지다(멤버) | 그릇→루틴/데이터 멤버 | 공통 | ⬅ HAS_FUNCTION+HAS_VARIABLE+HAS_MEMBER 통합 |
| `PARENT_OF` | 중첩 | 부모 노드→자식 노드 | 공통(framework=중첩함수만) | 유지 |
| `NEXT` | 실행 순서(흐름) | 이전 구문→다음 구문 **(DBMS, 신규)** / RULE→RULE **(EM 메인흐름, 기존 유지)** | DBMS(구문)·공통(룰) | ➕ 구문용 복원(끝점 라벨로 룰흐름과 구분) |
| `CALLS` | 호출 | 루틴→루틴 | 공통 | 유지 |
| `READS` / `WRITES` | 테이블 접근 | 루틴→TABLE | 공통 | 유지 |
| `REFERENCES` / `INIT_BY` | 변수 참조/초기화 | 루틴→변수 / 변수→출처 | 공통 | 유지 |
| `HAS_COLUMN` / `FK` | DB 구조 | TABLE→COLUMN / TABLE→TABLE | 공통(DB) | 유지 |
| `HAS_RULE`/`HAS_EXAMPLE`/`HAS_QUESTION`/`AFFECTS_TABLE` | 의미(EM) | 함수→RULE 등 | 공통 | 유지 |
| `EXTENDS` / `IMPLEMENTS` | 상속 | CLASS→CLASS | **Framework 전용** | 유지 |

**HAS_MEMBER 범위 규칙**: 그릇→**루틴(프로시저/함수)** 에만. 루틴 내부 구문은 HAS_MEMBER 아님 → `PARENT_OF`(중첩)+`NEXT`(흐름)로만.

## 핵심 불변식 (검증 대상)

1. 모든 코드 노드(구조 포함) 존재 — 원문 대비 노드 누락 0.
2. DBMS 인접 형제 구문은 NEXT 로 연결 — 흐름 추적 가능.
3. HAS_MEMBER 의 자식은 라벨이 루틴(PROCEDURE/FUNCTION/METHOD/TRIGGER)만 — 일반 구문 0건.
4. 구조 노드(DECLARE/TRY/TRIGGER_BLOCK)는 노드 존재 + LLM 산출(summary/rules) 비어도 정상.
5. 같은 라벨 = 같은 색·같은 의미(재현성). 옛 관계명(HAS_FUNCTION 등) 잔재 0.
6. 룰: 단위당 의미 중복 제거, 하위 세부 정확도 보존.

## 두 전략 구조 (같은 뼈대, 채우는 칸만 다름)

```
 공통:  FOLDER ◄BELONGS_TO─ 그릇 ─HAS_MEMBER► 루틴 ─CALLS► 루틴
                                              └READS/WRITES► TABLE
 DBMS:  루틴 ─PARENT_OF► 구문 ─NEXT► 구문   (흐름)
 FW:    CLASS ─EXTENDS► CLASS               (상속, DBMS 없음)
```
