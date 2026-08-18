# FW/DBMS 의미 소유권·입력·배치·소비 배선 — 2026-08-18

이 문서는 현재 source의 실제 배선을 고정한다. 과거 문서와 다르면 현재 코드·actual prompt·run이
우선한다. “공통 semantic-frame/v1”은 출력 계약이 공통이라는 뜻이지 입력 노드와 호출 단위까지
같다는 뜻이 아니다.

> **2026-08-19 현행 override**: 아래 6~8절은 Rule LLM 제거 전 원인·실험 이력이다.
> 현재 Rule은 Framework/DBMS 모두 branch inventory의 required row를
> `required_rule_rows → materialize_rule_items → materialize_and_validate_rules` 한 경로로
> 물질화하는 LLM 0 산출물이다. `rule-structures/deterministic-rule-v1`이 원 분기표 대비
> row coverage·중복·재물질화 동등성을 보존하고, graph/MCP/Architect에는 exact cond/then,
> 좌표, Example, 연결 evidence만 전달한다. Rule 호출·입력·출력 token은 모두 0이다.

## 1. 전략별 단일 표

| 전략·concern | 의미 owner | 모델 입력 | 정상 호출/배치 | 출력·검증 | graph 소비 |
|---|---|---|---|---|---|
| FW summary | routine만 | routine 정확 범위, structural profile, target-owned FUNCTION slot, 내부 CALL/CONTROL/RETURN evidence, callee summary | routine 1개, 최대 responsibility/input/result 3 slot을 한 요청 | dynamic exact-slot schema → canonical ID 복원 → grounding/contradiction | CodeNode summary/stereotype/frame |
| FW Rule | routine의 branch inventory 행 | LLM 입력 없음. RULE fact·condition/effects·row source는 결정론 감사 입력 | required row 전수 동기 물질화, LLM 0 | exact row coverage·중복 0·audit 재물질화 동등 | Rule, HAS_RULE, AFFECTS |
| FW TABLE | routine에서 확인된 direct/effective data usage | direct table evidence와 frame data slot | 독립 table concern | 허용 table/column과 evidence 검증 | TABLE/COLUMN 관계·설명 |
| FW Example | accepted Rule | LLM 입력 없음 | 결정론 0 LLM call | Rule cond/then projection | Example, HAS_EXAMPLE |
| DBMS summary | effective AST의 각 non-skip CodeNode | target별 key/range/AST/summary slot/source, 구조·callee·직접 evidence | 같은 effective parent의 sibling을 token budget까지 묶음; root는 solo | key별 exact dynamic schema, missing/extra/duplicate 거부 | 각 CodeNode summary/stereotype/frame |
| DBMS Rule | routine branch row를 배정받은 unit/gate CodeNode | LLM 입력 없음. unit/gate는 행 소유권만 정하며 구조 fact를 물질화기에 전달 | 행은 정확히 한 node/range가 소유, required row 전수 동기 물질화 | exact row coverage·중복 0·audit 재물질화 동등 | routine Rule/Example에 연결 |
| DBMS TABLE | direct R/W를 소유한 statement CodeNode | direct reads/writes/op/column 후보와 정확 source | 현재 sibling batch 중 direct R/W node만 별도 concern | direct ownership과 exact slot 검증 | TABLE/COLUMN 관계·설명 |
| DBMS Example | accepted Rule | LLM 입력 없음 | 결정론 0 LLM call | Rule cond/then projection | Example, HAS_EXAMPLE |

## 2. 현재 코드 근거

- FW target 필터·DAG: `framework_per_node.py:52-59, 97-142, 155-252`
- DBMS effective tree·sibling batch: `batch_planner.py:33-99`
- required row 단일 소유자: `pipeline/quality_gates/rule_obligations.py::required_rule_rows`
- 공통 물질화: `pipeline/models/rule_materialization.py::materialize_rule_items`
- FW/DBMS 공통 검증: `rule_row_batching.py::materialize_and_validate_rules`
- DBMS Rule unit/gate·row ownership: `batch_analyze.py::_plan_rule_units`, `_materialize_dbms_rules`
- DBMS concern 동시 시작: `batch_analyze.py:435-575`
- DBMS target별 입력 블록: `batch_prompt_values.py:34-164`
- 영구 감사 산출물: `rule_audit_artifact.py`, `branch_audit.py::persist_routine_audits`
- DBMS TABLE direct packet: `batch_prompt_values.py:236-303`
- exact keyed schema·복원: `summary_response_schema.py:178-231`, `batch_analyze.py:640-785`
- routine 최대 3 summary slot: `three_split_llm_fanout.py:106-134`
- owner·concern partition: `semantic_frame_planner.py:396-496`
- Example graph projection: `example_mapping_builder.py:69-147`

