# Framework call occurrence publish 감사 — C-065

**판정 시각**: 2026-08-21 KST
**상태**: 구현·자동 회귀·결정론 actual·봉인 직접 판정 완료
**범위**: Analyzer Framework call occurrence → Neo4j generation → REST/MCP/search. Architect는 보류.

## 1. 관찰된 실제 값

- Parser/Analyzer actual APAS: source 23, canonical call 12,113, unique 12,113, duplicate 0.
- configured preprocessing v1은 build/trace 근거가 없어 23 source 전부 unresolved다.
- Analyzer resolution: input/accounted 12,113, `unresolved_preprocessing=12,113`, CALLS edge 0.
- 구 outcome-only resolution ledger SHA-256:
  `01590fb9e31b8e934907f1a994f8261d92216e10c086778baf1b5678dcec850a`.
- source/grammar/owner/presence/outcome을 합친 새 canonical occurrence ledger SHA-256:
  `2ab333f8c9e38e67a7c84d9c8d3bf8153c40b0b1bd1f1495040789aa62966d23`.
- 최신 actual report:
  `_runs/framework/analyzer-call-occurrence-publish-20260821-r4.json`.

## 2. source→consumer 배선과 끊긴 고리

| 고리 | 현재 생산/변환 | 실제 출력 | 판정 |
|---|---|---|---|
| Parser fact | common evidence IR call fact | full/callee/argument range, presence, canonical fact ID | 보존 |
| Analyzer step2 | compatibility AST↔fact exact join + routine owner | `call_sites` 12,113 | 보존 |
| classifier | `classify_call_sites` | source-order `edge|excluded|preserved|unresolved` ledger 12,113 | 보존 |
| step5 state | `link_calls_by_name_step` | `call_resolution_ledger` update | 보존 |
| run boundary | `run_executor.execute_run` | update key 이름만 순회하고 payload 폐기 | **손실** |
| quality | `_summarize_analysis_findings` | analysis finding 파일만 세어 call unresolved 0으로 간주 | **잘못된 completed 가능** |
| generation | `publish_generation` | node/edge/semantic slot v1 closure만 저장 | **occurrence 없음** |
| search session | `completed_generation_session` | generation ID만 전달 | **quality/degraded 없음** |
| REST/MCP | `run_search`, `build_node_detail`, `build_gwt_detail` | aggregate CALLS만 노출 | **호출 없음과 전부 미해결 구별 불가** |

파일 좌표는 구현 직전 현재 HEAD 기준이다.

- `pipeline/steps/step5_linking/link_calls/call_classifier.py`: canonical ID/accounting/ledger producer
- `pipeline/steps/step5_linking/link_calls/link_calls_by_name_step.py`: state producer
- `pipeline/execution/run_executor.py:270-275`: update payload 폐기
- `pipeline/execution/run_executor.py:300-305`: finding-only quality로 publish
- `pipeline/execution/run_executor.py:706-755`: finding-only summary
- `graph/generation.py:211-300`: graph-generation-closure/v1
- `search/corpus/generation.py:19-75`: generation ID-only session
- `search/service.py:28-42`: ID-only search meta
- `search/corpus/node_detail.py:57-225`: aggregate relationship-only detail/GWT projection

## 3. 유입 이력

- `b914a28`: canonical call evidence consumer와 workflow ledger 도입.
- `bdaf7ae`: atomic generation publish 도입. 당시 closure가 node/edge/slot까지만 포함.
- update payload를 사용하지 않는 `astream` loop는 `4d0d25b` 계열에서 유입돼 ledger보다 먼저 존재했다.
- 따라서 한 커밋의 단일 오타가 아니라 새 evidence producer가 기존 run/publish 계약에 연결되지 않은
  cross-boundary integration 결함이다.

## 4. 확정 계약

1. Framework 의미 owner는 routine이다. call occurrence는 `CALL_OCCURRENCE` evidence node이며 별도 의미
   owner가 아니다.
