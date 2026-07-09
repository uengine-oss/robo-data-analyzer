---
description: "Task list — code_summary 프롬프트 환각·쓰기누락 방지 강화"
---

# Tasks: code_summary 프롬프트 환각·쓰기누락 방지 강화

**Input**: `/specs/029-summary-prompt-hardening/` (plan·spec·research·data-model·quickstart)

**Tests**: 테스트 코드는 없음 — 검증은 실데이터 재분석 + 임베딩 검색 측정(quickstart). 단위 test 불요.

**Organization**: user story별 그룹. US1(환각방지 P1)·US2(쓰기명시 P2)는 탐색적으로 적용됨 → 정식 점검. US3(의미어·입출력·범용 P3)의 일부가 잔여 구현.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: 변경 대상 프롬프트·hash 현 상태 확인

- [ ] T001 변경 대상 4개 프롬프트 + hash 현 상태 점검 — `prompts/dbms/_common.yaml`, `prompts/dbms/code_summary_batch.yaml`, `prompts/framework/_common.yaml`, `prompts/framework/code_summary.yaml`, `cli_scripts/prompt_hashes.json` (탐색 적용분 + 잔여 식별)

---

## Phase 2: Foundational

**Purpose**: 없음 — 각 전략 프롬프트는 독립 파일이라 차단 선행작업 없음.

---

## Phase 3: User Story 1 — 환각 방지: 하위 [핵심] 충실 계승 (Priority: P1) 🎯 MVP

**Goal**: 부모(호출 함수) summary가 하위(자식 구문/하위 함수) [핵심]의 처리 축을 계승, 다른 개념 치환=환각 금지.
**Independent Test**: 하위가 "광역 합계용 태그별"로 정확히 요약된 부모 MERGE 재분석 → 부모 [핵심]이 "광역 합계" 계승(유역수종 환각 아님).

- [ ] T002 [US1] dbms `prompts/dbms/_common.yaml` 단계4에 "자식 집계축 충실 계승·다른 축 치환=환각 금지" 정식 점검(탐색 적용됨)
- [ ] T003 [P] [US1] framework `prompts/framework/_common.yaml` 단계4(a)에 "하위 함수(callee) 처리축 계승·환각 금지" 정식 점검(탐색 적용됨)
- [ ] T004 [US1] dbms `prompts/dbms/code_summary_batch.yaml` [2] + framework `prompts/framework/code_summary.yaml` [2.3]에 "주어진 컨텍스트(하위 [핵심]·R/W·심볼·이름) 무시·치환·누락 금지, 충돌 시 코드·하위 [핵심] 우선" 정식 점검(탐색 적용됨)

---

## Phase 4: User Story 2 — 쓰기 노드 쓰기 동작·대상 명시 (Priority: P2)

**Goal**: writes 있는 INSERT/UPDATE/MERGE/DELETE의 [핵심]에 쓰기 동작·대상이 드러남(SELECT 절에 끌려 누락 금지).
**Independent Test**: `INSERT INTO LOG_TB ... SELECT` 노드 재분석 → [핵심]에 "로그 테이블에 기록" 포함(읽기로 끝나지 않음).

- [ ] T005 [US2] dbms `prompts/dbms/code_summary_batch.yaml` [2.1](쓰기노드 쓰기동작 필수) + [1.5](logical_name 쓰기) 정식 점검(탐색 적용됨)
- [ ] T006 [P] [US2] framework `prompts/framework/code_summary.yaml` [1.2.4]·[2.2](R/W·쓰기 무엇을 바꾸는지) 커버 확인 — framework는 함수 단위라 dbms 구문 쓰기노드와 대응 메커니즘 점검

---

## Phase 5: User Story 3 — 대상 의미어 + 입력→출력 종합 + 범용 예시 (Priority: P3)

**Goal**: 쓰기 대상도 테이블명 X·의미어로, 입력 테이블→출력 테이블 종합, 예시 범용.
**Independent Test**: 재분석 summary에 코드 테이블명(RDD01DD_TB) 노출 0 + 입력→출력 한 문장 종합. 예시에 도메인 고유명사 0.

- [ ] T007 [US3] **(잔여 구현)** dbms `prompts/dbms/code_summary_batch.yaml` [2.1]에 "쓰기 **대상도 테이블명 그대로 X, 의미어로**"(예: RDD01DD_TB → 일보) 명확화
- [ ] T008 [US3] **(잔여 구현)** dbms `prompts/dbms/code_summary_batch.yaml` [2]에 "자식 SELECT가 **어느 입력 테이블에서 읽어 → 부모가 어느 대상에 쓰는지** 한 문장 종합" 추가
- [ ] T009 [P] [US3] **(잔여 구현)** framework `prompts/framework/code_summary.yaml` [2.3]에 동일(대상 의미어 + 입력→출력 종합, 용어=하위 함수) 추가
- [ ] T010 [US3] 예시 범용화(K-water "유역·일보" → "대상 테이블·집계 테이블") 정식 점검(탐색 적용됨)

---

## Phase 6: Polish & Cross-Cutting (검증·정합)

**Purpose**: KV-cache 정합 + 실데이터 검증(Success Criteria)

- [ ] T011 **(헌법 V 필수)** `cli_scripts/prompt_hashes.json` — `_common` 등 변경분 SHA256 재핀(CI `byte_identical.yml` 통과)
- [ ] T012 재분석 `ROBO_NEO4J_DATABASE=test .venv/Scripts/python.exe cli_scripts/analyze_data_dir.py RWIS --strategy dbms` → 권역(MERGE:129) "광역 합계" 회복 + 쓰기노드 [핵심] 쓰기동작 누락 0 검증 (SC-001·002)
- [ ] T013 범용 예시로도 권역 회복 동일 확인 (SC-003) — 이미 1회 검증, 정정 후 재확인
- [ ] T014 임베딩 검색 요약질의 10/10 검증 — `module_test/임베딩서칭` `SUMMARY=1 JUDGE_TOPK=7 judge_eval.py` (SC-004)
- [ ] T015 CI `byte_identical.yml`(prompt_hashes 정합) 통과 확인

---

## Dependencies

- **US1·US2·US3 상호 독립**(서로 다른 지침 영역) — 병렬 가능. T002·T003·T006·T009는 [P](다른 파일).
- **Polish(T011~T015)는 US1·2·3 완료 후**(프롬프트 확정돼야 hash 재핀·재분석 의미 있음).
- T012(재분석)는 T011(hash) 이후 권장(캐시 정합).

## Implementation Strategy

- **MVP = US1**(환각 방지) — 권역 "광역 합계" 회복이 다운스트림 정확도의 핵심.
- 증분: US1(환각0) → US2(쓰기명시) → US3(의미어·종합·범용) → Polish(hash·검증).
- ★잔여 실작업 = **T007·T008·T009**(대상 의미어 명확화·입력→출력 종합) + **T011**(hash 재핀) + **T012~T015**(검증). 나머지는 탐색 적용분 정식 점검.

## 진행 요약

| Phase | 태스크 | 상태 |
|---|---|---|
| US1 환각방지 | T002~T004 | 탐색 적용 → 점검 |
| US2 쓰기명시 | T005~T006 | 탐색 적용 → 점검 |
| US3 의미어·종합·범용 | T007~T010 | **T007·T008·T009 잔여 구현** / T010 점검 |
| Polish | T011~T015 | **T011 hash·T012~T015 검증 신규** |
