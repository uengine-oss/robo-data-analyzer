# Quickstart: 041-summary-search-split 검증 절차

## 전제

- Neo4j 로컬(bolt://127.0.0.1:7687) — `neo4j`(쇼핑몰 124노드)·`test`(SKT 38노드)
- 놀이터 venv: `module_test/임베딩서칭/.venv-play/Scripts/python.exe`, `$env:PYTHONIOENCODING="utf-8"`
- analyzer LLM: 공유 `.env` 설정(기존 그대로), 예산 회로차단기 기본값
- 데이터셋 위치·기동 레시피: 메모리 manual.md·shopmall-demo-testdata.md 정독 후 실행

## Phase A 검증 (필드 신설 — 코드만)

```powershell
# 1. 프롬프트 해시 장부 갱신 + 3분할 byte-identical 확인
python cli_scripts/check_byte_identical.py --update
python cli_scripts/check_byte_identical.py    # exit 0
# 2. 스키마·저장 심볼 전수 grep (잔재·누락 0)
#    SummaryOutput( / BatchSummaryItem( / search_text / to_summary_item
```

## Phase B 검증 (파일럿 → 게이트)

```powershell
# 1. 파일럿: SKT(test DB) 재분석 → search_text 산출을 원문 소스와 대조 (표본 전수 정독)
# 2. 위험질의 확정: cluster_gold.yaml·rewrite_gold.yaml·measure_log 대조 → id 5~8개
# 3. ablation (놀이터, DB=test):
$env:ROBO_EMBED_TEXT_MODE="template";    # 현행 기준선 (재분석 후 재측정)
.venv-play\Scripts\python.exe eval\cluster_eval.py --ids <위험질의ids>
$env:ROBO_EMBED_TEXT_MODE="searchfield"; # 신규 재료
.venv-play\Scripts\python.exe eval\cluster_eval.py --ids <위험질의ids>
# 판정: searchfield 정답 유실 0 (template 동등 이상) → 통과
# 4. 통과 시 쇼핑몰(neo4j DB) 재분석 → 같은 ablation 반복
# 5. 측정 후 ROBO_NEO4J_DATABASE=neo4j 원복 + measure_log.md 기록(진 결과 포함)
```

## Phase C 검증 (다이어트)

```powershell
# 1. 다이어트 프롬프트+validator 교체 후 SKT→쇼핑몰 재분석
# 2. 길이: 전 노드 summary 비어있지 않은 줄 ≤ 3 (Cypher로 전수 확인) + [핵심] 잔재 0
# 3. 품질: 표본 노드 원문 대조 — "무엇을 하는지" 파악 충분, 한 줄 달랑 0
# 4. 상향 분석: 부모 노드 summary·rules 표본을 다이어트 전 산출과 대조 (FR-010)
# 5. 위험질의 재게이트 (searchfield) — 유실 0 유지
# 6. 프론트: 그래프 화면에서 코드노드·구문노드 상세 열어 search_text 비노출 + 요약 표시 정상 확인
```

## 완료 판정

- [ ] SC-001/002: 위험질의 유실 0 (파일럿·전량 모두)
- [ ] SC-003: 전 노드 3줄 이내 + 표본 검수 통과
- [ ] SC-004: 프론트 비노출·표시 정상
- [ ] SC-005: 호출 횟수 +0 (usage.jsonl 대조)
- [ ] SC-007: 상향 분석 표본 무회귀
- [ ] measure_log.md에 전 측정 기록(패배 포함) + DB 원복(neo4j)
