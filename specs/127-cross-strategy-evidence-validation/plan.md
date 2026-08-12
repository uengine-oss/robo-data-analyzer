# Implementation Plan

1. 현재 코드에서 지원 언어/방언과 parser→Analyzer→Catalog/Fabric→graph→MCP 소비자를 전수화한다.
2. 기존 세 함수와 겹치지 않는 shopmall 의미 표본 20개를 결과 열람 전에 봉인한다.
3. source에서 구조 정답과 의미 gold를 작성하고, 저장 run의 실제 prompt/response는 그 뒤 연다.
4. A/B 모두 같은 gold로 채점한다. 반복 호출은 같은 입력·모델·schema에서 수행하고 단발값과 분리한다.
5. 최초 오좌표부터 upstream 단일 생산자를 수리하며 중복 producer는 만들지 않는다.
6. 전체 회귀·prompt ledger·결정론 전수·직접 의미 판정 후 정본과 작업보고를 갱신한다.

