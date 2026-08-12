# spec 131 현재 Tasks

## 보존된 기준선

- [x] C131-01 봉인40과 source/prompt/output/stored 직접 판정 기준선 보존
- [x] C131-02 배치 상한과 source 의미 상한 분리, full node ID 의미 identity, 입력 충분성 상태 보존
- [x] C131-03 기존 계약 기준 `39/40=97.5%`, fabrication 0, contradiction 0 기록 보존
- [x] C131-04 동적 의미 프레임 방향과 complete-source Architect 기본 전달 폐기를 문서 정본에 고정

## 동적 의미 프레임 전환

- [x] T131-19 Framework/DBMS 전체 자연어 의미 producer→schema→normalizer→validator→storage→
  graph/API/MCP→Architect consumer inventory와 WIP keep/adapt/remove 단일 영향표 작성·후속 재사용
- [x] T131-20 공통 `StructuralProfile`, frame family, stable slot/evidence ID, `semantic-frame/v1` 계약 고정
- [x] T131-21 shallow dynamic schema와 stable-prefix prompt를 구현하고 schema/token/repetition/KV 관측 기준 고정
- [x] T131-22 SUMMARY·RULE·EXAMPLE·CALL·TABLE·COLUMN·MODULE·logical name을 frame-first 생산으로 전환
- [x] T131-23 legacy summary/description/nl을 frame projection으로 전환하고 자연어 이중 생산자 제거
- [x] T131-24 sufficiency/grounding/completeness/ownership validator와 population inventory 전환
- [x] T131-25 graph/storage/API/MCP semantic-frame 정본과 deduplicated Architect projection 구현
- [x] T131-26 Architect complete-source 기본 전달 WIP 제거·적응 및 spec055 Simplified/Detailed 소비 동기화
- [x] T131-27 Framework/DBMS 범용·반대 fixture, prompt/schema 성능, 전체 회귀 검증
- [x] T131-28 새 격리 DB/output의 최소 actual Analyzer 실행과 봉인 직접 의미 판정
- [x] T131-29 Analyzer 합격 뒤 Architect 두 실제 경로 GWT/evidence ref 원문 대조
- [x] T131-30 작업보고·HANDOFF 결과 교체, 임시 실행물 정리, diff 감사, 요청 범위 commit/push

## 불변

- A Python 관계식은 KEEP이며 수정·재실험하지 않는다.
- 병렬 처리·priority·round planning·batch scheduling·semaphore·concurrency를 변경하지 않는다.
- 기존 dirty WIP·보존 DB·logs를 reset/checkout/clean/overwrite하지 않는다.
- Architect 정상 생성 packet에 complete source나 새 source 조각을 넣지 않는다.
- `accept/apply`를 호출하지 않는다.
