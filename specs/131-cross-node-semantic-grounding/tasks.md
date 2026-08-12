# spec 131 현재 Tasks

현재 상태와 실행 순서는 `HANDOFF.md`가 소유한다. 이 파일은 현재 완료 게이트만 유지하며 과거의
세부 수행 이력을 누적하지 않는다.

## 완료된 기반

- [x] C131-01 Framework20/DBMS20의 SUMMARY8·RULE10·EXAMPLE8·TABLE6·COLUMN8 봉인과
  수정 전 직접 판정 완료 — `evidence/sealed-sample-40.json`, `semantic-review-40.md`
- [x] C131-02 summary 전체 흐름·입력 충분성 계약, Oracle ROLLBACK AST/effect,
  sufficiency graph/MCP 전달의 대표 replay와 회귀 고정
- [x] C131-03 컨텍스트 압축·새 세션 복구용 현재 상태 정본과 canonical 진입 경로 정립

## 완료된 의미 계약

- [x] T131-10 SUMMARY·RULE·EXAMPLE·TABLE·COLUMN별 필수/선택/노이즈 입력과
  actual prompt/output/validator/consumer matrix, 지원 언어·DBMS 방언 생산자 범위 작성 —
  `evidence/semantic-contract-matrix.md`
- [x] T131-11 저장 의미 모집단의 empty/partial/insufficient/missing_context/finding 전수
  inventory와 정상 needs_context/제품 결함 분류 —
  `scripts/inventory_semantic_outputs.py`, `evidence/semantic-population-inventory.md`
- [x] T131-12 모든 코드노드의 컬럼별 원문 usage를 좌표·소유권과 함께 table별로 집계해
  기존 최종 COLUMN 의미 입력 한 경로에 전달 —
  `pipeline/steps/step7_analyze_code/column_usage_projection.py`,
  `evidence/column-usage-grounding.md`
- [x] T131-13 최소 GPU 출력·결정론 evidence projection 대안의 공식 리서치·설계·제한 replay —
  의미 결정론화와 schema 폭주 금지
- [x] T131-14 공통 context·prompt·schema·validator·storage 원인의 범용 수리와
  반대 사례/producer-consumer 통합 검증
- [x] T131-15 preflight와 격리 DB·별도 output root 확인 후 Framework·DBMS 재분석,
  구조 의무 finding0 및 semantic population inventory 완료 — Framework
  `spec131fw620260811`/`20260811_114920_1370a4ba88fa`, DBMS
  `spec131db420260811`/`20260811_121047_2ee17bd958e5`; Framework READS gold 1건은
  source가 DELETE인데 SELECT로 적힌 gold 오류로 분리
- [x] T131-18 actual prompt 전체에서 Task/Input authority/allowed judgment/relation/Decision/Output
  계약을 모호함 없이 재구성하고 SUMMARY effective evidence와 TABLE usage direct evidence를
  concern별 projection으로 분리한다. 예시·needs_context·우선순위는 보존하고 중복·무관 입력만
  제거하며 actual 저장 prompt replay와 byte/token 비교로 검증한다.
- [x] T131-16 동일 봉인40 새 결과 직접 재판정 — `37 exact + 2 needs_context +
  1 omission`, 정답 `39/40=97.5%`, fabrication 0, contradiction 0
- [x] T131-17 Analyzer 전체 회귀·prompt byte·diff/WIP·환경 종료 감사와 현재 spec 정본
  동기화 — `1062 passed, 5 skipped, 388 subtests`, prompt `28/28`, actual MCP 전달 통과

## 후속으로 격리

- Architect 동일 조건 재실행은 이제 spec125/Architect spec055에서 별도 수행한다.
- A Python 관계식은 KEEP이며 spec131에서 변경하지 않는다.
