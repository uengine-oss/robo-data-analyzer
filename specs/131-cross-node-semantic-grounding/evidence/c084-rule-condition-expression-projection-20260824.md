# C-084 Rule condition expression projection

**Date**: 2026-08-24

**Scope**: Analyzer only; sealed SKT actual one-file accuracy slice

**Decision**: Parser structural evidence를 Analyzer-owned language-neutral Rule/Example 의미로 결정론 투영한다.

## Sealed inputs

| Input | SHA-256 |
|---|---|
| `apascrtu0010t02.c` | `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126` |
| Parser artifact | `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831` |
| resolved expression ledger | `a365bf42bdca47e754ba0c3f52be733272978ee0ac1f1f9f95839e4e6ce45db6` |

새 DB를 만들지 않았고 Neo4j write·전체 SKT 재분석·Strategy A 재실행은 하지 않았다.

## Confirmed cause and boundary

- Parser는 `SWITCH condition=rc`, `CASE case_value=RC_NFD`와 각각의 exact source range를 이미
  생산했다.
- 기존 Analyzer Rule producer가 `C546=RC_NFD`만 materializer에 전달하여 selector와 label의
  관계를 잃었다. 기존 결과는 `F("RC_NFD")`, 계약상 결과는
  `F("rc") == F("RC_NFD")`이다.
- Parser는 구조 사실만 유지한다. Analyzer adapter가 Parser parent edge와 role로 `case_match`를
  만들고, 언어 중립 core가 exact source atom과 검증된 operator shell을 렌더링한다.
- source 문자열 정규식 재파싱, binding/type/linkage 추측, framework/SKT 이름 특화 로직은 없다.
  지원하지 않는 복합 expression variant는 원문 전체를 exact `F(...)` atom으로 보존한다.
- bundle이 제공된 경로에서 condition relation이나 expression closure가 하나라도 없으면
  fail-closed한다. bundle이 없는 언어는 기존 계약을 유지하며 부분 projection으로 위장하지 않는다.

## Actual result

| Ledger | Result |
|---|---:|
| structural / semantic / resolved expression | 578 / 578 / 578 |
| Rule condition relation exact-once | 192 / 192 |
| deterministic Rule | 362 |
| synthesized Example | 89 |
| baseline 대비 조건 표기 변경 | 253 |
| 첫 변경 좌표 | `apascrtu0010t02.c:300` |
| 집중 검증 | 113 passed + 17 subtests |
| actual 포함 집중 검증 | 89 passed + 17 subtests |
| staged-only 전체 회귀 | 1,285 passed / 7 skipped / 367 subtests |

직접 의미 판정 좌표:

- `R546`: `F("RC_NFD")` → `F("rc") == F("RC_NFD")`.
- `R549`: 기존 structural sequence guard와 exact case relation을 함께 보존하여
  `not (F("rc") == F("RC_NRM")) and F("rc") == F("RC_ERR")`.
- `R549` Example `given`은 Rule의 두 조건을 동일한 Python AST 의미로 투영하고, `then`은 Rule의
  효과와 byte-for-byte 같은 목록이다.
- C084는 Rule/Example 구조 의미를 닫은 것이며 LLM business meaning 정확도나 전체 제품 합격으로
  승격하지 않는다.

## Input / instruction / output audit

| Layer | Current fact | Verdict |
|---|---|---|
| 입력 | target별 canonical evidence ID, exact 좌표, expression tree, slot별 source closure만 전달 | 구조화·중복 방지·exact-once는 강함. 모든 언어의 semantic adapter가 완결됐다는 뜻은 아님 |
| 지시 | semantic-frame 계약이 slot role, evidence refs, 충분/부분/불충분 판정, 소유권 금지와 self-check를 명시 | 길을 잃을 여지를 크게 줄였으나 model meaning 문장 자체의 결정론은 보장하지 않음 |
| 출력 | request-local required slot key를 동적 Pydantic schema로 exact-once 강제하고 extra field 금지 | 바깥 빵틀은 닫힘. legacy summary는 검증된 slot meaning을 정본 순서로 합성 |
| Rule/Example | LLM 0회, closed condition/effect IR에서 결정론 생성·채점·audit replay | C084 범위에서 구조 의미 빵틀이 닫힘 |

## Next efficient step

추가 문맥은 원문 전체를 늘리는 방식이 아니라 현재 slot의 결손을 증명하는 canonical fact만
dependency closure에 추가한다. 우선순위는 exact binding/def-use, call receiver/argument와 callee
availability, external boundary 상태다. Parser가 생산하지 않은 최종 의미를 이름 모양으로 추측하지
않는다. 작은 실제 좌표에서 producer→consumer를 닫은 뒤 관련 변경이 모두 끝날 때만 SKT 23파일
전체 성능·정확도 회계를 한 번 수행한다.
