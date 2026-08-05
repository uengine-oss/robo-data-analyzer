# Cross-Service Contract: 테이블 발견 (analyzer ↔ catalog ↔ fabric)

**소유**: 이 문서 (양 저장소 spec이 참조 — analyzer specs/107, catalog specs/discovery-endpoint)
**버전**: 1 | **일자**: 2026-07-30

## 경로·메서드

```
GET {catalog_base}/tables/discovery?datasource=<name>&sample_limit=5&max_tables=1000
```

- `datasource` 필수. `sample_limit` 1~50 (기본 5). `max_tables` 1~10000 (기본 1000).
- catalog 내부: fabric REST 프록시 —
  `GET {fabric}/datasources/{ds}/tables` → 테이블마다 `…/schema` + `…/sample?limit=` 병렬
  (동시성 상한 semaphore, 테이블 단위 부분실패 허용).

## 응답 (200)

```json
{
  "datasource": "prod_main",
  "tables": [
    {
      "name": "ORDERS",              // 실DB 테이블명 (그대로 — 변형 금지)
      "schema": "PUBLIC",            // 없으면 ""
      "columns": [                    // schema 조회 실패 시 [] + degraded=true
        {"name": "ORDER_ID", "dtype": "NUMBER", "nullable": false}
      ],
      "sample_rows": [{"ORDER_ID": 1}],  // sample 조회 실패 시 [] + degraded=true
      "degraded": false               // 이 테이블의 부분실패 여부 (조용한 누락 금지)
    }
  ],
  "truncated": 0                      // max_tables 초과로 잘린 건수 (0이 아니면 소비자가 경고)
}
```

## 오류

| 상황 | 응답 | analyzer 행동 |
|---|---|---|
| datasource 미등록 (fabric 404) | 404 | log_warning + 스트림 메시지, DDL 단어장으로 계속 |
| fabric 불달/전체 실패 | 502 | raise (catalog 활성 상태의 실패 = 심각, 헌법 IV) |
| 파라미터 오류 | 422 | raise (호출측 결함) |

## analyzer 소비 계약

- 호출 시점: parse_ddl 이후, 링킹 이전 (load_ast와 병렬 superstep 허용 — `tables` 키는 이 step만 씀).
- 가드 1벌: `ROBO_CATALOG_ENABLED` + `codebase.datasource` — 이 step 밖에서 데이터소스 HTTP 접점 금지.
- 병합: 대소문자 무시 정확 일치. 일치 → DDL Table 유지 + 샘플만 보강. 불일치 → 신규
  `Table(db=<datasource>, schema=응답 schema, name=응답 name, description_source="catalog")`
  + Column + HAS_COLUMN 업서트. Schema 노드/BELONGS_TO는 생성하지 않음(합의된 비범위).
- `table_sample_data[Table.id] = {"columns": [...], "sample_rows": [...]}` — step7 형태 불변.
- fuzzy 매칭 없음 — 구 sample-context의 rapidfuzz(≥85) 이름 흡수는 이 경로에 없다.
  (discovery가 실DB 이름을 직접 공급 → 단어장이 곧 진실. 알려진 트레이드오프.)

## 하위 호환

- catalog `POST /tables/sample-context` 유지 (analyzer 외 소비자 가능성) — analyzer 참조만 제거.
- fabric 무변경.