## 3. DBMS summary의 정확한 형태

DBMS summary는 “부모 블록 전체를 한 번 보내고 모델의 줄번호를 사후 분배”하지 않는다. 같은 부모의
형제 target 여러 개를 한 요청에 넣을 수 있지만, 각 target은 고유 request-local `node_key`, canonical
`target_id`, `source_range`, AST 값, summary slot, source block을 가진다. 응답 schema도 그 key를 필수
field로 강제하고 정규화 단계가 key를 canonical CodeNode로 복원한다. unknown, missing, duplicate key가
하나라도 있으면 그 concern은 성공으로 간주하지 않는다.

작은 target은 정확 범위 원문을 보낸다. token threshold를 넘은 container만 자식 구간을 compact하고,
자식의 canonical summary를 문맥으로 넘긴다. 즉 중복을 줄이되 자식 prose를 부모의 직접 source evidence로
오인하지 않는다.

## 4. 현재 성능 판정과 미검증 축

정확성 경계는 갖췄지만 “최적 batch”는 아직 미검증이다.

- `plan_batches`는 현재 `AnalysisContext.token_count` 합만 보는 greedy sibling packing이다. 동적 schema
  크기, 예상 output token, evidence reference 수, 실제 cache hit와 queue wait는 admission에 쓰지 않는다.
- shared `PrioritySemaphore`는 총 동시성을 제한하고 독립 DAG work를 실행하지만, summary priority가
  `-10,000,000`으로 절대 우선이다. Rule은 더 이상 LLM waiter가 아니므로 현재 starvation 비교축은
  summary와 TABLE 및 후처리 concern이다.
- Step 8 일부는 summary/analysis handoff로 겹치지만 dependency와 공유 slot 때문에 실제 overlap과
  critical path 감소는 actual timeline으로 확인해야 한다.
- 따라서 채택 조건은 단순 wall 감소가 아니라 concern별 queue wait, GPU idle, calls/input/output/cache,
  TTFT/E2E p95, deadline/repetition, slot/row 누락·중복, 의미 판정의 동시 합격이다.

## 5. 비교할 범용 후보

1. **현재 ownership 고친 기준선**: 잘못된 FW CALL slot과 고정 8-slot microbatch를 제거한 현재 경로.
2. **cost-aware packing**: input token뿐 아니라 schema/output/evidence 비용으로 의미가 닫히는 sibling만 묶는다.
3. **work-conserving fair admission**: summary가 임계경로 우선권은 갖되 한 waiter가 capacity번보다 많이
   추월당하지 않는 bounded-overtaking을 적용한다. 다른 concern이 없으면 summary가 전 슬롯을 사용한다.
4. **prefix-aware server scheduling**: 공통 prefix가 큰 요청군을 LPM/DFS-weight와 현 FCFS로 A/B 한다.
5. **server overlap/mixed chunk**: overlap scheduler와 mixed chunk를 각각 독립 A/B 한다.

고정 5/6행, input-length-only split, 의미 삭제 prompt, 동일 payload retry는 과거 반증됐으므로 재실험하지
않는다. 최종 구현에는 승자 한 경로만 남긴다.

## 6. 2026-08-19 실제 APAS 기준선과 발견 결함 — Rule LLM 제거 전 역사

- 대상: `분석대상모음/framework/c/apascrt00010t02_20260406`, DB `framework`
- run: `20260818_152935_fd1526e9f5ac`, 동시성 32, response cache off
- 중단 시점: 10분 33초, 417 calls, input 3,203,227 tokens, output 141,709 tokens
- GPU deadline 0건. fatal 원인은 `zordmfax00001.c:b430_cust_proc`의 row 1785였다.
- 해당 row의 검증된 slot은 `DECISION.ROLE` 140자와 `DECISION.TRUE_OUTCOME` 600자였다.
  두 slot은 각각 schema를 만족했지만 호환용 `Rule.nl` 투영이 합계 741자를 옛 단일-leaf
  상한 600자와 비교해 run 전체를 종료했다.
