# Common evidence IR 검증 원장 — 2026-08-21

## 1. 실행 전 봉인

이 절은 새 Parser의 실제 APAS evidence 출력과 집계값을 보기 전에 고정했다. 대상 원문은
`분석대상모음/framework/c/apascrt00010t02_20260406`의 C/H 23파일 전수다. 기존 보존 `analysis`는
수정하지 않고 Parser 격리 workspace에 materialize한다. 결과 원장은 spec 131
`_runs/framework/common-evidence-ir-20260821.json`에만 쓴다.

자동 회귀와 별도로 다음 모집단을 결과 비의존 규칙으로 봉인 직접 판정한다.

1. 각 source의 code-point source order상 첫 call과 마지막 call.
2. `presence.status != active`인 call 전부.
3. 줄바꿈을 포함하는 call 전부.
4. 동일 source range에 둘 이상의 call fact가 있는 경우 그 fact 전부.

실제 최종 판정 전 seal 보정: 첫 전수 원장은 규칙 2가 constant 조건의 **active else** 호출을 포함하지
않는다는 절차 결함을 드러냈다. 그 원장은 탐색 실행으로 무효화하고, 결과의 좋고 나쁨과 무관하게 각
`conditional_region` 안의 call 전부를 5번 모집단으로 추가한다. 부분 선택이 아닌 조건 영역 전수이므로
active/inactive/conditional을 모두 포함한다. 이 보정 뒤 생성한 원장만 최종 3층 판정에 사용한다.

5. exact range 시작점이 어떤 `conditional_region` 안에 있는 call 전부.

각 봉인 fact는 원문을 가리고 `sourceId`, grammar rule, exact call/callee/argument slice와 range만으로
우선 `호출 발생 사실`, `callee 경계`, `argument 순서`, `presence 판정 가능성`을 판정한다. 그 다음 원문
range를 열어 누락·과잉·좌표·조건 provenance를 대조한다. 호출의 프로젝트 target 의미는 Analyzer
resolution owner이므로 이 단계에서 추측하지 않는다.

## 2. 사전 계약과 실패

- 최초 red: `SemanticEvidenceIrContractTest` 4/4 실패. envelope 없음, Unicode/full-callee range 없음,
  canonical fact ID 없음, `#if 0` else 소실을 각각 재현했다.
- 구현 중 추가 red: Java initializer/anonymous-class constructor는 현 grammar가
  `classInstanceCreationExpression`이 아니라 `unqualifiedClassInstanceCreationExpression`으로
  평탄화해 canonical call 후보가 0이었다. Python의 이름 없는 callable도 `callName != null` 조건으로
  누락됐다.
- green 계약은 source hash·strict decode·Unicode code-point half-open range·canonical ID,
  C/Java/Python grammar call 경계, 전 frontend envelope, C directive-token presence와 명시 degraded다.
  exact source는 파일 envelope에 한 번만 두고 fact는 그 source의 range를 참조한다. decoded source hash와
  canonical range가 slice를 이미 결박하므로 fact마다 source text/hash를 반복하지 않는다.
  Oracle/PostgreSQL call coverage는 DBMS effective-CodeNode 구현 전까지 `unsupported`이며 빈 성공이 아니다.

## 3. actual 전수 회계

최종 Parser `48de7f82f310c522d35edc697ba53d7857cd4264`에서 APAS C/H 23파일을 두 번씩 parse했다.
두 실행의 AST JSON은 파일별 byte-identical이고,
source inventory와 모든 fact/call ID를 source order로 ledger hash에 봉인했다.

| 항목 | 값 |
|---|---:|
| source files | 23 |
| legacy `FUNCTION_CALL` | 12,113 |
| common IR `call` facts | 12,113 |
| active / inactive / conditional·unknown calls | 12,080 / 33 / 0 |
| named / constructor / expression callees | 12,113 / 0 / 0 |
| conditional regions | 17 |
| diagnostics / ANTLR recoveries | 0 / 0 |
| deterministic replay | true |
| all-fact ledger SHA-256 | `84df381a6544090133e5709add4df43b6611d7278b5fbe9ec5dd5a58bb23cd56` |
| call ledger SHA-256 | `5fa3d09666148d671616aadc8d043f17c9192d74eb062631cba1178ccf8639a0` |

기존 12,112는 `#if 0` 안 33개 call을 active로 세면서 실제 active else call 1개를 버린 값이다. 새 total
12,113은 inactive도 삭제하지 않고 evidence로 보존한 occurrence 모집단이며, active view는
`12,112 - 33 + 1 = 12,080`으로 닫힌다. unknown `PFM_DBG` 조건 영역에는 call occurrence가 없어 call의
conditional·unknown 수는 0이지만 그 region fact와 owner completeness는 남는다.

