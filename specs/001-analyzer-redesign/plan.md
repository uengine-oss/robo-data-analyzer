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
