# Phase 1 Data Model: 027-rw-grounded-insights

신규 Neo4j 노드/관계/스키마 **없음**. 출력 스키마 필드 추가 **없음**.

## 변경(런타임 컨텍스트만)
- 테이블 컨텍스트 문자열(`table_descriptions[fqn]`)에 **컬럼명 목록 텍스트** 추가: `"DDL설명 | 컬럼: c1, c2, … | 샘플(N행)…"`.
- per-함수 `affects_table`/`table_insights` 의 table 을 `ctx.reads∪writes` 로 **필터**(드롭). 출력 데이터의 노이즈 제거일 뿐, 모델 필드 변화 없음.

## 불변식
- AFFECTS_TABLE.table ∈ (그 함수의 reads ∪ writes). 위반 = 드롭+경고.
- 컬럼 권위 = DDL(state["columns"]). LLM 은 그 목록 내에서만 description 부여.