call completeness의 `population`은 실제 관측 occurrence만 센다. unresolved conditional region 수를
“아직 발견하지 못한 call 수”로 발명하지 않고 `unresolvedScopeFactIds`에 해당 region fact ID 전부를
기록해 active-set 불확실성을 별도로 표현한다.

실행 원장:

- `_runs/framework/common-evidence-ir-20260821.json`

## 4. 봉인 직접 의미 판정

봉인 규칙의 합집합은 526개다. 중복 포함 모집단 표시는 first 23, last 23, multiline 402,
non-active 33, conditional-region 내부 81이며 canonical fact ID로 합집합을 만들었다. 합집합의 presence는
active 493, inactive 33이다.

원문을 가린 1차 판정과 range를 연 2차 대조에서 526/526 모두 실제 call occurrence, full callee boundary,
ordered argument boundary가 일치했다. conditional-region 내부 81/81도 source region과 presence가
일치했다. `displayPreview`는 canonical payload가 아니라 봉인 판정용으로 exact source range에서 파생한
표시물이다. 프로젝트 target resolution은 Analyzer owner이므로 이 판정에서 맞았다고 주장하지 않는다.

## 5. 자동 회귀

최종 상태에서 Parser 전체 회귀는 48 suites, 168 tests, failures 0, errors 0, skipped 10이다. skipped에는
actual/benchmark opt-in이 포함된다. 계약 테스트는 supplementary Unicode 앞의 range, full callee,
nested/multiline/anonymous call, same-range identity, strict decode, conditional provenance, completeness
회계를 포함한다. C unknown branch를 첫/대체 branch로 추측하던 `c.alternate-preprocessor-branches.v1`
recovery는 red로 오판을 재현한 뒤 제거했고, build configuration이 없으면 partial로 남긴다.
추가 red는 parser recovery가 call completeness를 complete로 세탁한 경로, workspace 밖 full-file parse가
evidence를 생략한 경로, nested C condition이 effective parent presence를 잃은 경로를 각각 재현하고 green으로
전환했다.

## 6. like-for-like 성능 기준선

격리한 Parser `e5fbf6b2e2a20dfc1bf42a665d19960715a6e2dd`와 현재 구현을 같은 23파일·파일별 2회 parse·동일 JSON
serialization harness로 비교했다. benchmark 자체는 의미 모집단을 sample/cap하지 않는다.

| 항목 | baseline | current | 변화 |
|---|---:|---:|---:|
| call occurrences/facts | 12,112 | 12,113 | +1 exact occurrence |
| output bytes | 2,536,164 | 9,844,733 | +288.17% |
| two-pass wall ms | 2,948 | 4,317 | +46.44% |
| peak heap bytes | 216,242,240 | 228,634,496 | +5.73% |

현재 수치는 grammar-owned `calleeKind/terminalName`까지 포함한다. 최초 반복-text 시제품은 output 약
21.91 MiB, peak heap 약 339.1 MiB였다. 이를 source-once + range reference,
grammar/presence table, streaming fact serialization로 교체하고 legacy node의 임시 중복 call text/argument/
offset 필드를 제거해 의미 fact를 줄이지 않고 최종 8.83 MiB, 249.63 MiB로 낮췄다. baseline보다 여전히 큰
값은 새 exact source/evidence ledger의 비용이다. 이 한 회차
측정만으로 성능 우월을 주장하지 않으며, Analyzer consumer 통합 때 end-to-end wall/heap과 graph/prompt
중복 제거 효과를 다시 비교한다. 숫자 cap이나 sampling으로 낮추지 않는다.

성능 원장:

- `_runs/framework/common-evidence-ir-baseline-e5fb-20260821.json`
- `_runs/framework/common-evidence-ir-performance-current-20260821.json`

## 7. representation 근거와 미검증

source-once + range reference는 임의 압축이 아니다. OASIS SARIF 2.1.0 text region은 character offset과
length로 완전 지정할 수 있고 snippet은 optional이라고 정의한다. ECMA-426 source map도 source content를
한 번 두고 mapping이 그 source를 참조하는 구조다. Robo는 이를 그대로 복사하지 않고 source binding과
exact range 표현의 교차 근거로만 사용했다.

- OASIS SARIF 2.1.0: https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html
- ECMA-426 Source Map Format: https://tc39.es/ecma426/

아직 미검증인 것은 Analyzer의 language-neutral consumer, routine/effective-CodeNode owner mapping,
Neo4j/API/MCP actual replay, Oracle/PostgreSQL call fact 구현이다. 따라서 이 단계의 합격 범위는 Parser가
C/Java/Python call 및 C conditional-region evidence를 무손실 발행한다는 것까지이며, 전체 파이프라인
정확도 합격으로 확대하지 않는다.
