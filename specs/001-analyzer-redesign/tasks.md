# 작업 목록

## 완료

- [x] 최소 제품 그래프와 단일 transaction writer 구현
- [x] 함수별 Summary, Table Usage, Rule 독립 호출 구현
- [x] routine 직접 embedding과 검색 구현
- [x] 중복 원문 조회 경로와 검색 중간 노드 우회 제거
- [x] 모든 source 기반 제품 노드 `code_text` 보존
- [x] Parser 식별자 occurrence와 Analyzer `REFERENCES`·`INIT_BY` 구현
- [x] Analyzer source 재토큰화 제거
- [x] Analyzer 폴더·파일·스크립트·리소스 구조 정리
- [x] Catalog, Fabric, Gateway, Frontend, Workspace 계약 동기화
- [x] Architect label·property·relationship·UI 소비 계약 별도 동기화
- [x] 전체 서비스 테스트·build·type-check
- [x] 허용된 `playground` DB transaction·readback 검증
- [x] OpenAI 실제 3분할 확인
- [x] GPU 44,143 LOC 실제 1회 실행과 실패 지점 수정

## LLM 입력·성능 정리

- [x] Summary 입력을 전체 원문 + 들여쓰기 구조 트리 + 직접 R/W + 원문 밖 필수 문맥으로 축소
- [x] 빈 선택 section과 전체 callee Summary 전달 제거
- [x] Rule의 source 중복 condition/effect와 누적 row 경로 제거
- [x] Table Usage를 직접 접근 관련 source projection으로 축소
- [x] Summary·Rule·Table Usage prompt 문구 중복 정리와 계약 테스트 동기화
- [x] 요청별 queue·first-chunk·generation·validation 지연 기록 및 감사 보고 구현
- [x] 실제 작은 source로 prompt 문자·Qwen token·의미 필수 정보 회귀 검증

## 이전 최종 정리

- [x] 최종 레거시 문자열·diff 감사
- [x] 보호된 사용자 WIP를 제외한 저장소별 commit과 push

GPU 실행은 사용자의 1회 제한에 따라 수정 후 반복하지 않는다. Neo4j `test` DB는 접근하지 않는다.
