# Feature Specification: Analyzer 전 파일 명명·책임 재감사

**Created**: 2026-07-17
**Status**: Complete

## Requirements

- 현재 tracked source와 진행 중 신규 source 전체를 동결하고 파일별 감사대장을 만든다.
- 루트 설정 모듈은 `analyzer_settings.py`, 설정 집합은 `AnalyzerSettings`, singleton은 `SETTINGS`다.
- 루트 위치는 유지하며 옛 `env_catalog`, `EnvCatalog`, `ENV` 활성 참조는 0이어야 한다.
- 파일·클래스·함수·변수는 경로 문맥 없이 책임을 식별할 수 있어야 한다. 모호한 service/manager/client,
  data/result/item/tmp/utils/helpers/common/system 이름은 실제 경계 의미로 바꾸거나 근거를 기록한다.
- Catalog 외부 연동 이름은 `catalog_client`처럼 외부 경계를 명시하며 Catalog 서비스 내부 코드로
  오해되는 소유권을 만들지 않는다.
- 역사 specs는 수정하지 않고 현재 스펙·README·환경 예시·테스트·외부 활성 러너를 함께 갱신한다.
- R2 기능 변경을 보존하고 구조 리팩토링이 검색·분석·MCP·UI 의미를 바꾸지 않음을 검증한다.
- `CALLS` 자기참조는 끝점 ID 우연 일치로 재귀 판정하지 않는다. 생산자가 원문 호출로 확인한
  재귀 의도를 계약에 담고, 저장 경계에서 self/non-self 끝점과 의도 불일치를 거부한다.
- 소스 파일명 후보가 여러 개이고 요청 경로가 하나를 유일하게 식별하지 못하면 임의의 파일을
  선택하지 않고 모호성을 API 오류로 표면화한다.
- `AFFECTS_TABLE`은 R/W 필터가 확정한 Table ID를 Neo4j 저장과 라이브 캔버스 이벤트까지 그대로
  보존한다. bare table name 재매칭이나 다른 owner/datasource Table과의 다중 연결을 금지한다.
- Analyzer의 후처리·검색·MCP 상세조회는 모든 Neo4j 시작 노드와 확장 대상에
  `graph_owner='analyzer'`를 적용한다. 동일 ID를 가진 다른 서비스 노드를 읽거나 갱신하지 않으며,
  GLOSSARY/EMBED_META 같은 singleton도 owner를 MERGE identity에 포함한다.
- LLM fan-out은 입력/결과 길이 불일치를 거부하고, LLM config는 경로 이탈과 잘못된 bool/숫자
  타입을 명시적으로 거부하며, agent 파일 검색은 읽지 못한 파일을 정상 0건으로 숨기지 않는다.
- LLM 호출은 원시 AI 응답과 provider usage metadata가 확인된 경우에만 성공으로 취급한다. 원시 응답,
  AIMessage 또는 input/output token 계측값이 없으면 비용 0의 정상 호출로 통과시키지 않고 명시적으로
  실패시켜 모든 호출의 usage 기록과 run 예산 차단 계약을 보존한다.
- LLM fan-out의 취소는 일반 항목 실패 정책으로 drop/keep하지 않고 즉시 전파한다. 임베딩 공급자는
  원격/로컬 구분 없이 입력 텍스트 수와 정확히 같은 수의 벡터를 반환해야 하며 초과·누락을 모두 거부한다.
- LLM 가격표에 명시적으로 등록된 모델만 비용을 계산한다. 등록된 0원 로컬 모델은 유효하지만,
  미등록 모델을 비용 0으로 간주해 run 예산 차단기를 우회시키는 동작은 명시적으로 거부한다.
- 토큰 수와 비용 누적값은 유한한 음이 아닌 값이어야 한다. 음수·NaN·무한대 또는 입력 토큰보다
  큰 cache token으로 누적 비용을 줄이거나 예산 비교를 무력화하는 입력을 경계에서 거부한다.
- LLM YAML과 호출별 override는 선언된 타입과 범위를 그대로 검증한다. bool·분수형 정수를
  `int()`로 강제 변환하거나 비유한 sampling 값, 빈 모델명, 미지원 reasoning 값을 조용히 받지 않는다.
- Agent 구조 출력은 명시한 필드 외 값을 거부하며 DDL 변환과 call-wrapper 추출 방식은 실제
  소비자가 지원하는 값으로 제한한다. 소비자 0인 `entity_source` 거짓 계약은 스키마·프롬프트·SKILL에서
  함께 제거하고, SKILL 카탈로그 읽기와 오류 표면화는 단일 구현을 사용한다.
