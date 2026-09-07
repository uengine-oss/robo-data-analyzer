# 현재 상태 - 2026-09-07

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

- Analyzer: 1,319 passed, 4 skipped, 308 subtests passed
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
- 사용자가 요청한 실제 GPU 실행은 1회만 수행했으므로 수정 후 재실행하지 않는다. 따라서 성공한
  44,143 LOC 전체 완료 시간은 아직 실측되지 않았다.

Neo4j `test` DB는 어떤 검증에서도 접근하지 않았다.

새 prompt의 OpenAI generation smoke는 현재 `.env`가 사내 SGLang 인증값이라 401에서 1회 중단됐다.
모델 생성은 발생하지 않았고 임시 run 산출물은 삭제했다. GPU 전체 분석은 허용된 1회를 넘겨 재실행하지 않았다.
