# Implementation Plan: 용어사전 품질 근본재설계

**Branch**: `main` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/043-glossary-quality-redesign/spec.md`

## Summary

용어사전이 검색 다리 역할을 못 하는 실측 결함 4개를 근본 수정한다: ① 분할1 프롬프트의
domain_terms 표제어 자격을 "함수 텍스트(주석 포함) 실재 토큰"으로 개정(폭주 방어 문구는
유지) ② domain_terms 를 코드 노드 속성으로 저장 ③ build_glossary_step 에 AI 종합
단계(용어당 1콜 병렬 — 뜻 근거주의 종합 + 표기 변종 지목) 추가 ④ 파이프라인에서
build_glossary_step 을 모듈요약 뒤로 이동. 범용성 제약: 형태·토큰 겨냥 규칙 금지 —
판단은 AI, 기계 작업(수집·합집합·저장)만 결정론.

## Technical Context

**Language/Version**: Python 3.12 (analyzer 기존)

**Primary Dependencies**: LangGraph StateGraph(기존 파이프라인), `run_yaml_rule`/`gather_with_warmup`(기존 LLM 러너), Neo4j(neo4j_client)

**Storage**: Neo4j — 코드 노드 속성 `domain_terms`(신규 저장), `:GLOSSARY {terms_json}`(스키마 호환 유지)

**Testing**: 전량 재분석 실측(판정 4종: 폭주0·변종통합·소음·게이트) + prompt_hashes 갱신 검증(`check_byte_identical.py`)

**Target Platform**: 기존 analyzer 서버(5502) 파이프라인 내부 — 계약(입출력·API) 무변경

**Project Type**: 기존 서비스 내부 개선 (신규 서비스 없음)

**Performance Goals**: 종합 = 용어당 1콜 병렬(SKT 107·쇼핑몰 463) — 분석 본체(549콜) 대비 오차 수준. 용어당 문맥 ~800토큰 실측

**Constraints**: 폭주 회귀 0(SC-002) / 측정무결성(FR-008: 평가 코퍼스 용어 프롬프트 금지) / 형태규칙 금지(FR-007) / GLOSSARY 스키마 호환(FR-005)

**Scale/Scope**: 파일 6개 수정 + 프롬프트 rule 1개 신설 (아래 구조)

## Constitution Check

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. Determinism-First | ✅ | LLM 은 의미 작업(뜻 종합·변종 판단)에만. 수집·매칭·합집합·저장은 결정론 유지. 형태 정규식으로 "판단"을 흉내내는 것이 오히려 위반이었음(해피패스 제거와 정합) |
| II. Zero-Branch Strategy | ✅ | 순서 변경은 `analysis_pipelines.py` 의 Strategy 선언 튜플 재배열만. step 내부에 strategy 분기 0 |
| III. Single Source of Truth | ✅ | 신규 env 없음. 상한(문맥 노드 수)은 step 상수 1곳 |
| IV. No Silent Failure | ✅ | 종합 콜 실패 = log + 해당 용어 원본 유지(사전 전체를 죽이지 않음 — 모듈요약과 동일한 fail 정책). 문맥 컷 발생 시 로그(조용한 절단 금지) |
| V. KV-Cache-Friendly | ✅ | code_summary.yaml 개정 → 3분할 `extends` 구조 유지, `prompt_hashes.json` 재고정 + `check_byte_identical.py` 통과 필수. 종합 콜은 "전체 용어 목록"을 공유 접두로 배치 + `gather_with_warmup` 워밍 |
| VI. Schema Restraint | ✅ | 종합 출력 = 평탄 문자열 3필드(meaning / aliases 쉼표문자열 / evidence). 중첩·자유키 dict 금지(폭주 이력 준수) |
| VII. Submit-Tool Wrap | ✅ | `run_yaml_rule` + response_model 기존 경로 재사용(신규 패턴 없음) |
| VIII. Cohesion | ✅ | 종합 로직은 build_glossary_step 한 파일에 응집(수집→문맥조립→AI→합집합→저장이 순서 의존 한 단위) |
| Cross-Service Contracts | ✅ 내부 | 노드 속성 1개 추가(하위호환 — 소비처는 있으면 읽음)·GLOSSARY terms_json 스키마 동일 → 타 서비스 영향 0 |
| Cost & Safety | ✅ | 종합 콜도 cost_budget 회로차단기 경유(run_yaml_rule budget 인자) |

## Project Structure

### Documentation (this feature)

```text
specs/043-glossary-quality-redesign/
├── spec.md              # 완료
├── plan.md              # 이 파일
└── tasks.md             # /speckit-tasks 출력
```

(research.md·data-model.md·contracts/ 생략 — 근거는 spec 배경(전부 이 세션 실측)에 인라인,
데이터 변경은 아래 §데이터 2건뿐, 계약 변경 없음)

### Source Code (repository root)

```text
prompts/
├── framework/code_summary.yaml        # [5] domain_terms 규칙 개정 (FR-001·002·008)
├── dbms/code_summary_batch.yaml       # 동일 개정 (양 전략 동형)
├── framework/glossary_synthesis.yaml  # ★신설 — 종합 rule (system=규칙, user=용어목록(접두)+문맥)
└── dbms/glossary_synthesis.yaml       # ★신설 — 동일 본문
data_models/
├── graph_nodes.py                     # CodeNode.domain_terms 주석 갱신(저장됨)
└── code_analysis_output.py            # ★GlossarySynthesisOutput (meaning/aliases/evidence — 평탄 str)
shared/neo4j/nodes/code_node.py        # to_summary_item 에 domain_terms 추가 (FR-003)
analysis_flow/step7_post_processing/
└── build_glossary_step.py             # ★재설계 — 수집(현행)+문맥조립+AI종합+합집합+저장 (FR-004·005)
analysis_pipelines.py                  # build_glossary 를 병렬그룹에서 빼 모듈요약 뒤로 (FR-006)
cli_scripts/prompt_hashes.json         # 재고정 (V 원칙)
```

**Structure Decision**: 기존 배치 그대로 — 신규 파일은 프롬프트 rule 2개(전략별)와 출력
모델 1개뿐. 종합 로직은 build_glossary_step.py 안에 응집(원칙 VIII).

## 설계 상세 (구현 계약)

### A. 프롬프트 개정 — code_summary [5] domain_terms (FR-001·002)

- 표제어 자격 문구 교체: "본문에서 그대로 복사해 붙일 수 있는 식별자뿐(6종 열거)" →
  **"이 함수 텍스트(코드+주석)에 그 글자 그대로 실재하는 토큰"**. Ctrl+F 판정법·조합 창작
  금지·"다 훑으면 끝" 문구는 **그대로 유지**(폭주 방어).
- 뜻 규칙 추가: **근거주의** — "뜻은 이 함수 텍스트에 실제로 적힌 표현(주석 포함)에서만.
  주석은 용어의 도메인 의미에 대한 1차 근거다(동작 서술이 코드와 어긋날 때만 결정론 매핑
  우선 — §0.2 는 동작 서술로 한정). 정체(무슨 약자·무슨 기관)가 텍스트에 없으면 역할만
  서술하고 정체를 창작하지 마라."
- §0.2 문구 정밀화: "본문 주석은 무시" → "본문 주석의 **동작 서술**(신규/갱신 류)이 결정론
  매핑과 어긋나면 결정론 우선. 도메인 용어·의미 설명으로서의 주석은 근거로 참고."
- 예시: 무관 도메인(선박·의료) 유지, 평가 코퍼스 용어 0 (FR-008).
- 양 전략(framework·dbms) 동형 반영 → `prompt_hashes.json` 재고정.

### B. 조각 저장 (FR-003)

`to_summary_item` 에 `"domain_terms": fn.domain_terms` 추가(빈 값은 nonempty 가 걸러 하위호환).
화면 비노출: 코드 노드는 FallbackCard 라우팅에 도달하지 않음이 spec 041 에서 증명됨 — 추가
프론트 작업 0. (grep 검증: search_text 선례와 동일 경로)

### C. build_glossary_step 재설계 (FR-004·005)

```
1 수집(현행 _merge_terms 유지) → 표제어별 {canonical, meanings[], src_node_ids[]}
   (src_node_ids 신규 — 어느 노드가 그 조각을 냈는지, 수집 중 기록. 역추적 검색 불필요)
