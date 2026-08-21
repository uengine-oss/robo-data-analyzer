# Strategy A 실제 데이터 1단계 판정 결과

**Date**: 2026-08-21
**Decision**: 실패 — 20함수/77호출 단계로 확대하지 않는다.

## 1. 실행 무결성

- 실제 shopmall source 11/11 SHA-256 일치
- 사전 봉인 입력: 4함수, 15 prompt records, 104 exact rows, 18 gold cases
- selected prompt ledger SHA-256:
  `75292142cb95b9980b1d3064857a517cbfbfcb4dd43ab4fbebe36a224ee4182e`
- current model 호출: 역사 wire control 15 + 상한 제거 candidate 15 = 30, content retry 0
- HTTP 200/finish `stop`/JSON/row coverage: 두 variant 모두 15/15, 15/15, 15/15, 104/104
- run: `_runs/framework/strategy-a/strategy-a-stage1-current-20260821-r1`
- `results.jsonl` SHA-256:
  `cea2b50705e8a1df94a166a97434326b822d7561211757ba07ef77cfc84345dc`
- 실행 당시 `summary.json` SHA-256:
  `632d97b8e3c7f3ee9fa6a56d05821caf9801a7ff96b763b755d583de5550889b`
- `manifest.json` SHA-256:
  `3e813fe1ffd67f40d12b5f0ed4cba8e3baf84360f36f5df4e082f3e85d038a0e`

실행물은 다시 쓰지 않았다. 아래 수치는 저장된 raw 응답을 현재 checker로 read-only 재판정한 값이다.

## 2. 자동·결정론 대조

| 모델/배선 | 반복 | 완전 유효 record | Python 문법 실패 | 근거 closure 실패 | 행 회계 |
|---|---:|---:|---:|---:|---:|
| 이전 모델 + 역사 wire | 1 | 8/15 | 13 | 11 | 104/104 |
| 이전 모델 + 역사 wire | 2 | 11/15 | 5 | 10 | 104/104 |
| 이전 모델 + 역사 wire | 3 | 9/15 | 14 | 11 | 104/104 |
| 현재 모델 + 역사 wire | 1 | 7/15 | 12 | 18 | 104/104 |
| 현재 모델 + 상한 제거 | 1 | 7/15 | 12 | 18 | 104/104 |

- 같은 역사 wire에서 현재 모델의 완전 유효 record 7/15는 이전 모델의 세 번 결과 8/15, 11/15,
  9/15보다 모두 낮다.
- 현재 모델에서 `maxLength`만 제거해도 7/15 → 7/15로 변하지 않았다. 임의 상한 제거는 원칙상
  필요하지만, Strategy A 정확도 결함의 단독 원인은 아니다.
- candidate의 Python 실패 12건은 빈 `then_relation` 9건과 C 표기(`0L`, `&value`, `(void *)&value`)를
  그대로 Python 식에 넣은 3건이다.
- candidate의 근거 closure 실패 18건은 최종 반환부에서 row-local symbolic evidence를 literal
  `0000/9001/9003/9009`, `0/-1/-3`으로 바꾼 건이다. 전체 prompt의 referenced symbol에는 값이 있으므로
  직접 의미 판정에서는 창작으로 세지 않았지만, 계약된 해당-row closure는 통과하지 못했다.

### checker 반증과 정정

최초 candidate grounding 수치는 19였다. `R596`의 source evidence `"'\\0'"`와 Python AST 값 NUL을
문자 표면형으로 비교한 checker 오탐 1건을 발견했다. quoted literal을 `ast.literal_eval`로 해석한 뒤
비교하도록 red test를 추가했고 8/8이 통과했다. 저장 raw를 재판정한 최종 수치는 18이며 모델을 다시
호출하지 않았다.

## 3. 봉인 18건 직접 의미 판정

판정은 결과를 보기 전에 봉인한 gold case의 source 범위와 candidate 104행을 직접 대조했다.

