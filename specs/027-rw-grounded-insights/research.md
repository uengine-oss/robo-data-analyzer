# Phase 0 Research: 027-rw-grounded-insights

## R1. 컨텍스트 현황 (확정)
- per-함수 컨텍스트(`build_context_step`): `reads`/`writes`=테이블 fqn 튜플, `callees`=호출 함수 id 튜플. **컬럼 없음.**
- 테이블 채널 `_build_table_contexts`: `{fqn: "DDL설명 | 샘플(N행)"}` — 컬럼명은 샘플행 키로만(라이브 datasource 필요). 이번 run=샘플 0 → 컬럼 0.
- 렌더러 `_fmt_tables`(batch)/`_format_table_refs`(three_split): reads/writes 의 fqn→table_descriptions[fqn] 만 보임.

## R2. 결함 메커니즘 (라이브 확정)
- ① cust_num 등은 코드에서 DB INSERT 멤버로 쓰이나(소스 확인), 컬럼 목록 미주입 → LLM 코드추출 누락 → analyzed_description 빈값. fqn(table,col)==노드 id 라 생성됐으면 매칭됐을 것.
- ② c300(reads=[]·writes=[]) 컨텍스트에 `callees=[…e800_dbio_rltm_auth_hst_u0004…]` → LLM 이 callee 함수명을 테이블로 → 가짜 AFFECTS_TABLE.

## R3. 제약 적용 지점
- affects_table/table_insights 는 per-함수 `em`(ExampleMappingResult) 에 누적 → state → MATCH 기반 upsert(가짜는 자연 무매치지만 데이터엔 남고 컬럼설명 유실 유발). **per-함수 조립부(three_split/batch, ctx 가용)에서 reads∪writes 밖 드롭+경고** 가 최소·정확.

## R4. KV-cache
- 컬럼 목록은 table_descriptions(=user_values 가변부) → per-call 이미 비캐시. 시스템 본문 지시 변경은 rule 당 1회 리셋(허용). 공유 prefix(codebase_notes) 바이트동일 유지.

## R5. 컬럼 권위 출처
- `state["columns"]`(DDL 파싱, COLUMN 노드) → id="table.col" → `{table_fqn: [name]}`. 라이브 datasource 무관하게 존재.
