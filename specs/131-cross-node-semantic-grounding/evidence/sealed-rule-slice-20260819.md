# APAS Rule 의미 소유권 봉인 slice — 2026-08-19

## 봉인 시점과 목적

새 `RULE.BUSINESS_MEANING` 응답을 보기 전에 2026-08-19 03:20 KST에 고정했다. 기존 actual APAS
run `20260818_173654_12656da7f405`의 저장 prompt/evidence를 그대로 재사용하고, current system/user
고정 계약과 row-owned assignment/schema만 교체한다. source·condition/effect·symbol·callee·R/W
evidence는 바꾸지 않는다.

## 첫 봉인 좌표 무효화

처음 표는 JSONL 물리 행 순서로 index를 계산했지만 공식 replay 도구의 `load_rule_records()`는
`sort_key`로 정렬한 뒤 `--index`를 적용한다. 첫 병렬 호출 직후 출력한 recorded user SHA가 표와
다른 것을 확인해 즉시 중단 판정했다. 실행된 네 응답은 표본 선정 계약과 조인되지 않으므로 pilot이며
합격·성능 수치에 사용하지 않는다. 제품 의미가 아니라 검사 좌표계 결함이고, 아래 표는 공식 loader와
같은 좌표계로 응답 열람 전에 다시 봉인한 정본이다.

## 선택 규칙과 고정 대상 — 공식 loader 좌표 정본

결과를 보고 고르지 않도록 다음 네 유형을 고정했다.

| 유형 | record index | recorded user SHA-256 앞 12자 | assigned rows | old slots | old input/output | old latency |
|---|---:|---|---:|---:|---:|---:|
| 단일 행 최소형 | 92 | `a68b3bddf9e6` | 1 | 2 | 5,841 / 163 | 15,750ms |
| 첫 중간형 | 0 | `05d55591120e` | 9 | 12 | 8,064 / 759 | 75,641ms |
| request-local key 누출 전수 포함 record | 77 | `77ffcb594380` | 6 | 11 | 5,096 / 637 | 62,969ms |
| old output 최대형 | 98 | `4f4f313e17ff` | 8 | 12 | 7,210 / 1,599 | 158,406ms |

## 사전 판정 기준

- current slot 수는 assigned row 수와 같고 old slot보다 작아야 한다.
- current response에 digest-scoped private wire key와 private C/E ref가 사람이 읽는 meaning으로 남으면 실패다.
- 각 row는 누락·중복 없이 terminal이어야 한다. insufficient는 실패를 숨긴 것이 아니라
  missing_context의 타당성을 직접 판정한다.
- source/condition/effects/좌표는 저장 evidence와 불변이어야 한다.
- output token과 E2E가 줄어도 업무 의미 omission/fabrication/contradiction이 생기면 채택하지 않는다.
- 네 record의 병렬 wall은 가장 늦게 끝난 호출로 기록하되, 서버 공유 부하 때문에 전체 APAS wall로
  일반화하지 않는다.

## 실행 후 기록

### 후보 1 — 별도 assignment + row table

- 병렬 wall `18.046초`, 4콜, output `3,158 → 1,929`.
- `R961(ret == 'E')`의 극성이 뒤집혔고 direct-empty `R2357/R2445`가 종료 동작을 창작해 기각했다.
- 오행 8개를 singleton closure로 한 차례 역실험한 결과 `23.149초`에 행 소유권이 복구됐다. 따라서
  전체 singleton 전환이 아니라 떨어진 assignment↔row 조인이 공통 원인임을 확인했다.

### 후보 2 — row-owned output wire

- 병렬 wall `19.857초`, 4콜, input 합계 `26,201`, output 합계 `2,141`, deadline/retry 0.
- wire 교차는 제거됐지만 `R396`의 inherited path 극성 반전, `R399`의 child condition 흡수,
  `R2357/R2445`의 무근거 종료 창작이 남아 기각했다.
- source→evidence 역추적 결과 `R2357/R2445`는 `if (rc != RC_NRM) { ; }`이며 `direct`,
  `nested_control`, termination이 모두 없다. `R396/R423`도 직접 동작 없이 child control만 가진 shell이다.

### 확정 원인과 다음 후보

모든 required row에 non-empty `RULE.BUSINESS_MEANING`을 강제해 구조 전용/no-op 행에도 LLM이 동작을
만들어야 했고, semantic row에는 full inherited path와 child 구조까지 노출해 다른 행 의미를 복사하게
했다. 다음 후보는 직접 statement/call/effect가 있는 행만 semantic slot으로 보내고, provider view는
focus condition/direct/source로 닫는다. 구조 전용 행은 canonical condition과 좌표를 가진 Rule로
결정론 물질화한다. 이 계약을 Framework/DBMS 양쪽에 구현·국소 반증한 뒤 같은 4-record를 한 번만
재실행한다.

