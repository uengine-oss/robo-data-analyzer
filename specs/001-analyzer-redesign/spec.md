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

### 요청별 최소 입력

- Summary는 줄 번호가 붙은 함수 전체 원문, `라인 범위 : 구문 타입` 형식의 들여쓰기 구조 트리,
  확정된 직접 Table·Column·READ/WRITE, 원문 밖 필수 symbol, 호출자가 관찰하는 직접 callee 결과와
  현재 함수의 의미를 바꾸는 프로젝트 고유 계약만 받는다.
- 구조 트리는 부모·자식을 들여쓰기로 표현한다. 조건식, 호출문, 대입식, 누적 실행 경로, 내부 ID와
  원문에서 다시 읽을 수 있는 값을 반복하지 않는다.
- Rule은 의미를 채울 condition·effect·row의 source 위치와 중첩 구조, 관련 source 구간 및 원문 밖
  필수 symbol·callee·Table/API 계약만 받는다. source에 이미 있는 condition/effect 문자열과 부모
  `when` 경로를 각 row에 누적 복제하지 않는다.
- Table Usage는 확정된 Table·READ/WRITE·Column·발생 위치와 그 접근의 입력 구성·실행 조건·결과
  소비를 판단하는 source 구간만 받는다. 무관한 함수 본문은 보내지 않는다.
- 선택 section이 비면 section 이름과 빈 배열·객체를 모두 보내지 않는다.

### 요청별 성능 증거

각 실제 요청은 Analyzer 슬롯 대기, 첫 응답 chunk까지의 시간, 첫 chunk 이후 완료까지의 시간,
응답 검증 시간, 입력·출력 token과 결과 상태를 같은 요청 좌표로 기록한다. 첫 chunk 시간은 서버 queue와
prefill이 합쳐진 관측값이므로 서버가 별도 지표를 제공하지 않는 한 순수 prefill이라고 부르지 않는다.
전체 wall time만으로 모델·서버·Analyzer 중 어느 하나를 원인으로 단정하지 않는다.

### 정적 프롬프트와 출력 효율

- Summary·Table Usage·Rule의 system prompt는 하나의 정본을 공유하며 실제 전송 바이트가 같아야 한다.
  concern별 의미 지침은 고정 user prefix에 두고, 같은 금지·출력 계약을 system·user·공통 contract에
  반복하지 않는다.
- 입력 절감은 정적 지침, 동적 evidence, response schema를 분리해 측정한다. 출력 절감은 자연어 의미와
  JSON wire 비용을 분리해 측정하며, 의미 누락이나 local key의 순서 기반 오귀속을 만드는 축약은 하지 않는다.
- prompt 변경 전후에는 실제 직렬화된 system·user·schema의 바이트와 tokenizer token을 비교하고,
  같은 required population의 의미 정확도와 요청 지연을 함께 검증한다.

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
8. LLM 요청별 queue·first-chunk·generation·validation 지연과 입력 section 크기를 감사할 수 있다.
9. 세 함수 의미 concern의 system prompt가 byte-identical하고 동일 정적 지시가 한 번만 존재한다.
10. 큰 Rule은 완전한 row 단위로 분할되고 모든 row·atom이 분할 전후 exact-once다.
11. client 동시성은 배포 서버 상한과 8/16/24/32 실측 중 가장 빠른 값으로 정한다.
12. Neo4j endpoint lookup과 readback은 label+`_id` 제약을 사용하며 누락 관계를 조용히 버리지 않는다.
