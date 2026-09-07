# 구현 계획

## 1. Analyzer 핵심

- 최소 제품 그래프와 whitelist를 구현한다.
- Summary, Table Usage, Rule 요청과 결과 귀속을 분리한다.
- routine 직접 embedding과 관계 확장 검색을 구현한다.
- 전체 batch 검증 뒤 transaction 하나로 공개한다.

## 2. Parser 계약

- 식별자 occurrence를 공통 Parser 출력으로 제공한다.
- Analyzer의 source 재토큰화를 없앤다.
- C, Java, Python, Oracle, PostgreSQL의 실제 handoff를 확인한다.

## 3. 구조 정리

- `pipeline/{parsing,checks,meaning,stages}` 책임을 분명히 한다.
- stage 폴더를 `ddl`, `ast`, `external`, `scan`, `links`, `context`, `output`으로 정리한다.
- 운영 스크립트와 Agent 도구를 분리한다.
- 파일명과 import, 테스트 트리를 함께 동기화한다.

## 4. 서비스 동기화

- Catalog, Fabric, Gateway, Frontend, Workspace를 공개 계약에 맞춘다.
- Architect의 label·property 소비를 별도 단계에서 동기화한다.

## 5. 검증과 배포

- 정적 감사, 전체 단위 테스트, 빌드, type-check를 수행한다.
- 허용된 기존 Neo4j DB에서 transaction·검색·API·UI 연결을 검증한다.
- OpenAI 소규모 확인 뒤 GPU 5만 LOC를 한 번 측정한다.
- 결과 문서와 저장소별 커밋을 만들고 push한다.

## 6. LLM 입력과 지연 재검증

- 실제 prompt builder를 concern별 최소 projection으로 바꾸고 prompt 계약·schema·테스트를 함께 맞춘다.
- Summary의 구조 fact dump를 들여쓰기 구조 트리로 교체하고, 빈 section과 전체 callee Summary를 제거한다.
- Rule의 source 중복 condition/effect와 누적 row 경로를 구조 참조로 교체한다.
- Table Usage는 직접 접근의 원인·조건·결과에 연결된 source만 투영한다.
- 각 요청의 Analyzer queue, first chunk, generation, validation과 token을 한 record에서 비교한다.
- 기존 봉인 GPU 실행은 재실행하지 않고 회귀·작은 실제 입력·승인된 실호출 순으로 검증한다.

## 7. 정적 프롬프트 정리와 승인된 전체 재측정

- 세 함수 의미 concern의 system prompt를 하나의 정본으로 합치고 실제 전송 바이트 동일성을 테스트한다.
- system·user·공통 contract의 중복 지시만 제거하고 Summary 전체 흐름, Rule atom, Table 직접 접근의
  의미 의무와 closed response schema는 유지한다.
- 변경 전후 정적 tokenizer token과 실제 직렬화 prompt section을 비교한다.
- focused prompt/response 계약 테스트, Analyzer 전체 회귀, skill 감사를 통과시킨다.
- 사용자가 새로 승인한 GPU 전체 실행을 마지막에 한 번 수행한다. `test` DB는 제외하고 기존 허용 DB를
  인증·목록·online 상태까지 확인한 뒤 사용한다.
- 전체 run에서 함수·concern별 queue, first chunk, generation, validation, input/output token을 대조해
  병목의 최초 구간과 발생 조건을 actual로 판정한다.

## 8. 큰 요청과 공개 병목 처리

- Rule은 128 effect를 넘기 전에 완전한 row 경계로 결정론 분할하고 exact-once를 검증한다.
- 출력은 의미 필드와 local-key 귀속을 유지한 채 자연어만 짧게 요청하고 같은 실제 prompt A/B로 확인한다.
- 같은 32개 실제 요청으로 client 동시성 8/16/24/32를 비교하고 배포 서버 상한과 함께 최종값을 정한다.
- Neo4j relationship은 endpoint label 조합별로 묶어 labeled `_id` lookup과 readback을 수행한다.
- 전체 run 1회에서 실패 수, token, latency, graph 수와 공개 전후 시간을 baseline과 비교한다.
