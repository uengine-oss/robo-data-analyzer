# Feature Specification: 교차 전략 Analyzer 근거·의미 검증

**Status**: In progress  
**Created**: 2026-08-11

## 목적

A(업무 의미의 Python 관계식 표기)와 B(RULE·좌표·CALL/RW·TABLE·sample 근거 전달)를
경쟁 구현으로 섞지 않고, 동일한 원문·정답지에서 각각의 정확성·누락·창작·역추적성·비용을
검증한다. 동시에 DBMS와 Framework의 결정론 구조 근거가 parser부터 graph/MCP 소비까지
손실 없이 이어지는지 감사한다.

## 원칙

1. 구조적으로 완전성을 증명할 수 있는 사실만 결정론 truth로 승격한다.
2. 언어·방언·동적 구문 때문에 완전성을 증명할 수 없으면 LLM으로 사실을 확정하지 않고
   unresolved/needs_context 및 잔차 장부로 남긴다.
3. A의 관계식은 의미 표현 후보이며 source truth나 graph identity truth가 아니다.
4. B의 좌표·관계도 의미 정답을 자동 보증하지 않는다. 실제 전송 prompt와 원문을 대조한다.
5. LLM 단발 결과는 재현성 근거가 아니다. 동일 입력 반복에서 pass-all, 분산, 최악값을 함께 잰다.
6. 검사 결과가 이상하면 검사기·정답지·좌표 매핑을 제품보다 먼저 확인한다.
7. analyzer 재실행 0회, accept/apply 금지, 기존 dirty WIP 보존을 유지한다.

## 검증 범위

- Framework parser 지원: Java, C, Python. Pro*C/Cobol은 lexing-only임을 구분한다.
- DBMS parser 지원: Oracle PL/SQL, PostgreSQL. dialect별 동등 계약 여부를 따로 판정한다.
- 구조축: object reference, schema/link/alias/qualified column, CALL, R/W, RULE 좌표,
  prompt ownership, graph target, MCP/Architect provenance.
- 의미축: 결과 열람 전에 봉인한 신규 shopmall 20함수. 기존 A/B 세 함수와 겹치지 않는다.
- 소비축: 실제 저장 system/user prompt, raw/parsed response, validator, graph write, GWT view,
  Architect Simplified/Detailed 최종 결과.

## 합격선

- 결정론 구조축은 선언한 지원 범위에서 mismatch 0이어야 한다. 100%를 증명하지 못하는
  construct는 지원으로 세지 않고 잔차로 보고한다.
- 의미축은 각 함수/행을 exact·partial·contradiction·fabrication·omission·needs_context로
  판정한다. 평균만으로 합격시키지 않고 fabrication/contradiction과 반복 최악값을 별도 공개한다.
- A/B 채택·보류는 동일 표본·모델·prompt evidence·반복 수에서만 비교한다.

