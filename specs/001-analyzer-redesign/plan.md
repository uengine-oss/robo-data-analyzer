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
