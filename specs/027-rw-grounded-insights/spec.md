# Feature Specification: R/W-그라운디드 테이블·컬럼 인사이트 (027-rw-grounded-insights)

**Feature Branch**: `refactor` (analyzer 고정)
**Created**: 2026-06-26
**Status**: Draft
**Amended**: 2026-06-28 — FR-005 정밀화(드롭 표면화 심각도를 디스패처 예상 드롭=INFO / 진짜 오귀속=WARNING 으로 분리, error.log 소음 제거). 데이터·필터 판정 불변.

**Input**: step6 코드 분석 에이전트가 받는 컨텍스트가 결정론 R/W 사실에 그라운드되지 않아 ① 테이블 컬럼 목록 미주입으로 컬럼 설명 누락, ② callee 함수 목록 노출로 LLM이 callee 함수명을 테이블로 오귀속.

## 배경 (라이브 실측, 2026-06-26)

분석 그래프 품질 감사에서 확정된 두 결함 — **둘 다 분류/매칭 코드가 아니라 "LLM에 먹이는 컨텍스트"가 결정론 사실에 그라운드되지 않은 것**:

- **① 컬럼 설명 누락**: COLUMN.analyzed_description 10/73 빈값. 빈 컬럼(cust_num·acnt_num·bank_card_num 등)은 **실제 DB 연산에 쓰임**(예: `zpay_ap_rltm_auth_hst_i0002_in.cust_num = ...` INSERT). 그러나 step6 컨텍스트(`_build_table_contexts`)는 테이블 **prose 설명만** 넘기고 **컬럼 목록은 미주입**(라이브 datasource 없으면 샘플행 0). LLM은 코드 본문 struct 멤버에서 컬럼을 추출해야 해 누락. `fqn(table,col)`==노드 id라 생성만 됐으면 매칭됐을 것 → 미생성 확정.
- **② 테이블 오귀속**: 28/91 column-description 미반영(경고) + 가짜 AFFECTS_TABLE. 원인: 컨텍스트가 **callee 함수 목록을 통째 전달** → reads/writes 빈 디스패처 함수(c300, reads=[]·writes=[])가 callee `e800_dbio_rltm_auth_hst_u0004`(함수)를 **테이블로 착각**해 AFFECTS_TABLE 생성. (callee 테이블 정보는 불필요 — 각 함수는 자기 직접 R/W만 다뤄야 함.)

공통: **dbms·framework 동일**(컨텍스트 빌더·렌더러 공유). 분류·매칭 코드(call_classifier·fqn·affects_table upsert)는 정상.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - R/W 테이블 컬럼이 빠짐없이 서술된다 (Priority: P1)

DB를 읽고/쓰는 함수를 분석하면, 그 함수가 실제로 다루는 테이블 컬럼들이 `analyzed_description`에 채워진다 — 코드 본문에서 LLM이 우연히 알아채는 컬럼뿐 아니라, R/W로 연결된 테이블의 **알려진 컬럼 목록** 기준으로.

**Why this priority**: 분석 산출물(컬럼 의미)의 완성도 직결. P1.

**Independent Test**: 동일 입력 재분석 후, R/W 함수가 쓰는 컬럼(cust_num 등)의 analyzed_description 채움 확인. 빈 컬럼 수 감소.

**Acceptance Scenarios**:
1. **Given** 함수가 zpay_ap_rltm_auth_hst 에 INSERT(cust_num 등 포함), **When** 분석, **Then** 그 컬럼들이 컨텍스트의 컬럼 목록으로 LLM에 주어지고 analyzed_description 이 채워진다.

### User Story 2 - 테이블 귀속은 직접 R/W로 제한된다 (Priority: P1)

함수의 테이블 영향(AFFECTS_TABLE / table_insights table)은 **그 함수의 결정론 reads/writes 집합**으로 제한된다. callee 함수명이 테이블로 박히지 않는다. 직접 R/W 없는 디스패처 함수는 테이블 귀속 0.

**Why this priority**: 가짜 테이블/유실 제거. P1.

**Independent Test**: c300 류(reads/writes 빈) 함수가 AFFECTS_TABLE 0 인지, 가짜 테이블 노드/엣지 0 인지 확인.

**Acceptance Scenarios**:
1. **Given** reads=[]·writes=[] 디스패처 함수 + callee가 DBIO 함수, **When** 분석, **Then** 그 함수의 AFFECTS_TABLE = 0 (callee 함수명이 테이블로 안 박힘).
2. **Given** reads/writes 빈 디스패처에서 LLM이 callee 테이블을 귀속, **When** 영속 전, **Then** 전부 드롭 + **INFO**로 표면화(analyzer.log만, error.log 미오염 — 예상된 정상 동작).
3. **Given** reads/writes 있는 함수에서 LLM이 집합 밖 테이블을 1건이라도 귀속, **When** 영속 전, **Then** 그 건 드롭 + **WARNING**으로 표면화(error.log — 점검 가치 있는 진짜 오귀속).

### Edge Cases
- 정당하게 컬럼을 안 쓰는 R/W(테이블 전체 SELECT * 등) → 컬럼 목록 주되 강요 안 함.
- 라이브 datasource 있어 샘플행도 있는 경우 → 샘플 + DDL 컬럼 목록 병존(중복 제거).
- 컬럼이 매우 많은 테이블 → 컨텍스트 비대(상한/요약 고려).

