# spec 125 — Architect GWT 근거 계약 정합

## 문제

- 현행 RULE은 `anchor_line/cond/then/nl` 계약인데 MCP 목록 변환기는 폐기된
  `statement`를 읽는다. 규칙 N개가 빈 문장 하나로 합쳐져 아키텍트가 규칙 좌표와
  의미를 받지 못한다.
- `node_detail`은 선택 함수 하나에 전체 소스·내부 속성·모든 관계·RULE을 함께 보내
  shopmall 실측 17.1~53.1KB/노드다. 검색과 상세 2~3개를 합치면 한 GWT 판단에
  약 73~140KB가 든다.
- 분기 조건과 직접 효과는 이미 결정론 증거인데 모델이 다시 선택한다. shopmall
  1,609행 중 조건 오선택 28, 테이블 효과 오선택 27, 직접 대입 누락 8,
  반환 누락 1로 64행이 격리됐다.
- AST 복합대입 10개(`|=` 8, `+=` 2)의 연산자가 직접 대입 증거에서 사라져
  `disc += 2000`이 `disc = 2000`처럼 구조화될 수 있다.
- datasource 샘플은 분석 입력으로 쓰이지만 그래프에 보존되지 않아 아키텍트가
  실제 데이터 표본을 재사용할 수 없다.

## 사용자 시나리오

1. 아키텍트가 검색 목록에서 현행 RULE의 파일 행과 한국어 의미를 읽고 후보를 고른다.
2. 선택 노드는 전체 소스 대신 GWT 판단에 필요한 좌표, 직접 CALL/R/W,
   관련 테이블·컬럼·분석 시점 샘플만 압축 조회한다.
3. 일반 디버깅 사용자는 기존 전체 `node_detail`을 계속 사용할 수 있다.
4. RULE의 조건·직접 효과는 모델 선택과 무관하게 parser/linker 정본과 일치한다.

## 채택 계약

1. MCP 목록 RULE은 현행 정본에서 `line`, `text(nl)`, 규칙 단위 table 효과만
   투영한다. 폐기된 `statement` 호환 분기는 두지 않는다.
2. `node_detail(..., view="gwt")`는 기존 reader의 같은 Neo4j 결과를 압축 투영한다.
   새 Cypher 해소기나 두 번째 검색 경로를 만들지 않는다.
3. GWT 상세은 `id/name/labels/summary`, `file_path/start_line/end_line`, 직접
   `calls/called_by`, `reads/writes`, 해당 테이블의 컬럼과 최대 5개 샘플행만 보낸다.
   전체 `code_text`, embedding, 내부 분석상태, PARENT_OF/HAS_RULE 원형은 제외한다.
4. 기본 view는 기존 full 계약을 유지한다.
5. datasource 발견 시 이미 조회한 read-only 샘플을 TABLE에 JSON snapshot으로
   기록한다. DDL·Catalog 설명 권위는 변경하지 않으며 샘플 부재는 빈 배열로 명시한다.
6. RULE wire에서 모델은 `nl`만 소유한다. `cond/then`은 같은 `rule_evidence`의
   `when/direct`를 정규화 경계가 그대로 물질화한다.
7. 복합대입은 AST `operator`를 보존해 `target <op> rhs` 의미로 직접 효과를 만든다.

## 성공 기준

- shopmall 검색 응답에 빈 `statement` 규칙 0, 모든 목록 RULE에 양의 행 좌표와
  비어 있지 않은 `text`가 있다.
- GWT 상세은 현 full 상세 대비 바이트 70% 이상 감소하고 전체 소스 바이트는 0이다.
- 1,609 의무행의 구조 필드는 결정론 정본과 100% 일치하며 구조 선택 사유 finding은 0이다.
- `disc += 2000`, `flag |= FLAG_FREESHIP`, `fee += JEJU_EXTRA_FEE`의 효과가
  단순 대입으로 축퇴하지 않는다.
- 테이블 샘플은 분석 시 사용한 snapshot과 byte-equivalent JSON이고, datasource가
  없으면 조용히 창작하지 않는다.
- 기존 full detail과 검색 선정 ID/순서에는 무회귀가 없다.