2. 모든 canonical call ID를 같은 generation에 exactly once 저장한다. source order는 explicit ordinal로
   보존한다.
3. owner가 있으면 `HAS_CALL_OCCURRENCE`로 routine에 연결한다. ownerless unresolved도 generation closure에는
   남긴다.
4. 원문을 다시 파싱하지 않고 step2 call-site fields와 classifier outcome을 한 행으로 결합한다.
5. aggregate CALLS는 dependency view로 유지한다. CALLS 0은 occurrence 0을 뜻하지 않는다.
6. publish 전 call ID duplicate, owner membership, outcome population과 canonical hash를 검증한다.
7. unresolved population > 0이면 `completed_with_findings`다. excluded/inactive preserved만으로 degraded로
   만들지 않는다.
8. search meta는 generation quality와 call-resolution summary를, node detail/GWT는 owner의 exact occurrence
   evidence를 제공한다. preview용 category count가 full ledger를 대체하지 않는다.

## 5. 폐기한 설계

| 후보 | 폐기 이유 |
|---|---|
| generation property 하나에 full JSON | corpus가 커질수록 단일 문자열/transaction/read가 무한 성장하고 owner 조회가 전체 decode를 요구 |
| routine property마다 full JSON | 대형 routine 하나가 동일 문제를 만들고 ownerless occurrence를 보존하지 못함 |
| unresolved만 저장 | edge/excluded/preserved와 입력 모집단의 exact-once 뺄셈을 DB/API에서 증명할 수 없음 |
| CALLS relationship에 occurrence 배열 | excluded/unresolved는 target relationship 자체가 없어 보존 불가; aggregate edge와 occurrence 정체성 혼합 |

## 6. formal red와 검증 게이트

1. classifier ledger가 source call-site range/argument/presence/owner를 한 행에 보존하며 순서/ID가 exact-once.
2. evidence writer가 duplicate/blank/invalid outcome, missing owner를 pointer publish 전에 거부하고 한
   transaction으로 occurrence node+owner edge를 쓴다.
3. generation closure가 count/hash/outcome/reason을 저장하며 duplicate/mismatched owner에서 pointer를 바꾸지 않음.
4. run executor가 LangGraph update payload를 캡처하고 unresolved를 `completed_with_findings`로 판정.
5. completed generation session/search meta/node detail/GWT가 동일 summary/occurrence를 노출.
6. 기존 aggregate CALLS, 검색 corpus, generation isolation 회귀 통과.
7. `test` DB 명시 transaction rollback으로 node/edge/manifest 저장·조회와 residue 0 확인. 임시 DB 생성 금지.
8. actual APAS 12,113 occurrence를 Neo4j/API/MCP까지 전수 count/hash 대조하고 봉인 6종을 직접 판정.

## 7. rollback 경계

새 evidence label/relationship, generation closure v2와 reader field는 한 Analyzer 책임 슬라이스다. 실패 시
해당 커밋 하나로 되돌릴 수 있어야 하며 Parser `00fdf2a`와 Analyzer fail-closed `2d5f4c4`는 독립 보존한다.

## 8. formal red 결과

구현 전 계약 테스트는 다음 실제 단절을 각각 실패로 고정했다.

1. classifier ledger에 range/callee range/argument/presence/owner가 없었다.
2. `CALL_OCCURRENCE` writer와 generation occurrence closure v2가 없었다.
3. run quality가 unresolved call을 읽지 않았다.
4. search meta와 node detail/GWT가 occurrence/degraded 상태를 노출하지 않았다.
5. owner occurrence가 generic relationship으로만 보이거나 전혀 보이지 않았다.

검증을 느슨하게 하지 않고 오래된 macro-call fixture도 Parser 정본 field를 제공하도록 수정했다.

## 9. 구현 중 actual 경계가 추가로 발견한 결함

