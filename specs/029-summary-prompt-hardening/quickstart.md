# Quickstart: 검증 절차

프롬프트 강화가 환각·쓰기누락을 막고 검색 정확도로 이어지는지 end-to-end 검증한다.

## 전제

- analyzer venv: `d:/work/robo/project/robo-data-analyzer/.venv`
- 임베딩서칭 venv: `module_test/임베딩서칭/.venv-play`
- DB: dbms 분석은 `test` (메인 `.env`는 neo4j 보호 — 셸 환경변수 override; `load_dotenv(override=False)`라 셸 env 우선)
- RWIS 소스/AST: `../data/source/RWIS`, `../data/analysis/RWIS`

## 1. 프롬프트 변경 후 hash 재핀 (KV-cache 정합)

```powershell
# _common 변경 시 SHA256 재핀 (CI byte_identical.yml 통과용)
cd d:/work/robo/project/robo-data-analyzer
# (프로젝트의 hash 재생성 스크립트/명령으로 prompt_hashes.json 갱신)
```

## 2. 재분석 (test db, 메인 .env 무수정)

```bash
cd d:/work/robo/project/robo-data-analyzer
ROBO_NEO4J_DATABASE=test .venv/Scripts/python.exe cli_scripts/analyze_data_dir.py RWIS --strategy dbms
# 로그에 "Neo4j 준비 │ DB: test" 확인
```

## 3. summary 검증 — 환각 0 / 쓰기누락 0 (SC-001·002)

```bash
cd module_test/임베딩서칭
# 권역(MERGE:129) summary 가 "광역 합계"인가 (유역수종 환각 아님)
# 쓰기노드(INSERT/MERGE/UPDATE) [핵심] 에 쓰기 동작어(반영/기록/갱신/삽입) 포함률 100%
./.venv-play/Scripts/python.exe -c "..."  # _common get_driver 로 노드 summary 조회
```

**기대**:
- 권역 129 [핵심] = "광역 합계용 …일보에 반영" (≠ "유역별 수종")
- 쓰기 노드 [핵심] 쓰기 동작 누락 = 0건

## 4. 범용성 검증 (SC-003)

- 프롬프트 예시에서 도메인 고유명사(유역·일보) 제거된 상태로 2·3을 재실행 → 권역 회복이 **동일**하게 성립(예시 의존 아님).

## 5. 검색 정확도 검증 (SC-004)

```bash
cd module_test/임베딩서칭
rm -f data/.cache_corpus.npz data/.cache_judge.json
SUMMARY=1 JUDGE_TOPK=7 N_VOTES=3 ./.venv-play/Scripts/python.exe probes/judge_eval.py
# 요약질의(anchor summary 자동) 심판 완전적중
```

**기대**: 요약질의 완전적중 **10/10** (정정 전 8/10).

## 통과 기준

- [ ] 권역 summary "광역 합계"로 회복(환각 0)
- [ ] 쓰기 노드 [핵심] 쓰기동작 누락 0%
- [ ] 범용 예시로도 동일 회복
- [ ] 요약질의 검색 10/10
- [ ] CI `byte_identical.yml` 통과(prompt_hashes.json 갱신됨)
