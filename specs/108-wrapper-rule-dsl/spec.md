# Feature Specification: wrapper 규칙 DSL — 에이전트 산출물의 구조화

**Feature Branch**: `108-wrapper-rule-dsl`

**Created**: 2026-07-30

**Status**: Draft

**Input**: User description: "규칙이 잘못되면 안 되는 건 당연하므로 DSL 적용. 단 (a) 기존 스킬
전부를 DSL로 100% 표현 가능함을 먼저 증명(proframe lookbehind 포함), (b) raw 정규식 탈출구
유지(동일 검증기 통과 필수)."

## 근거 (착수 전 전수조사 — 2026-07-30)

기존 스킬 6종의 `table_access_patterns` 정규식 10개 전수 수집 결과:

- **표현해야 할 형태는 3종뿐**: ① wrapper 문자열 키(proframe ×2 — entity+구분자+op문자+숫자,
  `(?<!_s)` 제외 접미사 포함) ② 메서드 호출(리시버 접미사/고정 리시버 + op prefix 집합 +
  첫 인자 문자열 entity — ejb·jpa·mybatis·ofbiz ×5) ③ SQL 조각(C 문자열 개행 gap 허용 —
  shopmall ×4... 중 1개 중복 제외).
- **실증 결함 발견**: jpa `jpa_repository_call`, ejb 패턴 2개, mybatis `mapper` 패턴에
  `(?P<entity>…)` group 부재 — 링커는 entity 없으면 스킵하므로 **써진 그대로는 엣지 0인
  죽은 규칙**이다 (verify가 걸러내 사고는 미발생, 그러나 스킬 캐시 불량). 스킬 작성자조차
  named group을 빠뜨렸다 — 손 정규식의 형태 오류가 실재함의 증명이며 DSL 채택의 직접 근거.
- ejb `cmp_setter` 패턴은 entity 원천 자체가 없음(리시버=인스턴스 변수명) — 어떤 표현으로도
  테이블 링킹 불가능한 **원천 무효 규칙**. DSL이 이런 규칙을 형태상 거부하는 것이 옳다.

## User Scenarios & Testing

### US1 - 에이전트는 빈칸을 채우고, 정규식은 코드가 만든다 (P1)

스캔 에이전트가 raw 정규식 대신 구조화 규칙(3종 템플릿 중 택1 + 필드)을 제출하면, 코드가
정규식을 기계 생성해 기존 검증기(`verify_table_patterns`)로 동일하게 검증·사용한다.

**Acceptance Scenarios**:
1. **Given** proframe형 규칙(entity+`_s`제외+op문자셋+숫자4), **When** 컴파일, **Then** 생성
   정규식이 기존 수기 정규식과 **행동 동일** — 본 R/W 매치·시퀀스 변종 거부·`_s` 끝 entity 거부.
2. **Given** 형태에 안 맞는 규칙(필수 필드 누락·빈 문자셋), **When** 컴파일, **Then** 즉시
   명시 오류 (조용한 통과 금지).

### US2 - 기존 스킬 전부가 DSL로 이사한다 (P1 — 채택 조건 a)

유효한 기존 패턴 9개 전부를 DSL로 표현하고, 각각 원본 정규식과의 행동 동일성을 테스트로 증명.
(원천 무효 1개(ejb setter)는 표현 불가가 아니라 **규칙 자체 무효** — 스킬에서 사유와 함께 제거.)

### US3 - raw 정규식 탈출구 (P2 — 채택 조건 b)

DSL 템플릿에 안 들어가는 커스텀은 종전처럼 raw `call_regex` 제출 가능. 단 동일 검증기 통과
필수(현행 그대로). 에이전트 지침은 "DSL 우선, 불가 시에만 raw + 사유".

### Edge Cases

- 컴파일 산출 정규식은 반드시 `<entity>`·`<op_token>` named group 포함 — 죽은 규칙 재발 원천 차단.
- 템플릿 확장 요구(새 형태)는 조용히 필드 추가 금지 — 별도 spec (D7 미니 언어 방지 승계).
- 프롬프트 `agents/scan.md`는 SHA256 핀 고정(CI byte_identical) — 변경 시 해시 갱신 동반 필수.

## Functional Requirements

- **FR-001**: `TableAccessRule`(3종 discriminated union) 계약 신설 — wrapper_key /
  method_call / sql_fragment. 컴파일러는 규칙 → `call_regex` 문자열 생성 후 기존
  `TableAccessPattern` 경로(검증·링킹)에 합류시킨다 — 다운스트림 무변경.
- **FR-002**: 컴파일 산출물은 `<entity>`·`<op_token>` named group을 항상 포함 (구조상 보장).
- **FR-003**: `ScanResult.table_access_rules` 추가(기본 []) — 기존 `table_access_patterns`는
  탈출구로 유지. `_finalize`가 규칙 컴파일 → 패턴 목록에 병합 → 기존 verify 단일 관문.
- **FR-004**: 기존 스킬 6종 마이그레이션 — 유효 9패턴 DSL화 + 행동 동일성 테스트, entity 누락
  4패턴은 이사 과정에서 교정(리시버 entity), 원천 무효 1패턴은 사유 명기 후 제거.
- **FR-005**: scan.md 프롬프트 갱신(DSL 우선 지침) + prompt_hashes.json 동반 갱신.
- **FR-006**: 컴파일 실패는 명시 raise — 조용한 skip 금지 (헌법 IV).

## Success Criteria

- **SC-001**: 유효 기존 패턴 9/9가 DSL 표현 + 원본과 행동 동일성 테스트 green (조건 a 증명).
- **SC-002**: 컴파일 산출 전부에 named group 2종 존재 (구조 테스트).
- **SC-003**: raw 탈출구 경로 회귀 테스트 green (조건 b).
- **SC-004**: 전량 pytest green (기준선: 사전 존재 실패 1건 외).
- **SC-005**: CI byte_identical 게이트 통과 상태 유지 (해시 정합).

## Assumptions

- 실 LLM 에이전트가 DSL을 실제로 잘 채우는지는 실기동 검증 항목 (세션 미가용 — 미검증 기록).
- 스킬 문서의 yaml 블록 형식 변경은 에이전트가 읽는 문서라 프롬프트 캐시와 무관 (user 메시지 suffix).