- LLM 3분할 출력은 실제 후속 소비가 있는 필드만 요구한다. 분할 3이 생성해 HAS_RULE 속성으로 저장하기만
  하고 검색·분석·MCP·UI 어느 곳도 읽지 않는 `coupled_domains`는 응답 스키마·Framework/DBMS 프롬프트·
  병합 프롬프트·그래프 모델·writer에서 제거하며 기존 관계의 잔존 속성도 제거한다.
- 모든 Analyzer·YAML·검색 LLM 호출은 필수 usage를 검증하고 영속 기록한다. usage 파일 쓰기 실패를
  정상 성공으로 승격하지 않으며, 이미 발생한 비용은 기록 실패 여부와 무관하게 run 예산에 누적한다.
- Agent todo/tool NDJSON은 실제 소비자를 문서화하고, 보조 진단 JSONL 쓰기 실패가 성공한 Agent
  도구 실행을 실패로 바꾸지 않게 한다. 보조 기록 실패는 공용 경계에서 경고하며 호출 한도는 양의 정수다.
- 보조 prompt-turn 기록 실패는 Agent 성공이나 원래 Agent 예외를 덮어쓰지 않는다. invoke context는
  시작 기록 성공 후에만 활성화하고 종료 성공 여부와 무관하게 정리하며 JSON 갱신은 원자 교체한다.
- 가격 매칭은 정확 ID와 명시적 날짜/provider 정규화만 허용한다. 모델 이름에 `local` 등 등록 키가
  부분 문자열로 우연히 포함됐다는 이유로 미등록 유료 모델을 등록된 무료 모델로 취급하지 않는다.
- Analyzer 환경설정은 bool·숫자·열거형·경로의 잘못된 값을 조용히 기본값이나 다른 동작으로 바꾸지 않고
  시작 경계에서 명시적으로 거부한다. 특히 검색 구현 오타는 index로 간주하지 않고 로그 레벨 오타도 INFO로
  강등하지 않는다.
- 실행 결과 루트는 비어 있지 않고 현재 작업 디렉터리나 파일시스템 루트가 아니어야 한다. 전체 재설정은
  Analyzer 소유권 표식이 있는 디렉터리만 허용하며, 최초 생성 시 빈 디렉터리만 인수한다. 비어 있지 않은
  미소유 디렉터리는 내용물을 보존한 채 거부하여 경로 오설정이 소스·사용자 파일 삭제로 이어지지 않게 한다.
- NDJSON 응답 소비자가 연결을 종료하면 분리 실행 중인 분석·검색 producer task를 취소하고 정리가 끝날 때까지
  수거한다. 정상 `stream_end`에서는 producer의 정상 종료를 수거하며, task 참조를 버려 비용·DB 작업이
  요청 수명 밖에서 계속되거나 미수거 예외가 남지 않게 한다. 분석과 검색은 동일한 스트림 수명주기 구현을 쓴다.
- NDJSON 큐의 메모리 상한과 무손실 backpressure는 동기 emit 생산자·그래프 대량 이벤트·연결 종료 계약을
  함께 측정한 뒤 결정한다. 근거 없이 임의 상한을 두고 이벤트를 drop하거나 정상 대형 그래프를 실패시키지 않는다.
- 기존 root logging handler가 있어도 Analyzer 로그 레벨과 외부 라이브러리 노이즈 정책은 적용하고, stderr
  handler만 중복 추가하지 않는다. run 파일 handler 둘의 연결은 원자적이어야 하며 중간 생성·설정·연결 실패 시
  이미 붙인 handler를 모두 root에서 제거하고 닫은 뒤 원래 오류를 전파한다.
- 오류·경고의 선택적 NDJSON 동시 전달 실패는 로컬 로그에 2차 실패를 남기되 원래 예외나 정상 제어 흐름을
  대체하지 않는다. 오류와 경고는 같은 단일 보조 전달 경계를 사용한다.
- 문서에 선언한 모든 제품 NDJSON 이벤트 유형을 실제 생산자 목록과 맞추고, 단계 완료 `message`도 다른
  이벤트처럼 analyzer.log에 mirror한다. logger mirror에서 제외하는 유형은 종료 신호 `stream_end`뿐이다.
- WorkflowState에 새 키가 생겨 artifact 분류표에서 빠져도 경고와 `graph_extras.json` 보존 경로가 실제로
  동작해야 한다. 이 사각지대 안전망 자체의 import·직렬화·파일 생성 실패를 테스트한다.
- 검증 artifact는 알 수 없는 객체를 `str()`로 위장하거나 NaN·무한대를 JSON에 기록하지 않는다. 지원 타입은
  명시적으로 재귀 직렬화하고 set은 결정적 순서로 기록하며, 파일은 임시 파일을 완전히 쓴 뒤 원자 교체하여
  직렬화·쓰기 실패가 기존 증거를 잘린 파일로 바꾸지 않게 한다.
