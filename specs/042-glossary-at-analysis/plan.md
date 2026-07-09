# Plan: 042-glossary-at-analysis

**Spec**: [spec.md](spec.md) | **Date**: 2026-07-09

## 설계 (전수조사 확정 사실 기반)

- **수집** = 분할1 출력 필드 `domain_terms`(평탄 문자열 `"약어=뜻; 약어=뜻"`, 빈 값 허용 — 헌법 VI 스키마 절제) → CodeNode → 그래프 저장은 안 함(조각은 state 로만 흐름 — 노드 속성 오염 방지).
- **종합** = `step7_post_processing/build_glossary_step.py` 신설:
  - framework: 후처리 병렬 튜플(analysis_pipelines.py:102 — 4 step)에 5번째로 편입(입력 read-only·state 키 비충돌 유지). dbms: 순차 자리(build_components 옆).
  - 로직: 전 code_nodes 의 domain_terms 결정론 파싱 → canonical 병합. 뜻이 복수인 용어만 LLM 배치 1콜로 정리(같은 뜻 병합·다의어 나란히), 실패 시 나란히 보존 폴백(로그 — silent 금지).
  - 저장: `:GLOSSARY {id:"glossary", terms_json}` 단일 노드 MERGE (additive, 소비 Cypher 1개, run wipe 정책과 정합).
- **소비** = 놀이터 `glossary.build()`: 그래프 GLOSSARY 노드 우선 로드(결정론·LLM 0·colloquial 빈 배열 — 별칭은 질의 시점 3단 사다리 몫), 부재 시 기존 생성 폴백(옛 그래프 호환).
- **Constitution**: I 결정론(수집·파싱·저장 LLM 0, 종합만 1콜) / II 분기0(파이프라인 선언 2곳만) / V KV캐시(분할1 user_template_specific 만 수정) / VI 평탄 문자열 / 프론트 비노출(속성 미저장이라 원천 차단).

## Tasks

- [ ] T001 스키마: SummaryOutput·BatchSummaryItem 에 `domain_terms: str = ""` + CodeNode.domain_terms
- [ ] T002 fan-in 2곳(three_split·batch) — fn.domain_terms 적용
- [ ] T003 프롬프트 2곳 `[5] domain_terms` 지침(코드 실재 약어·시스템명·핵심 코드값만, 뜻=일반 업계 의미 우선+코드 역할, 없으면 빈 값) + 책임카드/마무리 5필드 + 해시 장부
- [ ] T004 build_glossary_step 신설 + 파이프라인 2곳 편입
- [ ] T005 놀이터 glossary.build 그래프 우선 로드(+폴백)
- [ ] T006 심볼 전수 grep + 컴파일
- [ ] T007 재분석(SKT→쇼핑몰) → GLOSSARY 노드 확인 → 양 DB 위험질의 게이트(searchfield+unified) — 동등 이상 + c200·d700 관찰
- [ ] T008 measure_log 기록 + 메모리 갱신
