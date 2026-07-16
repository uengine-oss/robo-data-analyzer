# Implementation Plan: Analyzer 공유 그래프 소유권·안전 초기화

공통 `graph/node_upsert.py`가 owner 속성의 단일 정의처다. 개별 query 예외(TABLE/COLUMN/SCHEMA/
PACKAGE/RULE/EXAMPLE/QUESTION)는 공통 owner 상수를 사용한다. `wipe_graph`는 owner marker 우선,
legacy 분석 라벨 제한 폴백으로 전환하고 혼합 DB 삭제 계약을 테스트한다.

