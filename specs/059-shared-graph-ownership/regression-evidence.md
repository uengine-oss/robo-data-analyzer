# 공유 그래프 소유권 회귀 증거

2026-07-17 실제 `neo4j` database에서 Architect `Proposal`, Data Fabric `DataSource`, Analyzer owner 노드를 함께 둔 상태로 shop_mall 전체 재분석을 실행했다.

- 시작 상태: Proposal 1개, DataSource sentinel 1개(`graph_owner=data-fabric`), Analyzer owner 3,377개.
- 재분석: 452.7초, 출력 8,153,346 bytes, process exit 0, `[FAIL]`/`[ERROR]` 0, stream 종료와 Result 산출 확인.
- owner 격리: Proposal ID 목록 byte-equivalent, DataSource Neo4j owner·Fabric registry·MindsDB 별칭 모두 보존. 재분석 후 Analyzer owner 3,381개. sentinel 정상 삭제 후 누수 0.
- 원문/그래프: 함수 124/124, TABLE 22/22, FK 11/11, duplicate id 0, R/W 비DDL 대상 0, AFFECTS_TABLE 잘못된 대상 0.
- 관계: CALLS 누락 0·과잉 0, 실제 재귀 self-loop 1/1, 제어구조 불일치 0, 고아 RULE/EXAMPLE 0, 중복 관계 0.
- 의미 품질: e2e 14/14, rule 386개·example 최대 46·반복 statement 0, GLOSSARY 182개, semantic search rules/tables 전달 통과.

실행 증거는 `goal-verify/output/runs/r1b-owner-reanalysis/shopmall-reanalysis.ndjson`와 `goal-verify/output/reports/{e2e-report,quality-report}.json`에 보존했다.
