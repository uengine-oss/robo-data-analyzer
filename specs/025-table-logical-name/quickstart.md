# Quickstart: 025 검증 (테이블 논리명 + 비즈니스 품질)

## 전제
- analyzer 루트 refactor, venv `.venv`, Neo4j(neo4j/an1021402), LLM frentis-ai-model. frontend dev(3000).
- ★024+025 코드 다 된 뒤 1회 재분석으로 함께 검증(공유 그래프 1회 변경).

## 1. 재분석 (024+025 묶음)
```
.venv\Scripts\python.exe cli_scripts\analyze_data_dir.py <대상> --strategy dbms
```

## 2. 검증 (그래프 직접쿼리 + 샘플 원문대조)
| 검증 | 기대 |
|---|---|
| 테이블 logical_name 채움(근거있는 것) | 대부분 보유, 빈 logical_name 저장 0 |
| 논리명 vs 물리명 | 다름·읽을 수 있는 도메인 표현(샘플) |
| 프론트 TableCard | 논리명 표시 + 물리명 병기(헤드리스 스크린샷 Read) |
| 요약 샘플 원문대조 | 값·코드(M/Q/S/D/P 등) 보존 100% / 없는내용 추가 0 |
| KV캐시 | 공통 system byte-identical(`check_byte_identical.py` 또는 wc -c) |

## 3. ★검색 회귀(별도 프로젝트, summary 변경분)
```
cd module_test/임베딩서칭
.venv-play\Scripts\python.exe eval/flow_eval.py        # 현장/요약 — 의미있는 하락 0 확인
```
요약 변경이 검색 정확도를 떨어뜨리면 → 문체 보강 재고(SC-005).

## 4. 스냅샷 diff (024+025 통합)
```
python scratchpad/graph_snapshot.py after
python scratchpad/graph_snapshot.py diff   # 의도변화(논리명·analyzed·id) + 회귀0
```

## 상세
- 설계: [research.md](research.md) · 엔티티: [data-model.md](data-model.md)