- 수정: 모델이 직접 쓰는 legacy wire leaf의 600자 제한은 유지한다. 반면 검증된 여러 semantic
  slot을 코드가 합치는 projection은 한 줄 제약만 적용해 어느 slot도 자르거나 버리지 않는다.
- RULE 실측 129 prompt의 고정 8-row 배치는 row 평균 6.3, slot 평균 9.6, evidence 평균
  7,616자였다. 옛 `cond/then` 모델 출력용 reference 48개 상한이 남아 있어 compact 입력으로
  바꾼 뒤에도 인접 batch를 단 하나도 합치지 못했다.
- 수정: 현재 모델 출력은 slot meaning/status/missing_context뿐이므로 참조 수를 옛 output field
  상한이 아니라 모델이 해석해야 할 exact condition/effect 사실량으로 정의했다. compact evidence
  chars, actual semantic slots, evidence references, hard row guard를 모두 만족할 때만 묶고
  deadline/repetition은 서로 다른 작은 schema로 exact bisection한다.
- Summary prompt에서 동일 `branch_index`가 설명문과 본문에 두 번 삽입되던 deterministic 중복도
  제거했다. 기준선 첫 64 Summary prompt에서 중복분은 총 66,666자였다.
- 검증: 관련 74 tests + prompt contract 21 tests/76 subtests + 전체
  `1092 passed, 5 skipped, 384 subtests`.

### 24-slot 후보 탈락과 최종 admission 경계

첫 cost-aware 실제 재실행 `20260818_154955_52e14bedb9ad`은 64 동시성에서 24-slot 후보를 검증했다.
기준선 완료를 기다리지 않고 다음 실제 분리가 확인된 시점에 성능 실패 run으로 중단했다.

| 결과 | rows | slots | evidence chars | evidence refs | latency |
|---|---:|---:|---:|---:|---:|
| 성공 | 8 | 13 | 13,439 | 33 | 153.3초 |
| 성공이나 위험 | 13 | 19 | 15,199 | 54 | 188.8초 |
| deadline | 14 | 20 | 16,979 | 54 | 210.0초 |
| deadline | 15 | 23 | 18,380 | 69 | 210.0초 |
| deadline | 15 | 23 | 23,840 | 115 | 210.0초 |
| deadline | 15~16 | 24 | 18,646~22,248 | 68 | 210.0초 |
| deadline | 13 | 24 | 19,222 | 275 | 210.0초 |

따라서 24-slot/24,000-char 후보는 채택하지 않는다. 분할 복구는 의미 누락을 막지만 210초를 먼저
소비하므로 정상 경로가 될 수 없다. 최종 admission은 실제 성공/실패 경계의 안전측으로 다음처럼 둔다.

- `MAX_RULE_SEMANTIC_SLOTS=12`
- `MAX_RULE_EVIDENCE_CHARS=16_000`
- `MAX_RULE_EVIDENCE_REFERENCES=64`
- `DEFAULT_ROWS_PER_CALL=24`는 비용 축이 놓친 경우만 막는 최후 hard guard

고정 8행으로 돌아간 것이 아니다. 12개의 값싼 행은 한 요청으로 묶는 계약 테스트를 유지하며,
slot/evidence가 무거우면 8행 미만도 나눈다. 같은 요청 재시도 없이 deadline/repetition만 exact bisection한다.

14-slot 확인 run `20260818_155817_9bd037141145`에서도 대부분은 완료됐지만
`c420_insert_zpay_ap_auth_req_hst`의 뒤쪽 14-slot 묶음은 evidence 10,771자·참조 40개임에도
2,320자를 생성하다 210초 deadline에 도달했다. 같은 함수의 앞쪽 10-slot 묶음은 106.5초에
완료됐다. 입력/참조 축만으로는 직접 효과의 서술 길이를 완전히 예측할 수 없으므로 slot 상한을
12로 한 단계 더 낮췄다. 반복 생성 1건은 11-slot에서 29.2초에 guard가 중단했으며, 동일 payload
retry 없이 서로 다른 작은 schema로 분할되는 계약을 유지한다.

## 7. Rule 의미 중복과 private key 누출의 양끝 영향 판정 — 폐기 경로 역사

중단 run `20260818_173654_12656da7f405`의 저장 Rule 응답을 전수 매핑했다.

