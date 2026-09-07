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

## 현재 재검증

- [x] 세 함수 의미 concern의 system prompt를 하나의 byte-identical 정본으로 통합
- [x] system·user·공통 contract의 중복 정적 지시 제거와 의미 계약 회귀 추가
- [x] 변경 전후 실제 정적 prompt token 비교
- [x] Rule complete-row 결정론 분할과 partial unavailable exact-once 구현
- [x] Neo4j labeled endpoint lookup과 labeled readback 구현
- [x] 출력 간결화 A/B와 자연어 귀속·빈 의미 검증
- [x] Qwen client 동시성 8/16/24/32 처리량 비교 후 16 확정
- [x] focused 검사와 Analyzer 전체 회귀 통과
- [x] Analyzer 헌법과 Claude/Codex 공용 skill에 재발 방지 기준 동기화
- [x] 새로 승인된 GPU 전체 실행 1회와 함수·조건별 병목 분석

재검증은 44,143 LOC 전체 20분 27.4초, 934/934 LLM 요청 성공으로 끝났다. 기존 `test` DB는 접근하지 않았다.
