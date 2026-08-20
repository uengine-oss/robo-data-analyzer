# Analyzer common call evidence consumer 검증 원장

**Date**: 2026-08-21
**Parser**: `4f2154ed1b228df7c2d45cd6ded35fff599602bd`
**Analyzer pre-commit base**: `e298cae8f4cb0c35a48b18cad68e0445fe0c7f77`

## 1. 책임 경계

이 슬라이스는 Parser v1 `call` fact를 Analyzer가 언어 중립으로 검증·소비하고 Framework의 enclosing
routine에 귀속하는 데까지만 승인한다. DBMS owner, macro/literal/symbol, Neo4j publish, API/MCP 결과는
이 슬라이스의 합격 주장에 포함하지 않는다.

생산·소비 경로는 다음과 같다.

```text
exact source bytes
-> Parser grammar/listener call + conditional-region facts
-> FILE evidence envelope
-> Analyzer envelope/hash/decode/range/ID/completeness validator
-> legacy AST traversal population/order cross-check
-> Framework enclosing routine owner mapping
-> call occurrence + unresolved ledger
```

Analyzer는 raw source에서 call terminal이나 wrapper argument를 다시 뽑지 않는다. `named|constructor`의
terminal은 Parser grammar가 발행한 `terminalName`, expression call은 nullable terminal을 그대로 쓴다.
wrapper target에 필요한 literal/symbol evidence가 아직 없으면
`insufficient_wrapper_argument_syntax_evidence`로 남기며 target을 발명하지 않는다.

## 2. fail-closed 계약과 red→green

validator는 다음을 Neo4j mutation 전에 확인한다.

- project-relative `sourceId`와 실제 source path 일치, workspace escape 금지
- raw byte SHA-256, Parser가 선언한 strict encoding decode, exact decoded text와 UTF-8 hash 일치
- Unicode code-point / half-open char range와 source 범위
- canonical fact ID 재계산, file/global 중복 0
- grammar/presence table reference, nonempty call/callee range, ordered non-overlapping argument range
- grammar-owned callee kind/terminal 규칙
- kind 전종 completeness matrix와 `population = emitted + explicitlyUnresolved`
- `reason == reasons[0]`, unresolved scope는 같은 envelope의 `conditional_region` fact만 참조
- Parser compatibility AST의 call population/traversal/line/terminal과 canonical facts가 exact-once 일치

formal red는 evidence module 부재, hash/ID/range/completeness corruption, expression terminal 위조,
legacy call population 누락, compatibility reason 불일치, call fact를 conditional scope로 위조한 경우를
각각 실패시켰다. EUC-KR 반례는 Analyzer의 선행 UTF-8 가정을 제거하고 Parser가 검증한 exact decoded text를
공유 source cache에 등록해 같은 bytes/text를 downstream이 재사용하도록 고정했다.

최종 자동 회귀는 집중 `28 passed, 10 subtests`, actual opt-in `1 passed`, 권위 unittest
`1121 tests OK, skipped 6`, 전체 pytest `1194 passed, 7 skipped, 367 subtests`다. `compileall`과
`git diff --check`도 오류 0이다.

## 3. actual APAS 결정론 전수 회계

Parser export harness는 source/AST/diagnostic 각 23개를 허용된 spec 131 framework run 아래에만 썼다.
Analyzer는 같은 fixture를 두 번 read-only 소비했다.

| 항목 | 값 |
|---|---:|
| source files | 23 |
| Analyzer CodeNodes | 7,921 |
| call facts / unique IDs | 12,113 / 12,113 |
| duplicate IDs | 0 |
| Framework routine owner resolved | 12,113 |
| active / inactive | 12,080 / 33 |
| named / constructor / expression | 12,113 / 0 / 0 |
| call completeness complete / partial | 22 / 1 |
| deterministic replay | true |
| call fact ledger SHA-256 | `5fa3d09666148d671616aadc8d043f17c9192d74eb062631cba1178ccf8639a0` |
| owner ledger SHA-256 | `1a4e512d008a65855d3f460bb8c454633ef33f220b84df30eb7ddcebb8d66f21` |
| resolution outcomes | edge 869 / excluded 11,126 / preserved 33 / unresolved 85 |
| resolution ledger SHA-256 | `2ba765b1eebd16de73890ef496f423c0a9103884333d6d2679e5c028c9521c5e` |
| first / repeated pass | 2.623156s / 2.589853s |
| first / repeated tracemalloc peak | 37,558,699 / 37,620,987 bytes |

partial 1파일은 build configuration 부재를 `insufficient_missing_build_configuration`과 해당 conditional
region fact ID로 보존한 결과다. 관측 call 1개를 누락시키거나 미지 call 수를 발명하지 않는다.
resolution 12,113행도 source order canonical ID와 exact-once 일치한다. excluded 11,126은 stdlib 6,918,
framework runtime 4,132, 아직 Parser macro evidence로 교체 전인 macro 76이다. unresolved 85는 active
`mpfm_dlcall` wrapper이며, literal/symbol argument evidence 없이 기존 한 줄 regex target 85개를 재생산하지
않은 명시 degraded 결과다. inactive wrapper 1개는 전체 inactive 33에 포함되어 먼저 preserved된다.

실행물:

- `_runs/framework/analyzer-call-consumer-parser-export-20260821.json`
- `_runs/framework/analyzer-call-consumer-fixture-20260821/`
- `_runs/framework/analyzer-call-consumer-accounting-20260821.json`

## 4. 봉인 직접 의미 판정

Parser 단계에서 source-order가 고정된 봉인 합집합 526건을 Analyzer 결과와 대조했다. 526/526의 canonical
ID, full call/callee/argument range, callee kind/terminal, presence가 일치했고 각 call line은 귀속 routine의
start/end line 안에 있었다.

그중 선행 봉인 순서 첫 40건은 `factId` 선택 후 Analyzer owner 결과를 열어 주 에이전트가 직접 판독했다.
40/40 모두 실제 enclosing routine 이름·signature·line 범위와 일치했다. 이 판정은 Framework routine owner
정확성에 한정한다. call target 의미, DBMS effective CodeNode owner, Neo4j/API/MCP 정확성을 대신하지 않는다.

## 5. 남은 의무

- C function-like macro 판정은 아직 Analyzer `_FUNCTION_MACRO_RE` compatibility 경로다. Parser macro
  evidence가 완성될 때까지 승인 범위 밖이며 C-055 pending이다.
- wrapper target은 literal/symbol argument evidence 전까지 명시 unresolved다.
- DBMS는 common envelope를 검증·보존하지만 call owner 소비는 구현 전이다. 따라서 nonempty DBMS call
  facts는 버리지 않고 즉시 실패한다. empty/unsupported matrix만 통과한다.
- Neo4j generation publish와 API/MCP occurrence/unresolved projection actual replay는 후속 슬라이스다.
- 따라서 이 결과를 전체 Analyzer 의미 정확도 또는 성능 우월 주장으로 확대하지 않는다.
