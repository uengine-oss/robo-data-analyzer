# 083 — 사전 압축(one_liner)을 분석 대상 노드로 한정

작성: 2026-07-23. 근거: run13 실측에서 step6 컨텍스트 64.8초의 실질 전부가
`large_code_one_liner` 335콜이었고, 그중 **208콜(62%)이 제어문 노드**였다.

## 원인 (코드로 확정)

`one_liner` 는 렌더 시 summary 가 없을 때의 대타이며, 소비처는 정확히 셋이다 —
`_format_callee_summaries`(callee 요약), `compact_body._ph`(자식 구간 치환),
`ancestor_context`(조상 골격). 셋 모두 **분석 컨텍스트 dict 에 있는 id 만** 조회한다
(`_children` 은 `if c in contexts` 로 거른다).

Framework 엔진은 분석 직전 contexts 를 routine 으로 좁힌다. 따라서 제어문 노드의
one_liner 는 만들어도 조회 경로가 없다. 반면 DBMS 엔진은 range 자체가 분석 단위라
contexts 를 좁히지 않으므로 그 노드들의 one_liner 는 실제로 쓰인다. 즉 "무엇이 분석
대상인가"가 엔진마다 다른데 step6 이 그 답을 모른 채 전부 압축하고 있었다.

## 계약 (변경 후)

- `Strategy.analysis_targets(code_nodes) -> set[str]` 를 선언 필드로 추가한다.
  Framework = routine 라벨 노드, DBMS = skip-투명 유효 트리 잔존 노드.
- 이 함수가 **단일 진실**이다. Framework 엔진의 기존 인라인 필터도 같은 함수를 쓴다
  (종전엔 `label_by_id` 로 같은 판단을 두 번 하고 있었다).
- `build_context_step` 은 `분석 대상 ∩ 임계 초과` 노드만 압축한다.
- 산출물 불변: 제외되는 노드의 one_liner 는 어느 렌더에서도 읽히지 않던 값이다.

## 검토했으나 하지 않은 것 — "순환 노드에만 압축" (더 줄일 수 있어 보였으나 기각)

남은 127콜(≈24초)도 없앨 수 있어 보였다: 분석 대상은 모두 summary 를 받으므로 대타가
필요한 경우는 의존이 끊긴 순환 노드뿐이고, 이 코퍼스는 `in_cycle=0` 이라 전부 사장이다.

**기각 사유**: `ancestor_context` 의 one_liner 사용은 "summary 부재" 폴백이 아니라
**크기 폴백**이다 — 조상 골격이 토큰 임계를 넘으면 순환 여부와 무관하게 one_liner 로
대체한다. Framework 는 조상이 컨텍스트에 없어 이 경로가 죽어 있지만, DBMS 는 조상이
컨텍스트에 살아 있어 순환으로 좁히면 큰 조상의 골격 대체가 조용히 깨진다. DBMS 코퍼스가
없어 실물 검증도 불가하다. 24초를 위해 검증 불가능한 경로를 흔들 이유가 없다.

(위 본 변경은 이 함정에 걸리지 않는다 — 대상 밖 노드는 Framework·DBMS 어디서도
컨텍스트에 없어 조상 체인에 등장할 수 없다.)

## 검증 결과 (증거)

- [x] 단위: tests/pipeline/step6_build_context/test_one_liner_targets.py 3/3 —
      Framework 대상=routine, DBMS 대상=제어문 포함, 큰 제어문 노드가 압축 호출에서
      제외됨(둘 다 임계 초과임을 먼저 단언해 테스트 무효화 방지). 전수 355 OK.
- [x] 전체 실행 대조 (run17, 2026-07-23 15:00~15:11, SKT 44k, test DB):
      **총 12분35초 → 11분16초 · step6 64.8초 → 26.4초 · 큰코드노드 335 → 127**.
      완주 416/416. 결정론 산출물 6종 run13 과 **정확 일치**(코드본문 4,484 · 요약 439 ·
      READS 70 · WRITES 15 · CALLS 651 · REFERENCES 7,209).
- [x] 입력 불변 실증: LLM 호출 1,788 → 1,573(-215 = 제거한 208콜 + 스캔 턴 차이),
      남은 콜의 프롬프트에 **compact 치환 마커 0건** — Framework 에서 자식 요약 치환이
      애초에 발생하지 않음을 실물 프롬프트로 확인(제거된 one_liner 는 읽히던 값이 아니다).
- [!] LLM 수치 RULE 893(-0.7%) · EXAMPLE 1,731(-4.5%). 입력이 불변임이 위에서 실증됐고
      결정론 수치가 전부 일치하므로 서버 배치 비결정성(사건 G) 귀속. 다만 EXAMPLE 은
      관측 대역(run11~17: 1,731~1,813) 하단이라 다음 run 에서 계속 감시한다.
