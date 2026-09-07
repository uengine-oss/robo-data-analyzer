# 현재 상태 - 2026-09-07

## 완료 상태

- 세 함수 의미 concern의 system prompt를 byte-identical 단일 정본으로 통합했다.
- Rule 입력은 effect에 결속된 exact source만 담고, 128 effect를 넘는 요청은 완전한 row 단위로
  결정론 분할한다. 분할 전후 row와 atom은 exact-once이고 성공 분할은 재호출하지 않는다.
- Qwen 배포 서버의 실제 실행 상한과 8/16/24/32 처리량을 비교해 client 동시성을 16으로 고정했다.
- Neo4j 관계 공개와 readback은 label별 batch와 labeled endpoint lookup으로 복합 제약을 사용한다.
- 자연어 출력과 exact local-key schema는 유지하면서 정확도를 해치지 않는 짧은 표현 지침만 적용했다.
- 사용자 WIP `scripts/recompute_strategy_a_notation.py`, Neo4j `test`, Architect, goal-verify는 건드리지 않았다.

## 현재 상태

- Summary·Rule·Table Usage의 입력을 concern별 최소 projection으로 교체했다.
- 요청마다 Analyzer queue, client-visible first chunk, stream generation, local validation을 따로 기록한다.
  first chunk는 network·provider queue·prefill을 포함하므로 서버 내부 원인으로 단정하지 않는다.
- run 종료 시 concern별 평균·p50·p95·최대, 가장 오래 걸린 단계, 입력/출력 token과 지연의 상관관계,
  느린 요청 20건과 입력 section·전체 prompt·response schema 크기를 `usage_summary.json`에 집계한다.

## 구현 완료

- 제품 그래프는 최소 속성·관계 whitelist로 닫고, 전체 결과를 메모리에서 검증한 뒤 transaction 하나로 공개한다.
- Summary, Table Usage, Rule은 독립 요청·독립 결과이며 routine에 각자 적용한다.
- 검색 embedding은 별도 검색 노드 없이 routine에 직접 저장한다.
- source API와 원문 재조회 우회를 제거했다. source 기반 노드는 줄 번호가 포함된 `code_text`를 직접 가진다.
- Parser의 식별자 occurrence로 `REFERENCES`와 `INIT_BY`를 만들며 Analyzer는 source를 재파싱하지 않는다.
- `NEXT`와 `INIT_BY` 등 화면 흐름에 필요한 관계는 유지했다.
- Analyzer 및 관련 서비스의 노드·속성·관계·UI 계약을 동기화했다. Architect는 별도 단계로 동기화했다.
- 폴더는 쉬운 한 단어, 일반 Python 파일은 2~3단어 원칙으로 정리하고 죽은 스크립트·리소스·레거시 경로를 제거했다.
- Summary는 줄 번호 원문과 `line range : syntax type` 들여쓰기 트리만 기본 입력으로 사용한다.
  직접 테이블 접근·원문 밖 symbol·결정론적 direct callee effect·연결된 외부 계약은 있을 때만 붙인다.
- Rule은 조건·효과·행을 한 번만 담고 각 행에 결속된 exact source만 전달한다. Table Usage도
  Parser/링커가 직접 접근에 결속한 source만 전달한다.
- 생성된 callee Summary와 죽은 `profile_prompt_json` 경로, 사용되지 않던 DBMS `_common.yaml`을 제거했다.

## 검증 결과

- Analyzer: 1,323 passed, 4 skipped, 312 subtests passed
- Parser: 229 passed, 12 skipped, Maven BUILD SUCCESS
- Architect: 1,376 passed, 2 skipped, frontend build passed
- Catalog: 63 passed, 28 subtests passed
- Fabric: 52 passed, 16 subtests passed
- Gateway: Maven BUILD SUCCESS
- Frontend: type-check와 production build passed
- Workspace: environment, process ownership, source lifecycle 검사 passed
- OpenAI 실제 3분할 확인: 3 calls, input 4,131, output 151
- 새 최소 Summary 작은 표본: system+user 3,939자, 배포 Qwen tokenizer 기준 1,774 token
- GPU run의 360개 Summary 기록을 같은 source+outline projection으로 오프라인 재구성한 결과:
  평균 17,614→8,744자, p95 53,116→21,319자, 최대 254,921→48,472자, 전체 50.4% 감소.
  선택 문맥이 있는 실제 요청은 이 수치보다 조금 커질 수 있다.
