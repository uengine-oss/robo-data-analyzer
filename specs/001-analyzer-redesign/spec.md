# Analyzer 제품 명세

## 목표

Parser가 만든 구조 사실을 Analyzer가 의미로 해석하여 사람이 이해하기 쉬운 최소 그래프로 공개한다. 같은 의미를 다른 노드나 경로에 복제하지 않으며, 내부 실행 장부는 제품 그래프와 분리한다.

## 책임 경계

1. Parser는 문법 구조, 정확한 원문 범위, 식별자 occurrence, 호출·상속·대입 구조를 만든다.
2. Analyzer는 이름 binding, 호출 대상, 테이블 접근, 업무 의미를 결정한다.
3. Analyzer는 관계를 만들기 위해 원문을 다시 토큰화하지 않는다.
4. 입증되지 않은 대상은 제품 노드나 관계로 추측 생성하지 않는다.
5. 모든 source 기반 노드는 자기 범위의 줄 번호가 붙은 `code_text`를 가진다.

## LLM 분석

함수마다 독립된 세 요청을 실행한다.

- Summary: 함수의 요약과 역할
- Table Usage: 검증된 직접 테이블 접근의 업무 의미
- Rule: 구조적으로 결정된 조건과 효과의 자연어 의미

세 응답은 하나로 합치지 않는다. 각 결과를 자신이 소유한 함수·관계·Rule에 적용한다. 입력에는 판단에 필요한 원문과 구조만 넣고 저장 ID, 해시, 경로, byte offset, 실행 상태, 중복 설명은 넣지 않는다.

## 그래프

- Parser의 실제 label을 source 노드 label로 사용한다.
- 별도 제품 노드는 `PACKAGE`, `TABLE`, `COLUMN`, `RULE`, `EXTERNAL_CHUNK`만 둔다.
- 사용 가능한 관계와 속성은 `contracts/graph-contract.md`의 whitelist가 전부다.
- routine은 `_search_embedding`을 직접 가지며 검색 결과도 routine에서 시작한다.
- 검색 주변 문맥은 기존 그래프 관계로 확장한다.
- `_id`, `_owner`, `_search_embedding`처럼 제품 동작에 꼭 필요한 내부 속성만 `_` 접두어를 사용한다.

## 공개와 검색

1. 완성된 전체 batch를 메모리에서 먼저 검증한다.
2. Analyzer 소유 데이터를 Neo4j transaction 하나에서 교체한다.
3. 실패하면 기존 그래프를 유지한다.
4. 다른 서비스가 소유한 데이터는 삭제하지 않는다.
5. API와 UI는 노드에 저장된 `code_text`를 사용한다.

## 구조와 이름

- 제품 폴더 이름은 한 단어로 쓴다.
- 일반 Python 파일 이름은 역할이 드러나는 2~3단어를 쓴다.
- `main.py`, `__init__.py`, `README.md`, `Dockerfile`은 표준 이름 예외다.
- 운영자 스크립트는 `scripts/`, Agent가 직접 쓰는 도구는 `agents/tools/`에 둔다.
- 소비자가 없는 스크립트·리소스·호환 계층은 두지 않는다.

## 완료 조건

1. graph whitelist 밖의 제품 값이 저장되지 않는다.
2. Parser→Analyzer 실제 언어별 handoff가 통과한다.
3. Analyzer, Parser, Catalog, Fabric, Gateway, Frontend, Workspace, Architect 계약이 일치한다.
4. 전체 비DB 회귀와 허용된 기존 DB 통합 검증이 통과한다.
5. OpenAI 소규모 실호출 결과와 토큰 사용량을 확인한다.
6. GPU 모델로 약 5만 LOC를 정확히 한 번 실행해 시간·토큰·결과를 기록한다.
7. 최종 문서, 작업 보고, 커밋과 원격 push가 완료된다.
