# Implementation Plan: 데이터소스 테이블 발견 + 샘플 수집 통합

**Branch**: `main` (커밋은 사용자 지시 시) | **Date**: 2026-07-30 | **Spec**: [spec.md](./spec.md)

## Summary

단어장 출처를 DDL 단일 → DDL ∪ 실DB(discovery)로 이원화하고, 데이터소스 HTTP 접점을 수집
step 한 곳으로 통합한다. catalog에 fabric 프록시 엔드포인트 1개 신설(Cross-Service),
analyzer의 step6 샘플 step은 흡수·제거. 그래프 스키마·step7 소비 계약 불변.

## Technical Context

Python 3.12 / httpx(analyzer)·aiohttp+FastAPI(catalog) / Neo4j 스키마 불변 / pytest.
신규 외부 의존 0. 성능: catalog 내부 semaphore 동시성(테이블 단위), analyzer는 HTTP 1회.

## Research (Phase 0 — 결정 기록)

- **D1 catalog 프록시 vs analyzer→fabric 직결**: 직결은 analyzer 서비스 경계(헌법
  Cross-Service: Catalog뿐) 신설 — 기각. catalog가 fabric REST 3종(이미 존재)을 프록시.
- **D2 sample-context 재사용 불가**: 그 API는 "이름을 주면 해소"라 발견(열거)이 안 되고,
  테이블 지식이 analyzer가 쓴 그래프의 되읽기(순환)임을 코드로 확인 — 신규 엔드포인트 필요.
- **D3 배치 위치**: parse_ddl 직후 `(load_ast, load_external, discover_datasource)` 병렬
  superstep. `tables`/`table_sample_data` 키는 이 step만 쓰므로 병렬 안전. 링킹 전 완료 보장.
- **D4 스텝 홈**: `pipeline/steps/step1_parse_ddl/discover_datasource_step.py` — 단어장 구축과
  같은 이유로 변경되는 소유자 (헌법 VIII).
- **D5 fuzzy 제거**: discovery가 실DB 이름을 직접 공급하므로 이름 편차 흡수(fuzzy)의 존재
  이유 소멸. 병합은 대소문자 무시 정확 일치만 — 계약 문서에 한계 명시.
- **D6 부분실패 = degraded 플래그**: 테이블 단위 실패를 조용히 빼지 않고 응답에 표시 +
  양쪽 로그 (헌법 IV).

## Data Model (Phase 1)

계약·응답 스키마·병합 규칙: [contracts/discovery-contract.md](./contracts/discovery-contract.md)
(단일 진실 — 여기 중복 기술하지 않음). Table/Column/HAS_COLUMn·table_sample_data 형태 불변.

## Constitution Check

| 조항 | 판정 | 근거 |
|---|---|---|
| I 결정론 | ✅ | HTTP 수집 + 결정론 병합. LLM 0 |
| II 분기 0 | ✅ | 두 전략 같은 step 선언 삽입. step 내부 전략 분기 없음 |
| III 단일 진실 | ✅ | 데이터소스 접점·가드 1곳으로 통합 (기존 2곳 → 1곳) |
| IV silent fail 금지 | ✅ 개선 | DDL 0건 침묵 진행의 실구멍을 표면화·보강. 404/절단/부분실패 전부 로그·플래그 |
| V~VII | N/A | 프롬프트·스키마·에이전트 무변경 |
| VIII 소유권 | ✅ | 단어장 구축 소유자(step1) 아래 배치. catalog 쪽은 samples/ 소유 |
| Cross-Service | ⚠️ 신설 | catalog `GET /tables/discovery` — 계약 문서 + catalog 저장소 spec 동반 |

## 배선 (현재 → 목표)

```
현재: parse_ddl → tables ──────────────────────────────→ step5 링킹
                                    step6 fetch_table_samples → table_sample_data → step7
목표: parse_ddl → tables ┐
      (load_ast ∥ load_external ∥ discover_datasource)   ← discovery: tables 병합+업서트
                         └ table_sample_data 동시 생산 ──→ step7 (무변경)
      step6 fetch_table_samples = 삭제 (배선·파일·테스트·참조 0)
```

영향 소비자 전수: step5 링커(tables — 병합 후 동일 타입), step7(table_sample_data — 형태 불변),
Neo4j upsert(기존 upsert_tables/columns/has_column 재사용), strategies.py 두 전략,
tests/pipeline/step6 관련 테스트(제거 대상), catalog api/samples(신규 라우트).

## Source Code

```
analyzer:
  integrations/catalog.py                     [수정] get_table_discovery() 추가
  pipeline/steps/step1_parse_ddl/discover_datasource_step.py  [신설]
  pipeline/strategies.py                      [수정] 두 전략 배선 교체 (step6 샘플 제거 포함)
  pipeline/steps/step6_build_context/fetch_table_samples_step.py  [삭제]
  tests/pipeline/step1_parse_ddl/test_discover_datasource_step.py [신설]
catalog:
  integrations/data_fabric.py                 [수정] REST 프록시 메서드(list/schema) 추가
  samples/discovery.py                        [신설] 발견 서비스 (semaphore·부분실패)
  api/table_samples.py                        [수정] GET /tables/discovery 라우트
  specs/00X-discovery-endpoint/               [신설] catalog 측 spec (계약 문서 참조)
  tests/…                                     [신설] 계약 테스트 (fabric 모킹)
```

## 마이그레이션/롤백

상태·그래프 스키마 불변, run은 전량 재구축 — 마이그레이션 없음. 롤백 = 양 저장소 revert.

## Complexity Tracking

Cross-Service 신설 1건이 유일한 복잡도 — D1에서 경계 유지 대안(직결)을 검토·기각한 근거 기록.
