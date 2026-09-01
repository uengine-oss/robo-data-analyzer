# 관련 서비스 계약

## Analyzer

- 분석 실행·제어, 직접 의미 검색, MCP를 제공한다.
- graph whitelist와 단일 transaction 공개를 소유한다.
- 원문 표시는 제품 노드의 `code_text`로 제공한다.

## Parser

- `parser-contract.md`의 구조 사실을 생산한다.
- 업무 의미와 최종 graph 관계는 결정하지 않는다.

## Catalog

- 현재 Neo4j 제품 그래프를 조회해 노드·관계·속성을 그대로 전달한다.
- 테이블 metadata의 권위 값을 보존한다.

## Fabric

- datasource와 table metadata를 object resolution 입력으로 제공한다.
- 공유 identity와 서비스 소유권을 보존한다.

## Gateway

- Analyzer의 분석·제어·검색 경로만 전달한다.
- 요청 DB 선택과 오류 계약을 변경하지 않는다.

## Frontend

- Parser concrete label과 graph whitelist를 기준으로 카드와 관계를 표시한다.
- 원문 패널은 선택 노드의 `code_text`, `file_path`, line 범위를 사용한다.
- `operations`가 있는 읽기·쓰기 관계는 실제 작업 종류를 표시한다.
- `_` 접두어 속성은 화면에 표시하지 않는다.

## Workspace

- 서비스 URL, 실행 순서, 환경 설정만 소유한다.
- 제품 graph schema의 별도 alias를 만들지 않는다.

## Architect

- Analyzer의 현재 label·property·relationship을 직접 소비한다.
- 존재하지 않는 값에 기본 의미를 부여하지 않는다.
- Analyzer 계약 변경과 별도 commit으로 동기화하고 실제 시나리오를 검증한다.