| # | 함수와 source | actual 핵심 | 판정 | 이유 |
|---:|---|---|---|---|
| 1 | `member_authenticate:595-620` | `R603/R613 ret == RET_INVALID`, `strcpy`만 표시 | partial | 입력 거부는 맞지만 `AUTH_RSLT_MISS` 값 전달은 빠졌다. |
| 2 | `member_authenticate:645-697` | not-found/fail/denied/limit 반환 분기 존재 | partial | 반환은 맞지만 MISS/EXPIRE/OVER 결과값이 `strcpy` 호출로만 축약됐다. |
| 3 | `member_authenticate:704-735` | `R730 semantic_status=unreachable` | contradiction | source 729~733에서 SMS 실패는 실제 도달 가능하고 실패를 무시한 뒤 성공 처리한다. |
| 4 | `member_authenticate:738-754` | empty/`@` 없음/정상 조건 존재 | partial | 성공 조건은 맞지만 설정되는 결과값이 없다. |
| 5 | `member_authenticate:756-776` | depth와 dispatch 음수 분기 존재 | partial | 분기는 맞지만 EXPIRE/MISS/OK 결과값이 없다. |
| 6 | `member_authenticate:780-803` | mobile 성공 조건에서 `db_exec` 존재 | partial | 마지막 로그인 갱신의 정확한 대상·값·순서는 표현되지 않았다. |
| 7 | `member_authenticate:835-845` | 두 auth-type 모두 history 호출, 실패 시 log만 수행 | exact | 성공/실패와 무관한 이력 시도와 결과 불변을 보존했다. |
| 8 | `member_authenticate:848-881` | 네 결과별 return mapping, output은 `strcpy`만 표시 | partial | 반환 mapping은 맞지만 `result_code_out`에 복사되는 실제 값 관계가 빠졌다. |
| 9 | `apply_point_balance:555-586` | 세 입력 거부와 네 유형의 `is_minus` 분기 존재 | exact | 입력 검증과 더하기/빼기 분류를 보존했다. |
| 10 | `apply_point_balance:591-609` | not-found와 query error 반환 분리 | exact | 두 실패 경로와 반환값이 source와 같다. |
| 11 | `apply_point_balance:611-625` | withdrawn + earn 차단, minus 경로 통과 | exact | 탈퇴 회원의 적립 차단과 차감 허용을 보존했다. |
| 12 | `apply_point_balance:627-641` | 잔액 부족 거부와 +/- 새 잔액 식 존재 | exact | 비교 방향과 두 계산식이 source와 같다. |
| 13 | `apply_point_balance:643-677` | sequence/insert 실패는 있으나 성공·순서가 분산 | partial | 양수 affected-count 의무와 실행 순서가 한 관계로 닫히지 않았다. |
| 14 | `apply_point_balance:679-707` | update 실패·성공과 `RET_OK` 존재 | partial | 정확한 갱신 대상·값·순서가 해당 행에 연결되지 않았다. |
| 15 | `next_ship_status:188-197` | null/empty 반환은 맞고 destination은 `strcpy` | partial | 빈 상태의 다음 값 `RECV`가 빠졌다. |
| 16 | `next_ship_status:203-214` | 네 source-state와 `RET_OK`, destination 값 0건 | omission | 핵심인 `GATHER/TRUNK/OUT/DONE` 다음 상태가 전부 누락됐다. |
| 17 | `next_ship_status:215-224` | DONE/RETURN은 맞지만 `R224 given=True` | contradiction | unknown fallback은 앞선 모든 상태의 complement여야 하는데 무조건 실행으로 표현됐다. |
| 18 | `db_commit:124-128` | `called("log_dbg")`, `ret == RET_OK` | exact | 로그와 무조건 성공 반환만 있으며 업무 R/W를 만들지 않았다. |

합계: **exact 6, partial 9, contradiction 2, omission 1, fabrication 0, needs_context 0**.

## 4. 원인과 판정

첫 오좌표 `R730`은 이전 모델 세 반복과 현재 두 variant가 모두 `unreachable`이다. 상한이나 현재 모델만의
문제가 아니다. deterministic row evidence가 SMS 호출 전의 `not rc < 0`과 호출 후의 `rc < 0`을 같은
시점 조건처럼 결합해 모순 입력을 만들었다. `R224 given=True`도 final fallthrough의 앞선 분기 complement가
row evidence에 보존되지 않은 upstream 결함이다. `R542`는 함수 전체의 서로 다른 시점 호출·대입을
`given=True` 한 행에 모아 temporal relation을 이미 훼손한다.

따라서 Strategy A 표기만 확대하거나 prompt를 고칠 단계가 아니다. `source → AST → deterministic branch/
effect assembly → actual row evidence`에서 시간 순서, call argument/value, complement를 먼저 무손실로
복구한 뒤 같은 봉인 입력을 다시 판정해야 한다. 20함수/77호출 확대는 금지한다.

## 5. 미검증

- 현재 두 variant는 각 1회이므로 현재 모델 반복 안정성은 미검증이다.
- 4함수 결과를 20함수 전체, 다른 corpus, 다른 언어로 일반화하지 않는다.
- Strategy A는 제품 pipeline에 배선되지 않았으므로 Neo4j/API/MCP 제품 결과 정확도 근거가 아니다.
