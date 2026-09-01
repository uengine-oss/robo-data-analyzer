# 현재 상태 - 2026-09-01

## 구현 완료

- 제품 그래프는 최소 속성·관계 whitelist로 닫고, 전체 결과를 메모리에서 검증한 뒤 transaction 하나로 공개한다.
- Summary, Table Usage, Rule은 독립 요청·독립 결과이며 routine에 각자 적용한다.
- 검색 embedding은 별도 검색 노드 없이 routine에 직접 저장한다.
- source API와 원문 재조회 우회를 제거했다. source 기반 노드는 줄 번호가 포함된 `code_text`를 직접 가진다.
- Parser의 식별자 occurrence로 `REFERENCES`와 `INIT_BY`를 만들며 Analyzer는 source를 재파싱하지 않는다.
- `NEXT`와 `INIT_BY` 등 화면 흐름에 필요한 관계는 유지했다.
- Analyzer 및 관련 서비스의 노드·속성·관계·UI 계약을 동기화했다. Architect는 별도 단계로 동기화했다.
- 폴더는 쉬운 한 단어, 일반 Python 파일은 2~3단어 원칙으로 정리하고 죽은 스크립트·리소스·레거시 경로를 제거했다.

## 검증 결과

- Analyzer: 1,315 passed, 4 skipped, 311 subtests passed
- Parser: 229 passed, 12 skipped, Maven BUILD SUCCESS
- Architect: 1,376 passed, 2 skipped, frontend build passed
- Catalog: 63 passed, 28 subtests passed
- Fabric: 52 passed, 16 subtests passed
- Gateway: Maven BUILD SUCCESS
- Frontend: type-check와 production build passed
- Workspace: environment, process ownership, source lifecycle 검사 passed
- OpenAI 실제 3분할 확인: 3 calls, input 4,131, output 151
- Neo4j `playground`: 제품 graph 2 nodes/1 relationship 단일 transaction 공개와 readback 통과 후 검증 fixture 삭제

## GPU 1회 결과

- 실제 배포 모델: `Qwen/Qwen3.8-27B-FP8`, SGLang alias `frentis-ai-model`
- 대상: C 23파일, 44,143 LOC, routine 416개
- 실제 호출 구간: 79 calls, input 387,029, output 36,658, cache 0
- 요청당 input: 평균 4,899, p50 3,971, p95 12,503, 최대 17,758
- 실행은 3분 14초에 제품 Rule의 빈 효과 설명 1건을 발견하고 원자 공개 전에 종료됐다.
- 직접 효과가 없는 행을 모델 입력 전에 제거하고, 효과 설명이 빈 행은 제품 공개 전에 격리하도록 수정했다. 관련 회귀와 전체 Analyzer 테스트가 통과했다.
- 사용자가 요청한 실제 GPU 실행은 1회만 수행했으므로 수정 후 재실행하지 않았다. 따라서 44,143 LOC 전체 완료 시간은 실측되지 않았다.

Neo4j `test` DB는 어떤 검증에서도 접근하지 않았다.
