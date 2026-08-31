# ROBO Data Analyzer

ROBO Data Analyzer는 ANTLR Parser가 만든 구조 사실과 원문 범위를 받아 사용자 코드의
구조·데이터 접근·업무 의미를 분석하고, 검증된 현재 세대를 Neo4j 제품 그래프로 공개하는
서비스입니다.

현재 설계의 정본은
[`specs/131-cross-node-semantic-grounding/README.md`](specs/131-cross-node-semantic-grounding/README.md)에서
시작합니다. 과거 번호별 spec과 실험 보고서는 현행 계약이 아닙니다.

## 책임 경계

- ANTLR Parser: 언어별 문법 구조, 중첩, 이름 조각, 정확한 원문 범위를 생산합니다.
- Analyzer: binding, type, linkage, 사용자 함수 연결, 테이블 접근, 업무 의미를 결정합니다.
- Neo4j: Analyzer가 확정한 현재 generation만 제품 결과로 공개합니다.

Analyzer는 원문을 정규식으로 다시 파싱해 Parser 사실을 보정하지 않습니다. 업로드 범위 밖
호출은 `external` 발생으로 회계하며, unresolved/external 대체 노드나 관계를 만들지 않습니다.
제품 `CALLS`는 같은 분석 범위에서 정의가 증명된 사용자 작성 루틴 사이에만 생성합니다.

## 실행 흐름

| 순서 | 입력 → 출력 | 책임 |
|---:|---|---|
| 1 | DDL·실행 설정 → 스키마 및 봉인 입력 | 입력과 설정을 한 번 고정하고 DDL을 구조화합니다. |
| 2 | Parser JSON → 코드 구조와 구조 사실 | AST 노드, 호출·선언·변수·범위 사실을 원문과 결합합니다. |
| 3 | 외부 파일 → 원문 조각 | 설정·문서의 실제 범위를 보존하며 필요한 조각만 준비합니다. |
| 4 | 프로젝트 표본 → 검증된 Framework 계약 | Framework 전략만 한 번 scanning하고 실제 검증된 패턴만 채택합니다. |
| 5 | 구조 사실·검증 계약 → 관계와 exact-once ledger | 테이블·호출·외부 참조를 결정론적으로 연결합니다. |
| 6 | 코드·관계·사용된 값 → 함수별 비중복 문맥 | 줄 번호 원문, 사용 테이블/컬럼, 변수/상수, 하위 함수 효과를 조립합니다. |
| 7 | 함수 문맥 → Summary·Table Usage·Rule Meaning | 세 의미 concern을 중복 없이 병렬 처리하고 구조화된 응답으로 받습니다. |
| 8 | 함수 결과 → 모듈·컴포넌트·테이블·검색 제품 | 결정론적으로 집계한 뒤 한 generation을 원자적으로 공개합니다. |

Framework와 DBMS는 입력 방식이 다르지만 호출 의미 판정은 모두
`Parser facts → language semantic adapter → neutral resolution core` 한 경로를 사용합니다.

## LLM 입력과 출력

LLM에는 내부 fact ID, 원문 byte offset, resolution ledger 같은 결합용 메타데이터를 보내지
않습니다. 의미 판단에 필요한 다음 내용만 concern별로 한 번 전달합니다.

- 줄 번호가 붙은 대상 원문
- 사용된 테이블·컬럼과 접근 종류
- 실제로 참조한 변수·상수 값
- 필요한 모듈·Framework 계약
- 현재 함수가 소비하는 하위 함수의 확정 효과

출력은 자유로운 증거 복창이 아니라 닫힌 구조입니다.

- Summary: 함수의 canonical 설명과 역할
- Table Usage: 함수와 테이블 사이의 업무상 사용 의미
- Rule Meaning: Analyzer가 배정한 조건·효과 atom의 의미
- Example: Rule 구조에서 결정론적으로 합성하며 별도 LLM 호출을 하지 않음

