# Implementation Plan

1. 기존 retriever를 `legacy`와 `global_anchor` 경로로 분리한다.
2. global 경로는 원문 recall 후 안전한 질의 보강을 만들고, 변형별 recall 결과를 ID로 병합한다.
3. 병합 후보를 보강 질의 전체 문맥으로 한 번 재채점해 앵커 3개를 고른다.
4. 앵커에서 CALLS 양방향과 테이블 관계를 정확히 1홉 조회해 evidence를 조립한다.
5. 평가기에 role·rank·구제경로·결과크기 비교를 추가한다.
6. 단위 테스트 후 legacy/global 53개 골드를 실행한다.
7. 회귀가 없을 때만 global을 기본값으로 전환하고 analyzer만 재시작한다.