| 항목 | 실측 |
|---|---:|
| 완료 Rule 요청 | 104 |
| semantic slot | 1,009 |
| `DECISION.ROLE` | 350 |
| `DECISION.TRUE_OUTCOME` | 350 |
| `STATEMENT.DIRECT_ACTION` | 309 |
| status | sufficient 1,009 / partial 0 / insufficient 0 |
| exact response wire `S<n>`가 meaning에 등장 | 6 slot |

첫 저장 응답부터 `rc == RC_NFD`, `return RC_NFD`, `PFM_ERRC 호출 … return RC_ERR`처럼
condition/effects를 나눈 그대로 다시 썼다. 최장 outcome/action은 각각 600자로 source call·대입을
복사했다. 이는 의미 해석을 구조 사실 수만큼 복제한 것이며, 구조 정본은 이미 RULE fact의
`condition/effects/type/timing/...`와 accepted Rule의 `cond/then/when`, 결정론 EXAMPLE에 있다.

소비자는 다음과 같이 확인했다.

| 경계 | 실제 소비 | 복수 role/outcome prose 필요 여부 |
|---|---|---|
| graph Rule | `anchor_line/cond/then/when/nl` + EXAMPLE | 불필요. `nl` 한 문장만 호환 표시 |
| Analyzer MCP frame | full `profile.evidence`와 semantic slots, rule_examples | 불필요. RULE fact가 구조 전부 보존 |
| Architect packet index | RULE evidence ID와 slot ID를 각각 index | 특정 `DECISION.*` role을 요구하지 않음 |
| Architect GWT gate | scenario마다 실제 RULE evidence ID, owner, TABLE/direct refs, scalar grounding | 불필요. exact RULE 구조가 필수 |

채택 계약은 required row마다 단일 `RULE.BUSINESS_MEANING`이다. LLM은 행 전체의 입증된 업무 의미만
쓰고, 구조 facts는 생성하지 않는다. `Rule.nl`은 이 한 slot에서만 투영한다. 따라서 APAS 104요청의
관측 구조라면 출력 의무는 1,009→350 slot로 줄지만, RULE condition/effects/Example/좌표는 0건
삭제된다. 실제 calls/tokens/wall과 의미 정확도는 구현 후 동일 봉인 slice에서 재측정하기 전까지
예측값으로만 취급한다.

private key는 canonical ID가 아니다. 새 wire namespace는 slot assignment digest를 포함하고 현재 요청의
exact key가 meaning/missing_context에 나오면 문자열을 지워 정상처럼 만들지 않는다. 해당 leaf만
`insufficient`로 바꾸고 quality finding으로 표면화한다. C/E ref는 현재 행에 실제 노출된 ID와 source
literal을 함께 아는 기존 ambiguity-aware grounding을 canonical frame 전에 적용한다.

## 8. 2026-08-19 중간 후보 구현·자동 검증 — 현행 아님

- planner는 required row마다 `RULE.BUSINESS_MEANING`을 정확히 한 개만 만든다. Rule response 경계도
  다른 historical family를 거부하고 assigned row↔slot exact 1:1 coverage를 검증한다.
- canonical condition/effects/type/timing/complement/nesting/fallthrough/handler/continuation,
  accepted Rule cond/then/when, 결정론 EXAMPLE은 변경하지 않았다.
- provider wire는 `W<slot-assignment SHA-256 앞 10자>_<index>`다. 같은 assignment에서는 안정적이고,
  누락·추가·중복은 닫힌 schema가 거부한다.
- 현재 요청의 exact private key가 meaning 또는 missing_context에 나타난 fixture는 해당 leaf만
  `insufficient`가 되고 sibling은 sufficient로 남았다. 실제 업무 literal `S0`는 그대로 보존됐다.
- Rule private C/E ref는 canonical frame과 `Rule.nl`에 동일하게 grounding됐고, source condition의
  실제 literal `C1`은 ambiguity-aware 규칙으로 보존됐다.
- 실행 결과:
  - 관련 semantic-frame/rule/batching 62 tests: 통과
  - 연관 prompt/quality/projection/MCP 76 tests: 통과
  - Analyzer 전체: `Ran 1056 tests` / `OK (skipped=5)`
  - prompt ledger: `OK - 29 prompts byte-identical`
  - Architect 새 Rule slot 소비 집중: `29 passed`
  - Architect 전체: `1382 passed, 2 skipped, 3 warnings`

이 결과는 자동 검증층이다. 실제 APAS 저장 prompt에서 slot 감소량, calls/tokens/latency, 의미 정확도는
T131-37의 동일 봉인 slice A/B 전까지 미검증이다.