| 결함 | mock이 놓친 이유 | 근본 처리 |
|---|---|---|
| Parser range는 `charOffset/charLength`, 새 내부 모델은 snake_case만 수용 | synthetic fixture가 내부형만 사용 | strict boundary가 정본 camelCase와 이미-normalized 내부형을 모두 받아 단일 snake_case 저장형으로 정규화 |
| occurrence read Cypher가 aggregation 뒤 `occurrence.ordinal`로 정렬 | fake transaction은 Cypher를 실행하지 않음 | 반환 property map alias의 ordinal로 정렬하고 actual Neo4j에서 확인 |
| 구형 `FUNCTION.id` 등 열린 AST label constraint가 generation staging을 차단 | 기존 explicit migration 목록은 stable label 위주 | `ANALYZER_OWNED` 실제 label과 `SHOW CONSTRAINTS` schema를 대조해 Analyzer label의 obsolete single-`id` uniqueness만 제거; Architect/타 owner constraint는 제외 |
| generation slot closure가 저장 정본 `SemanticFrame.slots` 대신 MCP projection `claims[]`를 읽음 | fake frame이 잘못된 claims 형식을 제공 | `SemanticFrame.model_validate_json`으로 저장 정본 전체를 검증하고 canonical slot map에서 identity 산출 |

## 10. green 및 3층 검증

### 10.1 자동 회귀

- Step 5 전체: 130 tests, failure 0.
- graph/execution/API 집중: 153 tests, failure 0, Neo4j opt-in 1 skip.
- C-065 핵심 producer/consumer 재검증: 38 tests, failure 0.
- Analyzer 최종 전체: 1,134 tests, failure 0, skipped 6.
- prompt byte identity: 16/16 identical.

### 10.2 결정론 actual 전수 회계

`test` DB에서 production schema 보장 후 단일 명시 transaction으로 APAS 전량을 적재·publish·조회하고
rollback했다. 임시 DB를 만들지 않았다.

| 항목 | 결과 |
|---|---:|
| source / canonical occurrence / unique | 23 / 12,113 / 12,113 |
| routine owner identities | 412 |
| persisted occurrence / owner edge | 12,113 / 12,113 |
| generation nodes / edges | 12,525 / 12,113 |
| Neo4j ledger SHA-256 | `2ab333f8c9e38e67a7c84d9c8d3bf8153c40b0b1bd1f1495040789aa62966d23` |
| API owner packets / occurrences / SHA | 412 / 12,113 / 동일 |
| MCP owner packets / occurrences / SHA | 412 / 12,113 / 동일 |
| outcome / quality | unresolved 12,113 / `completed_with_findings` |
| rollback | residual node 0, active pointer unchanged |
| 최종 wall time | 201.58초 |

두 독립 Parser/Analyzer replay ledger가 같고, DB storage decode, owner별 API full detail, source-free MCP packet을
다시 합친 전역 ordinal ledger도 입력과 exact equality다. count가 같은 것만으로 승인하지 않았다.

### 10.3 봉인 직접 의미 판정

결과를 보기 전에 고정한 framework runtime, stdlib, function macro, inactive source branch, argument wrapper,
workspace named routine 6종을 Parser exact range/presence와 storage/API/MCP projection까지 대조했다. 6/6 모두
`insufficient_preprocessing_build_context` unresolved이며 해당 source의 preprocessing evidence ID 2개와
일치했다. 이는 현재 build command/trace 부재의 정직한 degraded 결과이지 의미 정확도 완료 주장이 아니다.

## 11. 미검증과 후속

- configured preprocessing v2/expanded-token occurrence가 아직 없으므로 12,113 call의 effective target 의미는
  계속 미검증이다. 이 slice는 그 불확실성을 손실 없이 publish·소비하게 만든 것이다.
- actual 전수 transaction 201.58초는 correctness에는 합격했지만 성능 기준선으로 기록한다. occurrence를
  자르거나 hash만 저장하지 않는다. 정상 run에서 병목이 확인되면 source/evidence obligation을 canonical ID로
  무손실 partition하고 exact-once merge하는 별도 책임 슬라이스로 다룬다.
- ownerless unresolved는 generation closure에 보존되며, 현재 APAS actual ownerless population은 0이다.
