# C-083 expression slot closure evidence (2026-08-24)

## Decision

Parser의 구조식 fact를 Analyzer 내부 routine/semantic slot에 exact-once로 연결한다. Parser는
구문·구조만 생산하고 Analyzer가 expression variant와 slot 관계를 소유한다. source 정규식 재파싱,
binding/type/linkage 추측, 별도 unresolved graph node/label, 절단 상한, skip, fallback은 사용하지 않는다.

## Sealed actual

| 항목 | 값 |
|---|---:|
| source | `apascrtu0010t02.c` |
| source SHA-256 | `edd512c6478cb31f7a0f396f92ec850659f2720d25c553b8a4f4e42ba330d126` |
| Parser artifact SHA-256 | `94c5bbe98bfbf51b6dabc564c73361c417fb53d32b4308689a8988ab68b6a831` |
| resolved ledger SHA-256 | `a365bf42bdca47e754ba0c3f52be733272978ee0ac1f1f9f95839e4e6ce45db6` |
| routine owners | 46 |
| structural / neutral / discriminated | 578 / 578 / 578 |

## Cause closure

| 단계 | 건수/좌표 | 판정 |
|---|---:|---|
| routine ownership | 578/578 | nearest routine parent chain, orphan 0 |
| 최초 slot 참조 | 575/578 | residue 3 |
| 첫 residue | line 544 | SWITCH subject `rc` |
| 나머지 residue | lines 1366, 1833 | 같은 SWITCH subject 관계 누락 |
| 수정 후 slot 참조 | 578/578 | residue 0 |

Parser는 SWITCH subject `condition`과 CASE `case_value`를 모두 생산했다. Analyzer planner가 CASE guard에
case value만 넣어 subject를 소비하지 않은 것이 실제 producer→consumer 원인이었다. Parser의 parent edge와
role만 사용해 CASE guard에 두 구조식을 연결했다. keyword/source 재파싱은 없다.

## Prompt projection and cost

| projection | 46 routine 추정 token | baseline 대비 |
|---|---:|---:|
| baseline (expression closure 없음) | 175,447 | - |
| full canonical facts | 358,201 | +104.11% — 폐기 |
| compact tree + full EvidenceFact | 309,735 | +76.49% — 폐기 |
| single closure + canonical evidence IDs | 212,027 | +20.81% — 폐기 |
| request-local Q + role/tree only | 196,514 | +12.01% — 채택 |

Canonical frame은 exact line/character range와 전체 중첩 provenance를 보존한다. 모델에는 각 slot이 실제로
참조하는 표현식만 `Q…: [role, compact tagged tree]`로 보낸다. 총 증가는 21,066 추정 token이며 최대
단일 routine은 `c000_main_proc` +2,214다. 건수 상한이나 절단은 없다.

## Verification

- actual expression/routine/slot exact-once: 578/578, residue 0
- focused regression: 40 passed, 6 subtests passed
- staged-only full regression: 1,278 passed, 7 skipped, 367 subtests passed
- prompt hashes: 16/16 byte-identical
- semantic frame contract SHA-256:
  `501bfacb3542d2fbfb6271dab35c3e718a678b4c6fb892c5feab6dcbdfb755c4`

## Non-promotion boundary

이 결과는 LLM semantic input의 구조화·소유권·closure 완전성 검증이다. Python 구문 성공, Rule/Example
의미 정확도, 제품 합격을 주장하지 않는다. 특히 기존 deterministic Rule은 CASE 조건을 아직 subject와
결합하지 않아 `rc == RC_NFD` 대신 `RC_NFD`로 남는다. 이는 다음 Rule/Python projection 슬라이스의
확정된 시작점이다.