## Requirements *(mandatory)*

- **FR-001**: step6 테이블 컨텍스트는 각 R/W 테이블의 **알려진 컬럼 이름 목록**(DDL/카탈로그 유래)을 포함해야 한다(현 prose 설명에 더해). dbms·framework 공통.
- **FR-002**: code_table_insights 프롬프트(dbms·framework)는 **주입된 컬럼 목록을 기준으로, 코드에서 실제 쓰는 컬럼을 빠짐없이 서술**하도록 지시한다(struct 멤버↔컬럼 매핑 포함). "추측 컬럼 금지"는 유지하되 "주입된 목록 내"로 한정.
- **FR-003**: 함수의 테이블 귀속(AFFECTS_TABLE·table_insights table)은 **그 함수의 결정론 `reads ∪ writes` 집합으로 제한**한다. 집합 밖 테이블(특히 callee 함수명)은 드롭한다.
- **FR-004**: code_rules_examples 프롬프트(dbms·framework)는 **callee(하위호출)는 테이블이 아니며, AFFECTS_TABLE는 이 함수가 직접 R/W 하는 테이블만**임을 명시한다.
- **FR-005**: 집합 밖 테이블 귀속 드롭은 **항상 표면화**한다(조용한 유실/오염 금지, Constitution IV) — 단 심각도를 사실에 맞춘다. ① 허용집합(reads∪writes)이 **비어 있어** 전부 드롭되는 경우(=직접 R/W 없는 디스패처, bottom-up 설계상 callee 테이블이 컨텍스트에 노출돼 생기는 **예상된 정상 동작**)는 per-함수로는 **INFO**(analyzer.log, 추적용) → 사고 추적 전용 error.log에 함수마다 쌓이는 소음 제거. ② 허용집합이 **비어 있지 않은데** 그 밖 테이블이 드롭되는 경우(=진짜 오귀속, 점검 가치 있음)는 발생 즉시 **WARNING**(error.log). 분기 기준 = `allowed` 집합의 공백 여부 단 하나.
- **FR-005a**: ①(디스패처 예상 드롭)은 per-함수 소음을 내지 않되 **누락도 되지 않게**, run(step6) 종료 시 **집계 1줄을 error.log에 표면화**한다("디스패처 callee-테이블 드롭 N함수 M건, 예상 동작"). = 표면화는 하되(1줄, 사고로그에 보임) per-함수 노이즈는 0. 집계는 step6 시작 reset / 종료 flush 로 run-scoped(analyzer 단일 run).
- **FR-006**: 분류/매칭 알고리즘(call_classifier·fqn·upsert)·Neo4j 스키마는 **변경하지 않는다**.
- **FR-007**: 프롬프트 변경은 KV-cache(Constitution V)를 위반하지 않는다 — per-함수 가변부(컬럼 목록)는 user_values 채널, 시스템 본문 지시 변경은 1회성 캐시 리셋만 허용.

### Key Entities
- **테이블 컨텍스트(table_descriptions 채널)**: fqn→"DDL설명 | 컬럼목록 | 샘플". step6 렌더러(`_fmt_tables`/`_format_table_refs`)가 reads/writes만 골라 LLM에 보임.
- **결정론 R/W 집합**: `ctx.reads`·`ctx.writes`(step4 SQL_KEY 해소 결과) — 테이블 귀속의 권위 출처.

## Success Criteria *(mandatory)*

- **SC-001**: 재분석 후 COLUMN.analyzed_description 빈값(코드에서 쓰이는 컬럼 중) **유의미 감소**(이전 10건 기준).
- **SC-002**: 재분석 후 **가짜 AFFECTS_TABLE(실테이블 아님) = 0**, `analyzed_desc_unmatched` 경고 건수 유의미 감소.
- **SC-003**: reads/writes 빈 디스패처 함수의 AFFECTS_TABLE = 0.
- **SC-004**: 분류기/baseline 회귀 0.
- **SC-005**: 재분석 1회로 ① 컬럼 채움 ② 가짜 테이블 0 동시 확인.
- **SC-006**: 재분석 후 error.log 의 per-함수 `table_attr_off_rw` = **허용집합 비지 않은 진짜 오귀속만**(디스패처 예상 드롭은 analyzer.log INFO 로만, per-함수 error.log엔 0건). 데이터·드롭 판정 불변.
- **SC-007**: 디스패처 드롭이 1건 이상인 run 은 error.log 에 **집계 1줄**(`table_attr_off_rw_dispatcher`, N함수 M건)이 정확히 한 번 표면화된다(per-함수 N줄 아님). 드롭 0인 run 은 그 줄도 없음.

## Assumptions
- 컬럼의 권위 목록 = DDL 파싱 결과(state["columns"], COLUMN 노드). 라이브 datasource 없어도 DDL 컬럼은 존재.
- 데이터·DDL 구조는 정비됨(사용자). 한 run = 전체 재빌드.
- spec 025 WIP(enrich/table·column nodes)와 코드 파일 비충돌 — 본 작업은 컨텍스트 빌더·step6·프롬프트만.
