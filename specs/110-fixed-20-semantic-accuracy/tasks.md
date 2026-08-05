# 작업

## 범위·정답지 복원

- [x] 2026-07-27 canonical handoff와 2026-07-28 폐기 방식의 경계를 확인한다.
- [x] 기존 71함수 all-field 원장과 66개 FILE/TABLE/COLUMN 선정표를 확인한다.
- [x] 고정 20함수는 전체 검증의 빠른 회귀 표본이며 219개는 Rule/Example claim 분모임을 복원한다.
- [x] 현재 Pydantic response model과 prompt를 기준으로 LLM 산출물 목록을 다시 열거한다.
- [ ] 함수·모듈·테이블·컬럼·용어·컴포넌트별 field ownership / gold / selection 매트릭스를 확정한다.
- [ ] 기존 정답지가 없는 신규 산출물은 정확도 분모와 섞지 않고 gold gap ledger로 분리한다.

## 현재 실행 추출

- [x] 공식 CLI baseline 및 coverage-ledger 재실행을 완료한다.
- [x] 416함수 review packet과 고정 20함수 packet을 생성한다.
- [x] 현재 Neo4j에서 FILE 23, TABLE 7, canonical COLUMN 36개가 조회되는지 확인한다.
- [x] 고정 20함수 packet에 stereotype, table_insights, affected table/op, prompt/log provenance를 보강한다.
- [x] canonical 66 schema packet과 GLOSSARY/component packet을 현재 run hash에 묶어 저장한다.

## 직접 의미 판정

- [x] coverage-ledger 수정 전 Rule/Example 219개 직접 판정을 완료한다.
- [x] context-closed 재실행 Rule/Example 219개를 1차 직접 판정하고 누락 원인군을 확정한다.
- [x] 고정 20함수의 summary를 Coverage/Faithfulness/Extra로 1차 판정한다.
- [x] 고정 20함수의 search_text를 Coverage/Faithfulness/Extra로 1차 판정한다.
- [x] 고정 20함수의 logical_name과 stereotype을 1차 판정한다.
- [x] 고정 20함수의 QUESTION과 domain_terms를 1차 판정한다.
- [x] 고정 20함수의 table_insights와 affected table/op를 최종 판정한다.
- [x] FILE 23개의 module summary/logical_name/stereotype을 판정한다.
- [x] TABLE 7개와 COLUMN 36개의 logical_name/analyzed_description을 판정한다.
- [x] 최종 GLOSSARY/component는 gold가 있는 항목의 의미 정확도와 전체 구조·근거·write를 분리 판정한다.

## 원인 추적

- [x] 공통·분할 prompt의 상호충돌과 dead output을 수정 전 감사 문서로 고정한다.
- [x] 변수·상수·실제값·참조 흐름과 prompt context 완전성을 별도 직접 검증 축으로 고정한다.
- [x] field별 실패를 source/gold, prompt input, prompt 계약, model response, parser,
      fan-out/handoff, merge/filter, graph write/read-back 단계로 일괄 분류한다.
- [x] 고정 20의 gold evidence 실행 줄 219/219가 Rule prompt에 실제 포함됐음을 확인한다.
- [x] 참조 상수값 전달 후 해석 실패와 framework runtime 계약 미전달을 분리 판정한다.
- [x] 핵심 실패군의 최초 LLM prompt/response 또는 로직 위치를 evidence와 함께 남긴다.
- [x] context compaction/callee handoff와 완전 입력 이후 model response 실패를 구분한다.
- [x] Rule/Example cardinality, domain term, table description 과밀이 정확도와 폭주에 미친 영향을 분리한다.

## 범용 수정·회귀

- [x] Rule coverage ledger와 Rule 경계 계약의 1차 범용 prompt 수정을 적용한다.
- [x] 집중 테스트와 prompt hash 검증을 통과한다.
- [x] 1차 field 원인 분류에서 확인된 범용 수정만 일괄 적용한다.
- [x] 특정 함수·파일·테이블·코드값 hard-code 및 별도 LLM 재작성/심판이 없음을 코드·테스트로 감사한다.
- [x] 같은 고정 20함수와 canonical schema 표본으로 재실행·직접 재판정한다.
- [x] 반대 성격 표본과 전체 graph 구조/write 회귀를 확인한다.

## 폭주·상한 부가 검증

- [x] 현재 재실행에서 whitespace/repetition guard 중단 7건을 확인한다.
- [x] 연속 whitespace 조기중단 guardrail을 concern 단위로 구현·테스트한다.
- [x] SGLang constrained JSON whitespace 옵션이 현재 서버에서 꺼져 있음을 확인하고
      클라이언트 방어 경계를 문서화한다.
- [x] Rule/Example/domain_terms/QUESTION cap hit, truncation, 필수 obligation 누락을 함께 집계한다.
- [x] 실제 정확도 관측을 근거로 soft recommendation과 hard emergency cap을 최종 권고한다.

## 완료

- [x] `verification.md`에 run ID, hash, field별 수치, 실패 ledger, 원인, 수정, 회귀 결과를 기록한다.
- [x] 전체 필드 검증이 끝나기 전 Rule/Example 부분 결과를 전체 정확도로 보고하지 않는다.
