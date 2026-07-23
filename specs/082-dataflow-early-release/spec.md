# 082 — step7 dataflow 조기 해제 + critical-path 우선 투입

작성: 2026-07-23. 근거: run12 로그 전수 분석에서 발견한 숨은 병목 — 총 LLM 지연 합
33,912초 vs 벽시계 955초 = **실효 병렬 35.5/64** (step7 단독 37.7/64). GPU 를 절반
남짓만 쓰고 있었다.

## 원인 (코드로 확정)

문서화된 계약(framework_per_node 머리주석 "caller 는 callee 의 summary 완료만
기다린다")과 구현이 달랐다 — 구현은 `deps[fid] <= done` 으로 callee 의 **3콜 전체 +
영속화 완료**를 기다렸다. rules 콜은 p50 28초·p95 85초라 그만큼 caller 투입이 늦고,
종반에는 소수 큰 노드만 남아 GPU 가 놀았다(rules max 165초 꼬리).

## 계약 (변경 후)

- 의존 해제 시점을 의존 종류별로 분리한다:
  - **CALLS**: caller 프롬프트는 callee 의 `ctx.summary` 한 줄만 읽는다
    (`_format_callee_summaries`) → callee 의 **summary 확정(경계 검사 후) 즉시 해제**.
    three_split 이 그 시점에 `ctx.summary` 를 공개하고 `on_summary_ready` 로 신호한다.
    값은 종전 fan-in 시점과 동일 — **산출물 불변**.
  - **PARENT_OF**: 부모 #3 분할은 자식 rules·questions 를 읽는다
    (`_format_child_business_logic`) → 종전대로 전체 완료 대기.
- 투입 순서는 critical-path rank 내림차순: rank = 자기 비용(줄 수) + 나를 기다리는
  사슬의 최대 rank. 신호는 범용(줄 수·의존 구조)뿐 — 프로젝트 특화 판단 없음.
- 실패·스킵 노드도 완료 시 summary_done 에 넣어 caller 를 막지 않는다(기존과 동일하게
  caller 는 one_liner/signature 폴백 렌더). 회계 등식(FR-008)·pause 재투입 불변.

## 검증 결과 (증거)

- [x] 단위: tests/pipeline/step7_analyze_code/test_dataflow_scheduling.py 3/3 —
      의존 맵 분리(SCC 절단 포함), rank 순서(사슬 아래·큰 노드 우선), **조기 해제
      계약**(callee rules 가 인위로 붙잡혀도 caller 가 summary 확정 후·전체 완료 전에
      시작함을 스텁으로 실증). 전수 336 OK.
- [x] 전체 실행 run13 (2026-07-23 13:46~13:58, SKT 44k·23파일, test DB):
      **총 15분55초 → 12분35초(-21%) · step7 703→526초(-25%) · 실효 병렬 37.7→55.5**.
      완주 416/416. 결정론 수치는 run11/run12 와 동일 쿼리로 **전부 정확 일치**
      (코드본문 4,484 · 요약 439 · READS 70 · WRITES 15 · CALLS 651 · REFERENCES 7,209).
      LLM 수치는 기존 변동 대역(RULE 899 — run6~10 관측 상단과 동일, EXAMPLE 1,813).
      spec 080 감지기도 같은 run 에서 실전 발동 1건(4,492자 차단) — 상한 도달 0.