2 문맥 조립(결정론):
   - 공유 접두 = 전체 표제어 이름 목록(이름만, 한 줄씩) — 모든 콜의 user 앞부분에 동일 배치
   - 용어별 = src 노드들의 summary·search_text(상한 N=6, 초과 시 로그) + 그 노드들의 모듈
     summary(state["modules"] + summarize_module 의 _match_code_to_modules 재사용)
3 AI 종합(용어당 1콜, gather_with_warmup + llm_semaphore, fail=원본 유지·log):
   출력 GlossarySynthesisOutput { meaning: str, aliases: str(쉼표구분, 없으면 ""), evidence: str }
   수용 규칙(결정론): evidence 가 입력 문맥의 실제 문장 부분열이 아니면 종합 기각(원본 유지)
                     aliases 항목이 전체 표제어 목록에 없으면 그 항목만 무시
4 그룹 확정(결정론 union-find): aliases 지목을 합집합 → 대표=최다 표본 canonical,
   나머지=colloquial 로. meanings 는 대표 종합 뜻 우선, 부족분 " / " 병기(현행 규칙 유지)
5 저장: 기존 _UPSERT_QUERY 그대로(terms_json 스키마 호환 — FR-005)
```

### D. 파이프라인 순서 (FR-006)

Framework: 병렬그룹 `(summarize_module, enrich_table, build_components, health_metrics,
build_glossary)` → `(summarize_module, enrich_table, build_components, health_metrics)` 후
`build_glossary_step` 단독 → `embed_step`. DBMS: 이미 순차라 위치만 summarize_module 뒤
유지 확인. state 계약: build_glossary 가 `state["modules"]`(갱신본) 읽음 — 순차化로 보장.

## 검증 계획 (판정 4종 — spec SC 매핑)

1. SKT 전량 재분석(`test` DB) → SC-002(폭주 0)·SC-001(변종 통합·뜻 전수 대조)·SC-004(소음)
2. 쇼핑몰 전량 재분석(`neo4j` DB) → 동일 판정
3. 놀이터 게이트(검색기 수정 0, 해피패스 0 상태): SKT ≥6/7·쇼핑몰 ≥9/11 (SC-003)
4. SC-005: 저장된 조각만으로 종합 재실행 스모크(놀이터에서 조각 조회→동일 스키마 확인)

## Complexity Tracking

위반 없음 — 표 생략.
