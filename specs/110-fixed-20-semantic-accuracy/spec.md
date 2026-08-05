# spec 110 — 대표 노드 전체 의미 정확도 검증과 범용 개선

## 목적

2026-07-27 확정 정답지와 대표 노드 선정표를 기준으로 현재 Analyzer를 다시 실행하고,
LLM이 생성한 의미 속성을 필드별로 직접 비교한다. 낮은 정확도의 원인을 prompt 입력부터
parser, merge, graph write까지 추적하고, 특정 함수·고객·코드값에 종속되지 않는 범용
수정만 적용한 뒤 같은 표본으로 재검증한다.

2026-07-28의 별도 LLM judge 방식은 이번 검증의 판정 수단으로 사용하지 않는다.
스크립트는 추출·정렬·무결성·집계만 담당하며 의미 판정은 gold, source, 실제 출력,
LLM prompt/response log를 놓고 Codex가 직접 수행한다.

## 범위 정정

`고정 20함수 / active gold claim 219개`는 전체 검증의 전부가 아니다. 이는 고위험 함수의
Rule/Example 원자 주장 회귀 분모다. 전체 의미 정확도 검증은 아래 두 층을 함께 가진다.

1. 함수 고위험 회귀 표본
   - 고정 20함수를 매 재분석에서 동일하게 추출한다.
   - `summary`, `search_text`, `logical_name`, `stereotype`, `RULE`, `EXAMPLE`,
     `QUESTION`, 함수별 `domain_terms`를 모두 검증한다.
   - Rule/Example은 219개 active gold claim을 원자 단위로 직접 대조한다.
   - `table_insights`의 테이블 귀속, role, usage summary, column 의미와
     Example의 affected table/op도 source·scanner R/W·gold 관계를 기준으로 검증한다.

2. 함수 하나에 귀속할 수 없는 별도 노드 표본
   - FILE 23개: module `summary`, `logical_name`, `stereotype`.
   - TABLE 7개: `logical_name`, `analyzed_description`.
   - COLUMN 36개: `logical_name`, `analyzed_description`.
   - 최종 GLOSSARY 및 component 이름은 현재 파이프라인에서 새로 확인된 LLM 산출물이므로
     별도 선정표와 source-grounded gold가 있는 항목만 정확도 분모에 편입한다.
     gold가 아직 없는 항목은 정확도 숫자로 가장하지 않고 구조·근거·저장 검증과
     `gold gap`으로 분리 보고한다.

기존 71함수 대표 벤치마크와 66개 스키마 노드 원장은 표본 선정 및 필드 계약의 canonical
근거다. 이번 반복 개선의 빠른 회귀 루프는 고정 20함수를 사용하되, 전체 필드 소유권과
별도 노드 검증을 버리지 않는다.

## 권위 입력

- `docs/핸드오프/2026-07-27-analyzer-대표기준노드-의미판정-고정핸드오프.md`
- `분석대상모음/framework/c/apascrt00010t02_20260406/gold/`
- `.../2026-07-25-codex-meaning-judge/scratchpad/core-benchmark-selection.json`
- `.../scratchpad/core-function-verdict-ledger.jsonl`
- `.../scratchpad/core-schema-selection.json`
- `.../scratchpad/core-schema-verdict-ledger.jsonl`
- `.../scratchpad/field-gold-schema.example.json`
- `.../scratchpad/rerun-20260728-high-risk-20-selection.json`

Temp scratchpad 원본은 읽기 근거로만 사용한다. 현재 실행의 산출물과 새 판정 원장은
`_runs/` 및 이 spec의 `verification.md`에서 별도 hash로 고정한다.

## 필드별 판정 계약

모든 필드는 합산 점수 하나로 뭉치지 않고 소유권에 맞게 독립 판정한다.

- Coverage: 해당 필드가 반드시 담아야 하는 gold obligation을 충족하는가.
- Faithfulness: 실제 출력의 각 원자 주장이 source/gold에 의해 지지되는가.
- Extra: Coverage obligation과 매칭되지 않는 출력 원자 주장을 supported/unsupported로 구분한다.
- Exact: `stereotype`처럼 폐쇄 taxonomy인 값은 문자열 정확 일치로 별도 측정한다.
- Structure/write: 출력 JSON, parser 결과, merge 결과, Neo4j 최종 속성·노드·관계가
  의미 손실 없이 일치하는지 측정한다.

요약과 검색문은 같은 gold를 같은 밀도로 반복하는 필드가 아니다.

- `summary`: 목적, 주요 처리, 핵심 정상/예외 효과를 1~3문장으로 설명한다.
- `search_text`: 의미 검색에 필요한 구체 조건, 상태/오류 코드, 테이블·컬럼·호출 이름,
  값에 따른 결과 차이를 source-grounded하게 담는다.