### 후보 3 — semantic row만 전송한 첫 replay: 구조 결함은 제거, target-wide 문맥 오염 발견

- 도구 출력 절단으로 첫 병렬 응답은 관측값이 유실됐다. 제품의 내용 재시도가 아니라 stdout-only
  진단 결과 회수를 위해 같은 봉인 record를 순차 1회 측정했다.
- old `37 slot / input 26,211 / output 3,158`에서 current `17 slot / input 19,311 /
  output 1,493`으로 줄었다. E2E는 index 92/0/77/98 각각 `1.3/5.0/1.9/3.0초`, deadline/retry 0이다.
- direct-empty 구조 행 7개는 모델 입력에서 사라져 이전의 무근거 종료 창작과 parent/child wire 교차는
  재현되지 않았다.
- 그러나 index0 `R399`는 `strncmp(...,"N")==RC_NRM`을 “N이 아닌 경우”로 뒤집었고, 같은 record의
  다른 행은 현재 행에 직접 연결되지 않은 callee 의미를 가져왔다. index98은 근거가 `bar_cd`라는
  opaque 이름뿐인데 “은행 코드”로 확장했으며 generic DBIO 이름에서 조회/갱신 의미도 만들었다.
- actual prompt를 다시 열어 보니 row packet 밖에 target 전체 symbol 14~37개와 callee interface 최대
  13개, owner-wide R/W/external/API 목록과 의미를 암시하는 target signature/id가 남아 있었다.
  “row-local packet”이라고 부를 수 없는 두 번째 공용 문맥 경로가 확정 원인이다.
- 다음 후보는 이 전역 목록을 제거하고 direct CALL/DATA_OBJECT 또는 exact matched key로 연결된 보조
  근거만 row가 참조한다. sentinel/library 의미가 입증되지 않으면 원 비교식을 보존하고, opaque
  identifier는 확장 번역하지 않는 안전 표현을 함께 고정한다. 같은 원인의 범용 수정은 이 1회 후
  봉인 slice로 종료 판정한다.

### 후보 4 — row-local context 최종 replay: 구조·속도 통과, 의미 계약 기각

- 네 요청을 같은 서버에서 병렬 실행했고 wall은 가장 늦은 요청 기준 `16.809초`였다. 개별 모델
  E2E는 index 92/77/0/98 순서로 `2.5/3.1/6.2/6.5초`, deadline/retry는 모두 0이다.
- old 합계 `input 26,211 / output 3,158`에서 current `input 16,393 / output 1,605`로 줄었다.
  current slot은 `1+3+7+6=17`이며 schema required key와 row-owned wire 누락·추가·중복은 0이다.
- 그러나 index0 `R399`의 실제 조건은 E/F일 때만 각 `*_bank_yn == "N"` 검사가 붙고 X는 독립
  OR인데, 출력은 E/F/X 모두에 관련 플래그 N이 필요한 것처럼 합쳤다. 조건 범위 모순이며 이름만 보고
  “은행 여부”를 덧붙였다.
- index98 `R2335` source에는 `gubun` 분기가 없는데 출력은 “gubun에 따라 DB IO procedure를 선택”한다고
  썼다. `R2425`의 source에는 호출이 한 번뿐인데 “다시 호출”이라고 시간 순서를 추가했다.
- `normalize_frame_rules_response`는 schema/slot/anchor/private-key만 검증하고,
  `grade_rules(... projected_from_semantic_frame=True)`는 `cond/then`을 엄격 대조하지만 `nl`은 개행만
  검사한다. 따라서 위 문장은 실제 production에서 trusted Rule과 canonical frame에 들어간다.

같은 공통 원인의 prompt/packet 수정 1회 후에도 충분한 입력에서 모순·창작이 남았으므로 문구 반복은
종료한다. 소비자 감사에서 Architect GWT gate는 RULE evidence ID와 condition/effects/Example을
요구하고 `RULE.BUSINESS_MEANING`을 요구하지 않았다. 최종 설계는 Rule prose LLM 자체를 제거하고
모든 required row를 결정론 Rule로 물질화하는 것이다. 함수 summary와 callee/table/column 의미 claim은
각 원래 owner에 한 번만 남기며 RULE이 exact evidence refs로 연결한다.