세 concern은 byte-identical 공통 system prefix를 공유해 KV cache를 사용할 수 있고, 서로의
완료를 불필요하게 기다리지 않습니다. 같은 대상을 다른 표현으로 재질의하거나 정상 실패를
이유로 동일 요청을 반복하지 않습니다.

## 제품 저장 규칙

- 한 분석 scope의 새 결과는 새 generation에 기록합니다.
- 노드·관계·분석 health·발생 ledger는 호출자가 소유한 한 transaction으로 기록합니다.
- 모든 stage가 성공한 뒤에만 active generation pointer를 바꿉니다.
- 실패하거나 중단되면 transaction을 rollback해 이전 active generation을 보존합니다.
- 새 Neo4j database를 자동 생성하지 않으며, 명시된 기존 database만 사용합니다.

## 주요 디렉터리

| 경로 | 내용 |
|---|---|
| `agents/` | Framework scanning과 검증 도구 |
| `pipeline/steps/` | 입력부터 후처리까지 실행 단계 |
| `pipeline/steps/step2_load_ast/` | Parser input orchestration |
| `pipeline/analysis_ir/` | Parser facts, language adapters, neutral resolution |
| `pipeline/steps/step5_linking/` | deterministic linking orchestration |
| `pipeline/steps/step6_build_context/` | 함수별 LLM 문맥 조립 |
| `pipeline/semantic_tasks/{summary,table,rule}/` | family-owned prompt, schema, validator, and projection |
| `pipeline/publication/` | 제품 publication stage 계획 |
| `graph/` | Neo4j schema, writer, generation publication |
| `llm/` | provider transport, binding, cache, usage, runtime capability |
| `search/` | 공개된 검색 segment 조회와 ranking |
| `cli/` | 운영자용 공식 분석 CLI |
| `tools/verify/` | 봉인 run의 read-only 검증 도구 |
| `tools/admin/` | 명시적 안전 gate가 필요한 관리 도구 |
| `specs/131-cross-node-semantic-grounding/` | 유일한 현행 SDD와 계약 |
| `tests/` | 결정론 계약, 오류 주입, 통합 검증 |

## 실행

Workspace 운영 명령을 사용하는 것이 기본입니다.

```powershell
cd D:\work\robo\robo-workspace
.\robo.cmd up analyzer
```

Analyzer만 직접 실행할 때:

```powershell
cd D:\work\robo\project\robo-data-analyzer
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 15502
```

공식 CLI로 명시된 data directory를 분석할 때:

```powershell
.\.venv\Scripts\python.exe cli\analyze_data_dir.py --data-dir D:\path\to\data --database test
```

CLI는 `source/`, `analysis/`, 선택적인 `ddl/` 구조를 사용합니다. 전체 옵션은
`cli\analyze_data_dir.py --help`에서 확인합니다.

## API

| 경로 | 역할 |
|---|---|
| `GET /` | 서비스 health |
| `POST /robo/analyze` | 분석 시작과 NDJSON 진행 스트림 |
| `POST /robo/analyze/control` | 진행 제어 |
| `POST /robo/search/semantic` | 공개 generation 의미 검색 |
| `GET /robo/source` | 허용된 원문 범위 조회 |
| `/robo/mcp` | 같은 검색 제품을 사용하는 MCP endpoint |

## 검증

작은 영향 영역부터 테스트하고 관련 변경이 모두 끝난 뒤에만 전체 회귀와 실제 corpus를
확대합니다.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest tests\pipeline\step5_linking -q
.\.venv\Scripts\python.exe -m pytest tests -q
Remove-Item Env:PYTHONDONTWRITEBYTECODE
```

Neo4j integration은 기존 test database에서 격리된 scope를 사용하고 항상 rollback하는 opt-in
검증만 자동 회귀에 포함합니다. 실제 SKT 전체 재분석과 성능 측정은 입력·설정·정답지를 봉인한
마지막 검증에서 한 번만 수행합니다.

제품 CLI는 [`cli/README.md`](cli/README.md), 검증·관리 도구는
[`tools/README.md`](tools/README.md)를 따릅니다.