- `logical_name`: 다른 루틴과 구별되는 업무 책임을 짧게 나타낸다.
- `RULE`: 의미 있는 조건과 관찰 가능한 결과를 함께 가진 규칙이다.
- `EXAMPLE`: 소속 Rule을 검증하는 최소 시나리오이며 Rule에 없는 정책을 추가하지 않는다.
- `QUESTION`: 제공된 전체 증거로 답할 수 없고 분석 품질에 중요한 미해결점만 허용한다.
- `domain_terms/GLOSSARY`: 코드만으로 뜻이 자명하지 않은 내부 코드·상태·업무 용어만
  실제 evidence와 함께 허용한다.
- `table_insights/TABLE/COLUMN`: 테이블별로 묶고, 물리 이름·직접 R/W·컬럼 용도와
  업무 의미를 혼동하지 않는다.

의미가 비슷하다는 이유로 조건 범위를 넓히거나 좁힌 문장은 동치가 아니다. 예를 들어
“주문 시 오류코드를 반환한다”와 “주문 시 미정 상태는 보류하고 나머지만 오류코드를
반환한다”는 사실성·조건 경계·결과가 다른 주장으로 판정한다.

## 실행·추적 요구사항

1. 공식 CLI로 23개 source를 실제 Neo4j `test` 데이터베이스에 분석한다.
2. run ID, prompt tree hash, source/gold hash, 호출 수, 실패·재시도, whitespace flood 여부를 기록한다.
3. 매 실행 후 고정 20함수와 canonical FILE/TABLE/COLUMN 표본을 자동 추출하되 의미 판정은 자동화하지 않는다.
4. 각 판정에는 field, gold obligation/output atom, source evidence span, 실제 output ID를 남긴다.
5. 실패는 관측 가능한 최초 원인 단계로 분류한다.
   - source/gold 문제
   - prompt input 누락·왜곡·context compaction
   - prompt 계약 충돌·과밀·예시 편향
   - model response 누락·모순·폭주
   - structured-output/parser 검증
   - fan-out/handoff/merge/filter
   - graph write/read-back
6. 원인을 확인하기 전에 재실행하거나 임시 후처리로 결과를 맞추지 않는다.
7. gold 문구 복사, 다른 필드 복사, 별도 LLM 재작성·심판, 임계값·유사도 눈속임,
   특정 함수·파일·테이블·코드값 hard-code, happy-path 추가를 금지한다.
8. 수정은 최초 원인에만 적용하고 같은 표본과 반대 성격 표본으로 회귀 검증한다.

## 생성 상한과 폭주 안전

상한은 정확도를 대신하는 목표 개수가 아니라 구조 비상 상한이다. 고정값을 먼저 정답으로
정하지 않고 실제 분포, truncation/cap hit, 누락된 필수 obligation을 함께 보고 결정한다.

- `domain_terms`: hard max 10.
- Rule: hard max 48을 비상 상한으로 관찰한다. 32개는 실제 MMS 함수의 뒤쪽 필수
  분기를 잘랐으므로 정확도 관측 결과에 따라 폐기했다.
- Rule별 Example: 현재 hard max 8을 비상 상한으로 관찰한다.
- QUESTION: 현재 hard max 15를 관찰한다.
- 보통 Example 1~2개를 권장할 수 있으나 필수 경계·fallback을 버리도록 강제하지 않는다.
- 상한 도달은 조용히 절단하지 않고 품질 신호로 기록한다.
- JSON 종료 전 연속 whitespace가 임계치를 넘으면 해당 concern만 신속 중단하고,
  성공한 다른 concern은 재생성하지 않는다. 부분 JSON을 정상 결과로 복구하지 않는다.

## 현재 실행 증거

- coverage-ledger 수정 전 비교 실행:
  - run ID `20260730_060231_a98ffe35a7f6`
  - Rule `167/219`, Example `169/219`
  - 이 수치는 전체 필드 정확도가 아니라 Rule/Example 부분 결과다.
- coverage-ledger prompt 적용 실행:
  - run ID `20260730_062658_3b1c50c4c38d`
  - 1,418 calls, 423.8초, cache 70.2%
  - whitespace flood 0
  - 비고정 함수 1건 `LengthFinishReasonError`, concern 단위 1회 재시도 완료
  - 416 함수 review packet 및 고정 20함수 packet 추출 완료
  - FILE 23, TABLE 7, COLUMN canonical 36개를 현재 graph에서 다시 추출 가능함을 확인

위 실행의 Rule/Example 원인 보고서는 전체 검증 완료 보고서가 아니라 부분 진단으로만
보존한다.

## 완료 기준

- 현재 LLM 산출물 전체 목록과 필드 소유권, gold 존재 여부, 선정 표본이 한 매트릭스로 고정된다.
- 고정 20함수의 모든 함수 필드와 canonical 스키마 노드가 누락 없이 직접 판정된다.
- 각 낮은 정확도는 prompt/logic/parser/merge/write 중 최초 원인 증거와 연결된다.
- 범용 수정 전후의 field별 Coverage/Faithfulness/Extra/Exact/structure 수치가 비교된다.
- cap과 whitespace guardrail 권고는 실제 정확도·누락·폭주 관측에 근거한다.
- 테스트, prompt hash, 재실행, Neo4j read-back이 모두 통과한다.