- 단일 활성 분석 계약은 경고 후 슬롯 덮어쓰기가 아니라 두 번째 run의 명시적 거부다. 활성 등록은 run-output
  reset과 Neo4j wipe보다 먼저 수행하고, 초기화·취소·실패·성공 모두 자기 registry 슬롯과 stream을 정리한다.
  run ID는 동일 시각에 생성해도 충돌하지 않아야 한다.
- LangGraph 설정의 `configurable`은 dict 또는 미지정만 허용하고 잘못된 구조를 그대로 step에 흘리지 않는다.
  workflow 정의는 최소 한 step, 비어 있지 않은 병렬 그룹, callable, 유일한 node 이름을 빌드 전에 검증해
  프레임워크 내부의 늦고 모호한 오류 대신 정의 경계에서 원인을 밝힌다.
- pause·stop·예산 차단·상위 취소로 Step6 fan-out을 끝낼 때는 task에 `cancel()`만 호출하지 않는다. 등록을
  해제하고 미완료 task를 취소한 뒤 모든 task의 cancellation/finally와 예외를 await 수거해야 다음 재투입이나
  run 종료로 넘어간다. Framework per-node와 DBMS batch는 같은 PauseController 정리 경계를 사용한다.
- Neo4j에 저장한 제품 그래프 관계는 라이브 NDJSON에도 같은 관계 타입·끝점 ID·의미 속성으로 발신한다.
  `NEXT`처럼 기본 화면에서 숨길 관계도 생산 단계에서 누락하지 않고 프론트 단일 가시성 정책에서만 숨긴다.
  공통 그래프 델타 직렬화기는 미등록 관계나 빈 끝점을 정상 0건으로 건너뛰지 않고 즉시 거부하며, `CALLS`는
  재귀 의도 검증과 external stub ID 변환을 우회하지 않도록 전용 저장·발신 경계만 사용한다.
- 프론트 캔버스의 관계 칩은 선택한 관계 타입의 양끝 노드를 고른 뒤 그 노드들 사이의 다른 관계까지 자동으로
  섞지 않아야 한다. 라벨·관계 선택, 1-hop 확장, 프리셋 각각에 대해 최종 노드·관계가 선택 문맥과 일치하는지
  부정 assertion(무관 관계 0건)까지 검증한다. Architect 별도 작업 소유권과 겹치면 결함 근거와 SDD를 먼저
  보존하고 소스 정렬 후 반영한다.

## Acceptance

전체 inventory와 ledger 행 수 일치, 미분류 0, 옛 이름 0, public import/compile/full unit/prompt hash,
공식 Analyzer 재시작, REST/MCP/Analyzer UI와 Architect 연동 회귀가 모두 통과한다.
단, 사용자가 `project_temp`에서 실행 중인 서버·포트는 건드리지 않고 별도 포트와 산출물로 격리한다.

## Final target-tree binding

`D:/work/robo/세서비스-최종트리.md` §1과 §4는 이 spec의 목표 구조다.
이 문서는 정적 목표일 뿐 소스 코드보다 우선하는 정답은 아니며, 완료 구현·감사대장·실행 가능한
검증기가 최종 판정 근거다. 이전 spec 058 구조 테스트의 통과만으로 이 계약을 대신하지 않는다.

- `source/{file_cache,path_resolver}.py`와 `external/catalog.py`를 실제 소유
  경계로 만들고 옛 `pipeline/{source_file_cache,catalog_client}.py`를 남기지 않는다.
- `pipeline/{strategies,languages}.py`와 `pipeline/models/`를 실제 최종 경로로
  만들고 옛 `analysis_pipelines.py`, `programming_languages.py`, `data_models/`를
  호환 shim 없이 제거한다.
- Step2 reader/reference 책임, Step4 table/call 책임, Step6 entry/framework/sink와
  batch planning 책임을 문서의 분해선대로 실제 파일로 분리한다.
- graph domain-term 소유권과 example orchestration, search ranking, LLM 중간
  `client/` 계층, observability logger 분해, 죽은 scripts/files를 문서대로 정리한다.
- D-1 이후 생산자가 생긴 `control_subtree` 계열처럼 §1 작성 뒤 목표 트리가
  의도적으로 살린 경로는 삭제하지 않고 충돌표에 후속 명세 근거를 기록한다.
- 현재 runtime 파일 전부가 최종 유지·이동/분해·삭제 중 하나로 파일 단위
  매핑되어야 하며, 미분류 0과 required missing 0/forbidden present 0을 기계 검증한다.
