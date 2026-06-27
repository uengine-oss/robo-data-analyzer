# Research: stereotype 라벨 재부여 — 라벨순서 근거 & 032 철회사유 무력화

## 결정: stereotype을 라벨로 부여하되 소비는 위치기반

### 근거 1 — `labels[0]`은 항상 antlr 타입 (실증 + 코드 추적)

- **코드 추적**(전수):
  - `analysis_flow/step2_load_ast/load_ast_step.py:99-100` — `upsert_code_nodes` + `upsert_modules` 가 **모든 노드를 antlr 타입 라벨로 MERGE**(가장 먼저 라벨 토큰 등록).
  - `analysis_flow/step6_analyze_code/analyze_code_step.py:380` — `update_summaries(neo, [fn])` 가 함수 1건 완료 즉시 호출. d61f240은 여기서 stereotype **라벨** 부여.
  - `analysis_flow/step7_post_processing/summarize_module_step.py:107` — `upsert_modules(neo, updated)` 재호출(stereotype 채워진 모듈). d61f240은 `upsert_modules` 내부에서 stereotype 라벨 부여.
- → 타입 라벨(step2)이 stereotype 라벨(step6/7)보다 **먼저** 등록. Neo4j `labels()`는 라벨 토큰 생성순(처음 등록순) 반환 → `labels[0]`=타입.
- **실증**: d61f240 마이그레이션 검증 시 전부 `[FUNCTION,Command]`·`[FILE,Service]`(타입 먼저). 매 run wipe해도 토큰 id 유지.

### 근거 2 — 032 철회사유가 위치기반으로 해소됨

032 철회 근거 = "멀티레이블 소비처가 종류/역할을 분류하려면 (a) 하드코딩 역할집합(드리프트) 또는 (b) Neo4j 라벨순서 의존(비보장)이 필요" → 둘 다 부담.

- (a) 하드코딩 집합 → **불필요**. 위치(`labels[0]` vs `labels[1:]`)로 구분 = 드리프트 0.
- (b) 라벨순서 의존 → 이 파이프라인은 타입 먼저 등록이 **구조적으로 보장**(step 순서). "일반 Neo4j는 비보장"이 맞지만 이 생산 파이프라인 한정 안전.
- → 철회 사유의 전제(분류 부담)가 사라짐. 라벨의 장점(필터/색상 일급, `MATCH (:Command)`)만 남음.

### 대안 검토

- **속성 유지(032 revert 결론)**: 라벨=종류 단일로 깨끗하나, 역할 필터/색상이 속성 조회로만 가능(일급 아님). 사용자 최종결정은 라벨화. → 기각(단 무중단 위해 속성 **병행**).
- **하드코딩 stereotype 집합으로 분류**: 드리프트 발생(ontology_roles.yaml 변경 시 프론트 누락). → 기각(위치기반이 우월).

## 미해결/리스크

- Neo4j 라벨순서 "계약" 비보장 — 본 파이프라인은 안 깨지나, 만약 향후 step 순서가 바뀌어 stereotype이 타입보다 먼저 등록되면 `labels[0]` 가정 위반. 완화: step2(적재)→step6/7(분석) 순서는 DAG 근본 구조라 변동 가능성 낮음. 최악도 패널 표시 1회 swap(데이터/로직 무손상).