- Neo4j `playground`: 제품 graph 2 nodes/1 relationship 단일 transaction 공개와 readback 통과 후 검증 fixture 삭제

## 최종 44,143 LOC 재검증

- 결과 경로: `gpu-44143loc-qwen38-final16-20260907/20260907_104440_b477ee055558`
- 전체 20분 27.4초, 분석 공개 전 19분 01.3초, Neo4j 공개 1분 25.6초였다.
- 동일 규모 이전 baseline 38분 50.9초 대비 전체 47.3%, 공개 90.5%, 공개 전 20.0% 감소했다.
- Qwen 요청 934건이 모두 성공했다. Summary 416, Rule 433, Table Usage 78, Table Description 7이다.
- input 3,590,875, output 498,706이었다. 이전 baseline 대비 input 2.9%, output 11.1% 감소했다.
- Rule은 input 5.9%, output 16.2% 감소했고 최대 input은 26,959에서 19,763 token으로 줄었다.
- 큰 Rule 17개가 추가 분할됐지만 실패·retry·unavailable은 0이었다. 실제 Rule 결과의 빈 조건·효과도 0이었다.
- 동시성 실측은 8=7.40, 16=9.26, 24=6.62, 32=8.84 calls/s로 16이 가장 빨랐다.
  서버 `max_running_requests`도 16이므로 16을 최종값으로 유지한다.
- 가장 느린 Rule은 126~128 effects, input 14,447~19,763, output 3,086~3,802였으며
  96.4~118.8초가 걸렸다. effect·input·output과 model latency 상관은 각각 0.928·0.932·0.949였다.
- 병목은 소수 실패가 아니라 큰 Rule 요청의 생성량과 서버 실행 상한 안에서 934건을 처리하는 총량이다.
  16보다 큰 client 동시성은 처리량을 높이지 못했다. 추가 우회·retry·truncate는 적용하지 않는다.
- 제품 graph는 11,366 nodes, 20,049 relationships이며 source 기반 노드의 `code_text` 누락은 0이다.
  Summary 근거 오류 2건은 공개 전에 거부되어 해당 함수의 summary와 embedding이 없는 것이 정상이다.
- `SEARCH_SEGMENT`, `ANALYSIS_SCOPE`, `ANALYSIS_GENERATION`, `GLOSSARY_TERM` 및 제거 대상 legacy 속성은 0이다.
- `analyzer.log`는 node/relationship 전체 props 복제를 없애 9.39 MB로 줄었다.

## GPU 1회 결과 정정

- 실제 배포 모델: `Qwen/Qwen3.8-27B-FP8`, SGLang alias `frentis-ai-model`
- 대상: C 23파일, 44,143 LOC, routine 416개
- 실행 전체: 22:26:15~22:44:01, 성공 558 calls, input 3,825,560, output 319,895, cache 0
- 호출별 전체 LLM latency는 Summary 평균 49.15초·p95 97.61초, Rule 평균 76.08초·p95 167.17초,
  Table Usage 평균 34.61초·p95 55.36초였다.
- Analyzer 슬롯 대기는 Summary 평균 430.13초, Rule 평균 399.48초, Table Usage 평균 223.43초였고
  최대 대기는 Rule 1,005.75초였다. 총 슬롯은 32, 실제 peak도 32였다.
- 마지막 Summary 요청이 두 chunk 뒤 120초 동안 새 chunk를 받지 못해 실행은 실패했고 제품 공개 전에
  종료됐다. 기존 `3분 14초·79 calls·입력 387,029·출력 36,658`은 최종값이 아니라 중간 관측값이었다.
- 직접 효과가 없는 행을 모델 입력 전에 제거하고, 효과 설명이 빈 행은 제품 공개 전에 격리하도록 수정했다. 관련 회귀와 전체 Analyzer 테스트가 통과했다.
- 당시 승인된 실제 GPU 실행 1회는 실패 후 재실행하지 않았다. 2026-09-07 사용자가 프롬프트 정리와
  전체 회귀가 끝난 뒤 같은 규모의 GPU 전체 실행 1회를 새로 승인했다.

Neo4j `test` DB는 어떤 검증에서도 접근하지 않았다. 최종 GPU 전체 분석은 새 승인 범위에서 1회만 수행했고
재실행하지 않았다.
