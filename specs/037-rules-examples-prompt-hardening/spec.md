# Feature Specification: code_rules_examples 프롬프트 하드닝 (환각·기술규칙)

**Feature Branch**: `037-rules-examples-prompt-hardening` (미생성 — WIP 보호)

**Created**: 2026-06-28 · **Status**: Draft

**Input**: "zapamcom 분석 결과(RULE/EXAMPLE)를 원문과 대조하니 ①순수 기술 DBIO 래퍼가 '조회 실패 시 에러 반환' 같은 기술규칙을 생성 ②given/when/then에 원문에 없는 가짜 코드값('CARD'/'FAIL'/'B001')을 지어냄. 두 전략(framework·dbms) 프롬프트를 하드닝."

## 목적 (북극성)

`rules`/`examples` = **코드의 비즈니스 의도를 담아, 그것만으로 코드를 재생성(전환)했을 때 원문과 동일 동작이 나오게** 하는 스펙. 따라서 평가 기준 = **정확도 + 완전성(누락 0)**. 노이즈 제거는 부차, **핵심은 "원문의 모든 도메인 의도를 빠짐없이·정확히 잡았나"**. 검증 = 원문 대비 **누락**을 찾는 것.

**프롬프트 작성 원칙(범용)**: 이 프롬프트는 모든 코드에 적용되는 템플릿 → **지침·예시 모두 범용**으로 쓴다. 특정 코드베이스 값(`CARD`/`op_cl`/`pay_mthd_cd` 등)을 박지 않는다. (2026-06-28 사용자 정정.)

## 배경 — 원문 대조로 확정된 품질 결함 (2026-06-28)

좋은 예(우수): `a000_input_validation`·`b000_main_proc`·`b800_skb_*`·`c300_*` — 실제 코드값(A002/0007/030) 보존, 분기 난사 없이 1~3 Rule로 통합. 프롬프트 의도대로 동작.

결함(순수 기술 DBIO 래퍼):
- **결함 1 (기술규칙 남발)**: `b500`/`b600`/`b700`/`b710`(원문=단일 DBIO 조회 + DBMS오류→RC_ERR + NFD 통과)가 "인증결과코드 조회 실패 시 에러 반환"·"SK-VAN 원장 조회 실패 시 오류 반환" 같은 **기술 관심사 규칙** 생성. 프롬프트 §1.1 FAIL·§1.6a가 이미 폐기대상이라 명시했으나 LLM이 무시.
- **결함 2 (코드값 환각)**: `b500` given `결제수단코드='CARD'`/`'FAIL'`/`'UNKNOWN'`, `b700`/`b710` `'B001'`/`'BANK01'` — 원문에 없는 가짜 영어 토큰. 실제 `pay_mthd_cd`는 숫자 `'1'/'2'/'5'`. (`b800`은 실값 A002/0007 보존=정상이라 비일관.)

## User Scenarios & Testing

### User Story 1 - 기술 DBIO/호출 래퍼의 기술규칙 억제 (Priority: P1)

순수 DBIO 조회·호출 래퍼(도메인 분기 없음, 표준 에러처리뿐)는 `rules: []`·`examples: []`를 내야 한다.

**Acceptance**: `b500`/`b700` 류 재분석 시 "조회 실패 시 에러 반환" 기술규칙 = 0. 단 조회/저장에 도메인 코드값 매핑·정책이 있으면(예 `b200` op_cl→'XXXX') 그 부분만 Rule(억제 X).

### User Story 2 - given/when/then 코드값 환각 금지 (Priority: P1)

given/when/then의 코드값 리터럴은 원문에 실제 등장한 값만 쓰고, 없으면 자연어로만 표현한다.

**Acceptance**: 재분석 시 원문에 없는 가짜 코드값('CARD'/'FAIL'/'UNKNOWN'/'B001') = 0. 실제 리터럴('0000'/'A016'/'030')은 보존. 숫자 도메인값은 숫자로.

### Edge Cases

- 도메인 가치 있는 DBIO(코드값 매핑·정책 분기 포함)는 억제 대상 아님 — 그 부분만 Rule.
- 원문에 대표 코드값이 아예 없는 경우: 자연어 서술만(가짜 값 채우기 금지).

## Requirements

- **FR-001**: 두 프롬프트(`prompts/dbms/code_rules_examples_batch.yaml`·`prompts/framework/code_rules_examples.yaml`)의 `user_template_specific` [0] 절대원칙에 **"기술 DBIO/호출 래퍼 → 즉시 빈 배열"** 게이트를 추가한다(식별 기준 + 도메인 가치 있으면 예외).
- **FR-002**: 동 [2] examples 절에 **"given/when/then 코드값은 본문 실제 등장값만, 가짜 생성 금지"** 지침을 추가한다.
- **FR-003**: 변경은 `user_template_specific`(분할 고유)에만 — 공통 `system`(_common.yaml) 불변 → KV 캐시 prefix 무손상([[principles]] §8).
- **FR-004**: framework·dbms 양쪽에 동등 적용(두 전략 모두).
- **FR-005**: 모든 추가 지침·예시는 **범용**(특정 코드값/식별자 미포함). 기술래퍼 억제 지침에도 "도메인 의도 있으면 반드시 Rule(누락 금지)" 균형을 명시 — 억제가 누락으로 번지지 않게.
- **FR-006 (누락 감사)**: 최종 프롬프트 보강은 **재분석 결과 vs 원문 완전성 감사**에서 발견된 누락에 근거한다. ★1순위 의심 = 현 프롬프트의 **강한 통합 상한**(§1.3 "Rule 4 넘으면 의미합성 사고" / §1.5 "Rule 8 넘으면 분해 question")이 진짜 복잡한 함수(예 다분기 메인 처리)에서 **서로 다른 도메인 결정을 압축·누락**시킬 위험. 감사에서 누락 확인되면 상한을 "가치 있으면 개수 무제한, 노이즈만 억제"로 정밀 조정(폭주 방지와 균형).

## Success Criteria

- **SC-001**: zapamcom 재분석 후 순수 DBIO 래퍼(b500/b600/b700/b710)의 기술규칙 = 0, 가짜 코드값 = 0.
- **SC-002**: 우수 함수(a000/b800/c300)의 기존 품질(실값 보존·통합) 회귀 0.
- **SC-003**: 출력 폭주(공백/반복) 재발 0, KV 캐시 byte-identical 유지(`check_byte_identical.py`).

## Plan & Tasks (compact)

**수정 지점 (텍스트 추가만, 로직 0)**:
- T1 `prompts/dbms/code_rules_examples_batch.yaml`: §0에 "0.7 기술래퍼 게이트"(P1) + [2]에 "코드값 환각 금지"(P2).
- T2 `prompts/framework/code_rules_examples.yaml`: §0에 "0.7 기술래퍼 게이트"(P1) + §2.3에 "코드값 환각 금지"(P2).
- T3 검증: `check_byte_identical.py`로 3분할 system byte-동일 확인(KV) → 재분석 → 원문 재대조(SC-001/002).

**KV 캐시 안전**: 추가는 분할 고유 user 구역 → system prefix 불변.
