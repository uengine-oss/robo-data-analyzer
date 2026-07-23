# 094 — one_liner 사전 압축을 "읽힐 수 있는 노드"로 좁힘

작성: 2026-07-23. spec 083("분석 대상 노드만")을 대체한다.

## 무엇이 문제였나

`one_liner` 는 렌더 시 `summary` 가 없을 때 쓰는 **대타**다. 소비처는 셋이고 각각 관문이 있다.

| # | 소비처 | 관문 |
|---|---|---|
| 1 | `three_split_llm_fanout._format_callee_summaries` | `c.summary or c.one_liner or c.signature` — summary 가 없어야 도달 |
| 2 | `range_body.compact_body` (자식 치환) | `body_for` 가 `ctx.extra["child_ids"]` 를 요구 |
| 3 | `range_body.ancestor_context` (조상 골격) | `ctx.extra["parent_id"]` 를 요구 |

`FrameworkContextBuilder.augment` 는 항등이라 `ctx.extra` 에 `child_ids`/`parent_id` 를
**아예 안 채운다**. 따라서 Framework 에서 2·3 은 물리적으로 도달 불가다. 1 은 Bottom-up
순서상 callee 가 먼저 분석되므로 summary 가 항상 있고, 없는 경우는 **순환 내부**뿐이다.

결과: 이 코퍼스(`in_cycle=0`)에서 one_liner 127건이 전량 사장됐다. 실측 32초.

## 왜 엔진으로 분기하면 안 되나

"Framework 는 순환만"으로 하드코딩하면 DBMS 의 nested range(2·3 이 살아 있음)가 조용히
깨진다. 관문이 `ctx.extra` 라는 **데이터**이므로 데이터를 읽어 판정하면 엔진 분기 0으로
같은 효과가 나고, 부모-자식을 채우는 strategy 는 자동으로 안전하다.

## 계약

`_one_liner_readable_ids(contexts)` = 다음의 합집합.

- 어떤 ctx 의 `extra["parent_id"]` 로 지목된 id (소비처 3)
- 어떤 ctx 의 `extra["child_ids"]` 에 든 id (소비처 2)
- `find_call_cycles(callee 간선, 전체 id)` 가 순환으로 판정한 id (소비처 1)

압축 대상 = `분석 대상 ∩ 읽힐 수 있음 ∩ 토큰 임계 초과`. 세 조건 모두 결정론이다.

`find_call_cycles` 는 step7 에만 있어 step6 이 쓰면 계층 역전이라
`shared/graphs/call_cycles.py` 로 옮겼다. 소비자(step7 둘, step8 하나)를 같은 슬라이스에서
정합시켰고 옛 경로 참조는 0이다.

## 명시적 트레이드오프 (숨기지 않는다)

분석이 **실패한** callee 는 summary 가 없어 소비처 1 에 걸리는데, 순환 밖이면 one_liner 를
안 만들므로 `signature` 로 떨어진다. 종전엔 큰 노드에 한해 one_liner 가 나왔다.
다만 임계 미만(1,000 토큰) 노드는 **원래도** signature 로 떨어졌으므로, 이 변경은 실패
경로의 대타를 크기와 무관하게 균일화한다. 완주 416/416 에서는 발생하지 않는 경로다.

## 검증 결과 (증거)

- [x] 단위: `tests/pipeline/step6_build_context/test_one_liner_targets.py` 6/6 —
      부모/자식 지목·순환 멤버·평탄한 호출사슬(읽힐 자리 없음) 세 경우와 통합 1건.
      전수 회귀 **385 OK**(skip 5).
- [x] 실물 웜런: `one_liner_start` **0건**(종전 127건). 결정론 6종 정확 일치,
      완주 416/416, RULE/EXAMPLE 직전 웜런과 동일.
- [ ] **미검증**: 콜드런에서의 실제 시간 절감(예상 -32초). 웜런은 one_liner 콜도 캐시
      적중이라 절감이 안 보인다. 다음 콜드런에서 확인해야 한다.
