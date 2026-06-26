# Phase 1 Quickstart / 검증 절차: 026-scan-contract-realign

## V1. 정합 전수 검증 (코드 ↔ 문서) — SC-003

```bash
cd robo-data-analyzer
# 옛 이름 잔재 0 이어야 함
grep -rn "framework_functions" prompts/ skills/ data_models/ analysis_flow/ shared/ --include="*.md" --include="*.py" | grep -v ".venv"
# 중간 이름 잔재 0 이어야 함
grep -rn "framework_code_nodes" prompts/ skills/ data_models/ analysis_flow/ shared/ --include="*.md" --include="*.py" | grep -v ".venv"
# 신규 이름이 코드+문서 양쪽에 존재해야 함
grep -rln "framework_runtime_functions" prompts/ skills/ data_models/ analysis_flow/ shared/ | grep -v ".venv"
```
합격: 앞의 둘 = 0건, 셋째 = 코드/문서 모두 포함.

## V2. 단위/회귀 — SC-005

```bash
.venv/Scripts/python.exe -m pytest -q        # 분류기 회귀 0
python cli_scripts/baseline_runner.py        # baseline 무변경(있으면)
```

## V3. 가드(FR-003) 합성 테스트 — SC-004

- 드롭목록에 프로젝트 code_nodes 이름을 섞은 합성 `scan_result` 주입 → `_warn_if_framework_overlaps_project` 가 경고 발생하는지 단위 테스트.
- 정상(겹침 0) 입력 → 경고 미발생.

## V4. 라이브 재현 — SC-001/002 (운영자 1회, ~4분, LLM+Neo4j 필요)

```bash
# 깨끗한 data/source/<proj> + data/ddl 준비 후
.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py --strategy framework
```
- `logs/analysis.run/latest/tool_events.jsonl` 의 `submit_scan` → `framework_runtime_functions` 에 **PFM_*/mpfm_*/PRINT_* 만**, 프로젝트 함수(a000_*/b000_*) **0건**.
- `analyzer.log` 의 `link_calls_by_name done` → internal 회복(이전 비정상 54 대비 증가), 프로젝트 함수가 드롭되지 않음.
- Neo4j: PFM_*/mpfm_* 가 노드/엣지로 남지 않음.
