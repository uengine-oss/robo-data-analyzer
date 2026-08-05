# 07. AST 구조 사실 생산자 계약

## 1. 절대 결정

host-language grammar가 식별할 수 있는 특정 구문은 analyzer가 source 정규식으로 다시 찾지
않는다. `antlr-code-parser`의 언어별 ANTLR listener가 AST JSON으로 emit하고 analyzer는 그
구조 사실을 routine/branch/symbol/table/graph에 연결하고 검증한다.

이 결정은 RETURN 한 건의 패치가 아니다. 언어 지식을 parser 밖으로 새게 하지 않고 새 언어를
listener 추가로 수용하기 위한 cross-service 책임 경계다.

## 2. 2026-08-05 현재 확인한 사실

- C 실제 AST corpus에는 `RETURN` node가 0개다.
- C grammar는 `jumpStatement`, Java는 `returnStatement`, Python은 `return_stmt`, PL/SQL은
  `return_statement`, PL/pgSQL은 `returnStmt`를 이미 구분한다.
- C/Java/Python listener는 해당 return callback을 AST node로 emit하지 않는다.
- PL/SQL listener는 `RETURN`과 `ASSIGNMENT`를 이미 emit한다.
- analyzer는 `branch_inventory.py`에서 첫 토큰, 정규식, statement terminator, 괄호 개수를
  이용해 RETURN/종료/CALL/ASSIGNMENT/entry를 원문에서 재추출한다.
- Rule evidence에는 이 재추출 결과가 `ret` effect로 전달되므로 RETURN을 전혀 안 쓰는 것은
  아니다. 다만 전용 AST RETURN을 쓰는 것도 아니며, 완전성은 한 줄 형태에 제한된다.

## 3. 생산자와 소비자

| 사실 | 목표 생산자 | analyzer 책임 |
|---|---|---|
| RETURN과 반환 표현 | 언어별 parser listener/AST JSON | routine·branch 귀속, evidence ref, 의미 미추론 |
| THROW/RAISE 및 함수 종료 | parser listener/AST JSON | exit obligation과 finding 연결 |
| BREAK/CONTINUE/GOTO/EXIT | parser listener/AST JSON | control-flow obligation 연결 |
| FUNCTION_CALL와 callsite 좌표 | parser listener/AST JSON | symbol scope/import 해석, user/external 분류 |
| ASSIGNMENT의 좌·우 구조 | parser listener/AST JSON | 필요한 구조 검증만; 값·객체 상태 추적 금지 |
| 분기 조건과 제어 소유권 | parser listener/AST JSON | branch path/required row 조립 |
| embedded SQL/문자열/외부 문서 | DDL parser·skill·전용 lexer | table R/W 및 external evidence 연결 |

parser가 node type과 좌표만 주고 analyzer가 다시 식을 파싱해야 하는 반쪽 계약도 피한다. 필요한
표현은 AST JSON의 명시 필드로 보존하되, 정확한 필드명·호환성은 parser spec 016에서 Node 계약과
모든 소비자를 확인한 뒤 확정한다.

## 4. 현재 legacy scanner 감사 대상

최소한 다음을 조사한다. 목록이 전수라고 아직 주장하지 않는다.

- `_termination_rows`
- `_return_expressions`
- `_calls`
- `_assignment_pairs`
- `_entry_statement_line`
- `condition_text`와 source condition 재구성
- source atom/literal/callee 추출 중 parser syntax와 중복되는 부분
- R/W linker 중 host AST가 이미 제공하는 구문과 embedded text를 구분하지 않은 부분

각 항목은 `AST로 이동 / analyzer 연결 유지 / embedded-text owner 유지` 셋 중 하나로 분류한다.

## 5. 전환 순서

1. parser/analyzer 두 저장소의 spec·plan·tasks와 dirty WIP를 고정한다.
2. 지원 언어별 source fixture에 정상·실패·경계 구문을 만든다.
3. 변경 전 source occurrence와 AST 누락을 red test로 고정한다.
4. 각 언어 listener가 공통 AST role/필드를 emit하게 한다.
5. AST JSON vocabulary와 analyzer ontology/reader/context/evidence 소비자를 같은 슬라이스에서 맞춘다.
6. source occurrence ↔ AST node의 누락·추가·wrong-range·wrong-parent 0을 전수 검사한다.
7. analyzer를 AST-only 소비로 전환하고 legacy source scanner와 ontology return keyword를 삭제한다.
8. 전체 parser test, analyzer unittest, prompt hash, 실제 corpus 재파싱, cache-off cold를 실행한다.

구 parser와 신 analyzer 또는 신 parser와 구 analyzer를 섞어 조용히 통과시키지 않는다. 명시적인
version/contract 검증 없이 fallback alias를 두지 않는다.

## 6. 언어 범용성 합격 기준

- parser가 공식 지원하는 C, Java, Python, SQL/PLSQL/PostgreSQL 변형별 계약 fixture 존재
- multiline/inline/value-less return과 nested ownership 검증
- throw/raise와 정상 return의 역할 구분
- call/assignment/control statement의 부모·범위·표현 보존
- analyzer 코드의 언어명/키워드/statement terminator 기반 구문 판정 0
- 새 언어는 listener + AST vocabulary + analyzer role + 생산자/소비자 테스트를 한 묶음으로 추가

## 7. 성능과 정확도 영향

- parser가 한 번 만든 구조를 재사용하므로 analyzer 중복 lexing과 언어별 예외를 줄일 수 있다.
- AST JSON이 커질 수 있으므로 node 수·파일 크기·parse wall·analyzer 입력 토큰을 전후 측정한다.
- 구조 node 전체를 LLM prompt에 복사하지 않는다. Rule에 필요한 좌표/짧은 ref만 보낸다.
- 성능 이득을 이유로 AST occurrence를 생략하거나 정확도를 낮추지 않는다.

## 8. 종료선

- 언어별 listener 구현은 해당 grammar가 제공하는 context까지만 한다. grammar가 구문을 구분하지
  못하면 추정 regex를 추가하지 않고 parser grammar/지원 한계로 기록한다.
- 같은 구문을 parser와 analyzer 양쪽에 병존시키지 않는다.
- 한 언어 fixture 통과를 모든 언어 통과로 일반화하지 않는다.
- parser 출력만으로 의미가 확정되지 않는 상태코드/업무 뜻은 LLM/needs-context 영역으로 남긴다.

## 9. 저장소 상태 주의

`antlr-code-parser`에는 2026-08-05 기준 C `switch/case` 소유권 정규화 dirty WIP가 있다.
`CAstListener.java`, `AntlrParseHarness.java`, `CControlFlowAstTest.java` 변경을 reset/checkout하거나
덮지 않는다. RETURN 작업은 해당 diff와 충돌 여부를 먼저 확인하고 같은 생산자 원칙으로 통합한다.
