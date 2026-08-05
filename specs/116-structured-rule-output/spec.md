# spec 116 — Rule·Example 구조화 출력 v2: JSON 봉투 + 파이썬 표현식 leaf

**Status**: Draft (2026-08-03, current-contract correction 2026-08-05)

> **현재 계약 정정 — 먼저 읽을 것.** 이 문서에는 구현 과정의 폐기된 목표와 실험 원장이
> 함께 남아 있다. 특히 값·객체 추적, arbitrary Python leaf를 결정론 정답처럼 해석하는 설계,
> 내용 재요청, 후보 한 건의 전체 호출 실패는 현재 계약이 아니다. 현재 책임 경계·성능 실측·
> 검증법·정정표는 `docs/handovers/analysis-performance-validation-canonical/README.md`에서
> 시작한다. **아래 §1~§80은 모두 역사 원장**이며, 그 안의 “최종/현행/권위”라는 이름도 당시
> 시점 표현이다. 현재 계약은 §0과 마지막 §81만 사용한다. 과거 절과 충돌하면 현재 코드·로그,
> 헌법 1.3.2, 정정표 순으로 재검증하며 `05-spec116-정정표.md`의 폐기 표를 우선 적용한다.

**운영 핸드오프**: `D:\work\robo\docs\핸드오프\분석시간-정확도-검증-정본`
**증거 실물**: `D:\work\robo\docs\핸드오프\evidence-구조화출력-실측-20260803\v2test\`
(harness.py sha16 `93179c886e4e4cd0` — SYSTEM_PY·grade_py가 3·4단계의 실행 명세)
**선행 spec**: 110 (고정 20함수 의미 정확도). 110의 §5.8 JSON DNF 제안은 본 spec이 실측 근거로 대체한다.

## 0. 현재 권위 계약 — 2026-08-05

- 18분대는 현재 cold 허용선, 11~15분은 정확도 비협상 추가 개선 목표다.
- Rule은 LLM이 branch 좌표별 cond/then/extra/nl 의미 선택을 강제 구조로 출력한다.
- 결정론 코드는 구조 사실·좌표·소속·제한 문법·finding 격리·Example/graph 파생만 맡는다.
- 값/객체 추적, 상태값 의미 추론, 언어별 arbitrary Python 의미 변환, LLM 결과 하이재킹은 폐기됐다.
- 모델 후보 오류는 전체 run fatal이 아니라 좌표별 quality finding이다. 내용 재시도는 없다.
- canonical summary 하나가 사람 표시·검색·bottom-up을 맡되, 현재 purpose-only prompt는 TA-144 결함이다.
- 현재 성능 병목은 Rule output이다. compact regex는 `extra` 의미 차이의 직접 판정 전 미채택이다.
- parser grammar가 식별 가능한 RETURN/CALL/ASSIGNMENT/종료/조건 구문은 언어별 listener가
  AST JSON으로 제공한다. analyzer의 정규식·첫 토큰 source 재탐색은 legacy 폐기 대상이다.
- content retry 0, transport 최대 3 attempts, 범용 prompt/schema 개선 1회 replay,
  후보별 cold 1회(+경계 불명확 시 확인 1회)의 종료선을 지킨다.
- acceptance와 검증 방법은 handoff `01`, `03`, `04`, `06`, `07` 및 tasks TA-146~152를 따른다.

## 1. 목적

LLM이 만드는 비즈니스 규칙(Rule/Example)을 자연어 문장 대신 **JSON 봉투(행·anchor) +
파이썬 표현식 leaf**로 출력하게 하고, 파서가 소스에서 자동 생성한 분기표(정답지)와
**결정론 채점**(뺄셈·등호·소속·모순·ast 트리 비교)이 되도록 파이프라인을 개조한다.

정확도 100%가 목표가 아니다. **모델이 틀리면 틀린 대로 내고, 틀렸다는 사실이 사람 없이
판별되는 것**이 목표다(사용자 원문 요지). 실패 단위는 함수 전체가 아니라 행이다.

### 배경 — 실측된 사고 4종 (근본 원인: 규칙의 의미가 한국어 문장 안에 있어 기계 비교 불가)

1. 규칙 48개 상한에서 잘려 default·종료 계약 13+개 소실(zapamcom10020, 출력 8,038/10,000
   토큰 — 여유가 있는데 상한을 보고 멈춤).
2. 원문 메시지의 짝 없는 괄호를 인용했다가 검증기가 오판, 함수 전체 규칙 폐기
   (재채점 85,247건 중 오탐 64).
3. `` `mpfmdbio(` `` 절단으로 함수 10개 규칙 0(b450).
4. 자연어라 짝바뀜·값누락·default 부재를 기계가 못 잡음.

## 2. 범위 / 비범위

### 포함
- analyzer 저장소의 rules/examples 출력 계약 교체(#3 분할만; #1 summary·#2 tables 유지)
- 분기표(정답지) 강화, 값·객체 추적기, 채점기+배칭, Neo4j 저장 스키마,
  독립트랙(stereotype·glossary)

### 비범위
- Architect/frontend 수정(사용자: "일괄 수정할 거니 호환 고려 불필요" — Neo4j 스키마는 통째 교체)
- **전체 재분석 실행 — 사용자 새 승인 전 금지**(분석 실행은 그래프 DETACH DELETE + logs 삭제의 파괴 작업)
- GPU 서버 설정·플래그·백엔드 변경(권한 없음 — 보류 3건은 §11-R′에 기록만)
- 별도 LLM repair/judge, 부분 JSON 채택, 무작정 재시도, 특정 함수·코드값 하드코딩

## 3. 전체 배선 (목표 상태)

```text
소스 원문
  → [1단] ANTLR 파서: 제어문 트리+줄번호 (ontology_roles.yaml roles.control_structure가 대상 선언)
  → [2단] branch_inventory: 행별 조건원문(괄호짝 완독)·코드값·직접호출·역할(decision/complement/controller)·중첩
     = 정답지(분기표). 1단계에서 종료문(return/PFM_CATCH) 행 추가 + else-if 부정 보존 + JSON 실체화
  → [값추적] 위치별 마지막 대입·callee 반환집합(bottom-up)·객체 필드분해·미상 관측 (2단계, 신규)
  → 프롬프트: SOURCE 통째(자르지 않음) + 분기표 + (배칭 시) 할당 행 목록
  → LLM 출력: JSON 봉투 {rules:[{anchor_line, cond:"<파이썬 표현식>", then:["<파이썬 호출문>"...],
     nl, examples...}]} — leaf만 파이썬, 구조는 스키마(문법 강제 디코딩)
  → 채점: 행 매칭(echo) → 커버리지 뺄셈 → ast.parse·정규화(교환법칙 정렬·상수접기·not(a==b)→a!=b) →
     분기표와 트리/집합 비교 → 모순·창작·어휘 검사 → 행 단위 거절·상한 있는 재요청
  → enrichment(코드): rule_id="R-"+anchor, example_id, when=함수심볼, affected_tables(스캐너 결합)
  → Neo4j 저장(새 스키마) → (후일) Architect 소비
```

## 4. 출력 계약 (3단계 대상)

봉투(스키마 강제) — 실측 확정본은 harness.py `SYSTEM_PY`:

```json
{"rules":[{"anchor_line": "<분기표 행 번호 그대로 (int)>",
  "cond": "<파이썬 불리언 표현식> | null",
  "then": ["<파이썬 호출문>", "..."],
  "nl": "<사람용 한국어 한 문장 — 표시 전용, 채점 제외>"}],
 "questions": []}
```

leaf 규약:
- cond·then은 파이썬 문법. 소스의 필드·경로·식은 `F("원문 그대로")`로 감싼다
  (C 표기 `itf->in.x`를 verbatim 소속검사 가능하게 — 표기 변환 금지, 원문보존 원칙).
- then 효과 어휘(닫힘): `call("함수명")` / `ret("반환값")` / `set(F("대상"), "리터럴")` /
  `set(F("대상"), F("복사원본"))` / `raise_error("메시지ID")` / `select("테이블명")`.
  break·continue는 효과가 아니므로 금지.
- else/default 행의 cond는 `else_of("IF@<줄>")` / `else_of("SWITCH@<줄>")`.
  else-if 합성은 `else_of("IF@512") and (<조건>)` 허용(py1 실측 — 모델 자발 표현).
- 원문의 호출 조건은 함수명을 해석해 새 helper로 바꾸지 않고
  `F("strncmp(x,\"03\",n)") == 0`처럼 원문 호출을 보존한 채 Python 연산자만 쓴다.
  `len(...)`처럼 표준 Python 표현으로 정규화하는 예외는 해당 언어 ontology가 현재 행의
  원문 함수·인자 의미를 정확히 증명할 때만 허용한다.
- 모든 값·이름은 SOURCE에 실재하는 것만. 확실치 않으면 만들지 말고 questions에 남긴다.
- 값·식별자는 원문 것만, **문법 번역은 허용** — "코드 리터럴만 사용"을 변환 지시와 함께
  쓰면 PL/SQL 문법까지 복사하는 역효과 실측(§8 결함 원장, 벤치 §4).
- cond/then 문자열 필드에는 **leaf regex pattern**을 스키마에 명시(R′-③,
  xgrammar regex 지원범위 검증 포함).
- 프롬프트 지시 순서는 **evidence-first echo**: 행 복창 → cond/then(R′-⑤, 해시 재기준 1회).
- 현행 "제출 직전 완전성 감사" 블록은 유/무 1회 대조 실측 후 제거 확정(R′-⑦ — 결정론
  외부 검증이 있는 구조에서 자기검증 지시는 이득 미미가 실증된 문헌 사실 §10).

행 생성 규칙:
- 분기표의 [decision]·[complement] 행마다 규칙 정확히 1개. [controller] 행 제외.
- anchor_line은 분기표 행 번호 그대로 — 행 echo·배칭·채점의 단일 좌표. rule_id="R-"+anchor.
- Rule 48·Example 8 상한 제거. 유한성은 분기표 행수+토큰예산+반복가드가 보증.

배칭(대형 함수): 입력(SOURCE+분기표)은 통째 유지, user 꼬리에 할당 행 목록만 추가.
KV prefix 캐시가 입력 중복을 흡수. echo 규율은 6콜 실측에서 이탈 0·중복 0.

when 계약: **실행 가능 단위(함수/프로시저)당 1개, 코드가 채운다.** "뭐하면" 사람 문구는
logical_name+given의 결정론 합성. 시나리오별/결정단위별 when은 경계가 판단이라 채점이
붕괴하므로 기각(외부 반박 검토 완료 — 핸드오프 §10).

## 5. 정답지 계약 (분기표 — 1단계 대상)

- 정답지는 사람이 아니라 **파서가 소스에서 매 실행 자동 생성**한다(분기표). 사람 표본은
  채점기 자체 검증에만 쓴다 — no-gold-set 원칙.
- 1단계 강화 항목:
  1. 종료문 행 추가: RETURN / CATCH-RETURN(PFM_CATCH) — 파서 문장에서.
  2. else-if 부정 보존: 현행 `branch_index()`의 "조건도 상속" 문구는 반대 표기 — 교체.
  3. controller 태그 유지.
  4. case 값 정규화: C 8진(`case 02`) → 수치 병기(py1 실측 R-550: `== 2`는 수치 등가로
     옳으나 분기표가 정규화 규칙을 가져야 대조 가능).
  5. run별 JSON 실체화(현행은 프롬프트 문자열로만 존재).

## 6. 채점 계약 (4단계 대상)

파이프라인(전부 결정론, LLM 0):

1. **행 매칭(echo)**: anchor_line ∈ 분기표 행 집합. 할당 밖·중복·창작 행 검출.
2. **커버리지 뺄셈**: 할당 행 − 출력 행 = 누락(축퇴 검출 — 27행 중 1개 출력 시 빈칸 26 특정 실측).
3. **ast.parse**(mode="eval") — 문법 검증. py1 실측 실패 0.
4. **정규화**: 교환법칙 정렬·상수 접기·`not(a==b)`→`a!=b` 불리언 정규화.
5. **분기표 대조**: 조건원문+코드값과 트리/집합 비교, then ↔ 분기표 직접호출(callees)·
   효과원자(effect_atoms) 등호 대조.
6. **모순 검출**: AND 블록 안 같은 필드=다른 단일값(OR→AND 반전 3건 실측 검출 장치).
7. **창작·어휘 검사**: lit 소스 소속검사, F() 원문 실재검사, 닫힌 action 어휘.
8. **행 단위 거절·재요청**: 실패한 행만 재요청. **실패 사유·행 번호·위반 내용을 구체
   명시**(막연한 "틀렸다"는 역효과 — 문헌 실증 §10), **상한 3**. 상한 소진 시 해당 행은
   누락으로 표면화(silent fail 금지).

grade_py 제품화 시 수정할 하네스 오탐 2종(py1 실측 — 모델 출력은 깨끗했음):
- `else_of("IF@512")`의 인자를 리터럴로 소속검사 → else 참조 형식은 별도 검사.
- `F("atoi(gc_Bank_Cd)")` 같은 F() 안 함수식을 리터럴로 검사 → F() 원문은 소스 부분문자열 검사.

## 7. 결정 원장 (핸드오프 §9 전체 이관 — 정본)

| 결정 | 이유 | 폐기 대안 | 되돌릴 조건 |
|---|---|---|---|
| **leaf 표기 = 파이썬 표현식 subset, 구조 = JSON 스키마 강제** | blind 벤치 9종(21문항 중 유일 3회 바이트 동일·ast 대조 가능) + py1 주입 실측(20/20행·반전 0·시스템 789자·72tok/rule·10.6초) | ①자체 JSON DNF(any/all) — OR→AND 반전 3건 실측 ②DMN/FEEL — hit policy 창작 실측 ③Gherkin 본문 — 자연어라 대조 불가 ④KDM/SBVR/fUML — 정체 자체 창작 | 실코퍼스 확대 실측에서 파이썬안 오답률이 JSON안보다 높게 나옴 |
| C 표기는 `F("원문 그대로")` 문자열로 보존 | 파이썬 문법이 아닌 `itf->in.x`를 verbatim 소속검사 가능하게 | 표기 변환(itf.in.x) — 원문 대조 불가 | 없음(원문보존 원칙) |
| 정답지 = 파서 분기표(매 실행 자동, JSON 실체화) + 사람 표본은 채점기 검증 전용 | no-gold-set 원칙 + 전 노드 커버 | 사람 정답지 상시 운영 — 20함수에 며칠 실측 | 없음 |
| when = 실행 가능 단위당 1개(함수/프로시저), 코드가 채움 | 재현=호출, 중간상태는 via_call로 mock 가능. LLM 자유 when은 "처리 수행" 빈말 실측 | 시나리오별/결정단위별 when — 경계가 판단이라 채점 붕괴 | 없음 |
| Rule 48·Example 8 상한 제거, 유한성은 분기표 행수+토큰예산+반복가드 | 48 상한이 안전장치가 아니라 내용 절단(토큰 1,962 남기고 멈춤 실측) | 상한 유지 | spec110 결정 원장의 "새 구조가 유한성·완전성 보증" 조건 충족됨 |
| 출력 배칭(입력 통째 유지+행 할당+echo)·행 단위 거절·상한 있는 재요청 | echo 규율 6콜 완벽 실측, KV prefix 캐시로 입력 중복 흡수 | 입력 분할 — 문맥 절단 | 없음 |
| Neo4j 새 스키마로 통째 교체(호환 제약 없음) | 사용자: "Architect도 일괄 수정할 거니 고려 불필요" | statement 칸 유지+additive | 사용자 방침 변경 |
| DBMS 절은 보고서에서 제외(구현 5단계에는 포함) | 사용자 지시 | — | 사용자 지시 |
| stereotype: 결정론 배제규칙(쓰기 있으면 Query 금지 등) + 우선순위를 yaml 데이터로(Command›Adapter›Query›Validator›Translator›Helper) | 정의 중복으로 정답 미결정이 원인(실측 오답 2건: d100·get_card_ser_num) | 프롬프트 문구만 보강 | 제품이 다른 순위 채택 |
| glossary: 프로젝트 전역 AST 식별자 집합으로 금지, 단 사람용 주석·메시지에 실재하는 표기는 생존 | mpfmdbio·테이블명이 함수 로컬 금지집합을 통과한 실측(오염 3건: mpfmdbio·zpay_ap_auth_req_hst·RC_NRM) | 함수 로컬 금지 유지 | — |
| 리서치 판정: 채택 7 / 보류 3 / 기각 3 (§11-R′) | 사용자 제약 "서버 협의 X" + §10 근거 | 서버 항목 즉시 채택 | 서버 협의가 열리면 보류 3을 그 순서로 승격 |
| 값 사실 주입은 "자기 소스에 없는 사실" 3종만(§19): callee returns(기존)+callee caller-가시 객체 쓰기+SKILL api_contracts. 자기 함수 위치별 대입 요약은 비주입 | 사용자 지시(2026-08-03): "소스 원문과 중복되는 내용은 넣지 말고 소스만 봐서는 모르는 값 사실 위주로" — [SOURCE]가 이미 전량 프롬프트에 있음 | 자기 함수 위치별 값 요약 주입(소스 중복) | z10020·b100 전/후 실측에서 이득 없이 소음 관측 시 주입 제거(§19) |
| leaf 지침 강화(당시 교차 언어 임시 벤치 36/36; 폐기된 arbitrary Python 번역 설계라 스크립트는 2026-08-05 제거): 이름/문법 분리, 호출형 비교 번역, 호출 의미 의역 금지, 값 자리에 문장 금지, 변수 치환 금지, if식 금지 | 기존 한 줄 지시는 6개 언어에서 65%만 순수 표현식(역사 실측), 강화본 36/36. v19ab probe: 직전 상한 소진 5행 중 어휘 위반 부류 전멸, z10020 3/3 첫 시도 통과 | 현재 계약은 source 언어를 arbitrary Python 의미식으로 번역하지 않고 evidence ref 선택+구조 검증을 사용한다. 이 행은 채택 설계가 아니라 당시 실험 근거다. | 프롬프트 100%는 시료 기준일 뿐 폐쇄가 아니며, 현행 ref 소속·finding 계약은 최신 정정표를 따른다. |
| 선언 상수값 채점 앵커 배선 수리: `build_control_inventory` 가 referenced_symbols 의 immutable_constant declared_value 를 `declared_values` 로 전달 | build_inventory 독스트링의 설계 의도가 생산 경로 미배선(실측: LEN 심볼 선언값 3 인용을 창작 거절 — v19ab probe). 프롬프트가 2순위 증거로 인정하는 값은 채점도 인정해야 함 | 본문 리터럴만 앵커 | 없음 |

## 8. 결함 원장 (p1→py1 실측 반복 — 프롬프트 규칙의 근거)

| 회차 | 발견(실측) | 해소 |
|---|---|---|
| p1 | thinking 토큰 전소 / then에 return 누락 2건(nl엔 "반환한다" 쓰고 구조에서 빠뜨림) / 필드끼리 비교를 value에 / example 값 창작(20231001 등) / lit에 따옴표 | thinking off / "then이 유일한 채점 대상" / right_field / 창작금지 강화 / lit엔 값 글자만 |
| p2 | else-if를 규칙 2개로 이중 생성 / 반례 값 'N' 창작 / expr 조건에 examples | 행당 1규칙 / not_satisfy는 value_not / expr이면 examples [] |
| p3 | **OR→AND 반전** / break를 return으로 / 길이·인자를 code 칸에 유용 / controller 행에 규칙 | OR 배치 예시+모순 기계검출 / break 금지 / from_field·length 정식 칸 / controller 제외 |
| p4~p6 | OR 반전 재발 3건(검출기가 잡음) / length 심볼 미인용 JSON 붕괴(스키마 표기 애매 — 우리 실수) / 대형 JSON 틀 깨짐 2콜 / 축퇴 1콜(27행 중 1개) | 모순은 검출→재요청 경로 / length는 JSON 문자열 명시(해소 실측 27/27) / 틀은 운영 문법강제 몫 / 축퇴는 뺄셈+재요청 |
| **py1** | **위 부류 중 반전·틀깨짐·창작 전부 0. 신규: 8진 case 값 정규화 필요, F()·else_of 채점기 오탐** | 분기표 case 정규화(1단계), 채점기 오탐 2종 수정(4단계) |

교훈(4회 반복 실측): **스키마에 표현이 없는 곳은 모델이 즉흥으로 메운다** — 필요한 표현에
정식 칸을 주고 그 밖은 기계 거절.

## 9. 폭주·사고사 제약 — 새 스키마가 반드시 계승 (출처: 현재 코드 주석·설정·spec110)

| 사고·제약 | 내용 | 새 설계에의 함의 |
|---|---|---|
| 5차 사고(2026-05-30) | SGLang+xgrammar에서 **자유키 `dict[str,str]`** → value 시작 토큰 선택 실패 → 공백 무한 폭주 | 자유키 dict 금지 유지. 파이썬 leaf는 "단일 문자열 필드"라 이 방어와 부합 |
| 4차 사고 | 깊고 상한 없는 `list[KeyValueItem]` 반복 폭주 | 깊은 반복 구조 금지 — examples 설계 시 준수 |
| run7 사고 | 스칼라 1개 출력에 JSON 강제 → 문법상 합법인 공백만 3만 토큰 | 한 값짜리 룰은 평문 모드(현행 유지) |
| 필드 순서 방어 | 나열형·개방형 필드가 **마지막**이면 "끝" 경계가 없어 폭주(load_pg_company 30k 실측). 순서: 나열형 앞 → 닫힌 선택지 끝 | 새 rules 스키마의 필드 순서를 이 원칙으로 검토(예: nl을 rules 뒤가 아닌 규칙 내부에) |
| repetition guard (spec 080) | exact 반복 3,072자/period≤512, JSON 문자열 밖 연속 공백 32, root 닫힌 뒤 8자에서 스트림 중단. **RepetitionLoopAborted는 재시도 없이 partial 보존**(`concern_retry.py`) | 유지. 행 단위 재요청은 이 계약과 별개 축(검증 실패 행만, 상한 有) |
| 최종 run 실측 | 폭주 중단 13회(period-1 12·period-504 1) — summary·rules 양쪽. 서버 관측 `constrained_json_disable_any_whitespace=false` | 폭주는 용어 전용 문제 아님. 서버측 개선은 권한 밖 — 보류(§11-R′) |
| max_tokens | config 30,000은 비상 브레이크(길이 제어 수단 아님), rules yaml 10,000 | 파이썬안 실측 72tok/rule 기준으로 재산정 |
| 헌법 VII | **현행 1.3.1** — 단순 호출은 `with_structured_output(include_raw=True)`를 유지하되 배포 기본+실측된 출력계약 override가 채널을 소유한다. Agent는 submit-tool wrap. schema 원본 불변·cache 채널 격리·fail-closed 공통 | TA-58~62의 fresh 회귀가 1.3.0 단일 json_schema 공인을 대체. 언어·함수명 분기 금지 |
| 헌법 V | 분할 그룹 system은 byte-identical, `prompt_hashes.json`+CI(`byte_identical.yml`)가 감시 | SYSTEM_PY 도입 시 해시 갱신·공통 prefix 설계 필수 |
| transport/concern 재시도 | transport 오류만 제한 재시도(`transport_retry.py`), concern은 동일 요청 2회(`MAX_CONCERN_ATTEMPTS=2`) | "무작정 재시도 금지"의 현행 구현 — 행 재요청 상한 3도 같은 정신 |

## 10. 리서치로 확정된 외부 사실 (2026-08-03 딥리서치 — 원본은 사용자 보유)

| 사실 | 함의 |
|---|---|
| xgrammar는 JSON Schema의 `minItems`/`maxItems`를 **미지원**(경고 후 무시) | 문법으로 "최소 N행" 강제 불가 확정 — 축퇴는 `min_new_tokens`+뺄셈·재요청으로만 |
| xgrammar의 공백 상한(`max_whitespace_cnt`)은 SGLang이 **미노출**, compact JSON 플래그는 서버 플래그 | 서버측 차단은 전부 협의 필요 → 보류 |
| **Qwen3 하이브리드 + `enable_thinking=false` + 문법 백엔드 조합에서 문법 강제가 조용히 꺼지는 버그 클래스**가 SGLang/vLLM 이슈로 다건 보고 | "운영은 문법 강제라 틀 안 깨짐"은 가정일 뿐 — **실적용 프로브 필수**(3단계 선행 ⓐ) |
| `min_new_tokens`는 요청 파라미터지만 일부 SGLang 버전에서 미작동 보고 | 스모크 테스트 선행(3단계 선행 ⓑ) |
| Qwen 공식: 2025-07-21부터 하이브리드 thinking 폐기, Instruct/Thinking 분리 | 전용 Instruct 교체가 서버팀 전달 1순위 안건(우리 버그 클래스와 근거 일치) |
| 출력 전 자기검증 지시는 결정론 외부 검증이 있는 구조에서 이득 미미(실증) | 현행 "제출 직전 완전성 감사" 블록 = 제거 후보(유/무 대조 후 확정) |
| 재요청은 실패 사유·위치를 구체 명시할 때만 효과(막연한 "틀렸다"는 역효과 가능), 재시도 상한 3 권장 | 4단계 재요청 설계에 반영 |

## 11. 리서치 판정 (R′ — 2026-08-03 완료, 사용자 제약 "서버 협의 불가" 적용)

**채택 7** (무서버):
1. 파이썬 leaf 유지(문헌 보강).
2. `min_new_tokens` 하한 → 축퇴 차단 — **스모크 실측으로 기각 전환(2026-08-03: 서버가
   무시, 200 요청→2토큰)**. 축퇴 차단은 뺄셈·행 재요청 전담. 서버팀 전달 안건에 병기.
3. 문자열 leaf에 regex pattern 명시 — **xgrammar pattern 지원 실측 확인(프로브 5/5 마스킹)**.
4. **문법강제 실적용 프로브 — 완료**: 강제 작동 확인. 단 운영 전송은 function_calling이
   아니라 `response_format json_schema strict`(langchain 1.3.5 자동 선택 — 헌법 VII 전제와
   불일치 실측, governance 안건).
5. evidence-first echo 순서(행 복창→cond/then, 해시 재기준 1회).
6. 재요청은 실패 사유·위치 구체 명시 + 상한 3.
7. "제출 직전 완전성 감사" 블록 제거 후보 — 유/무 1회 대조 후 확정.

**보류 3** (서버 협의가 열리면 이 순서로 승격): compact JSON 플래그 → LLGuidance 재벤치
(격리 hang 검증 조건부) → **전용 Instruct-2507 교체(최강 근거: Qwen 공식 하이브리드 폐기 +
우리 버그 클래스 일치 — 서버팀 전달 1순위 안건)**.

**기각 3**: minItems 문법 강제(xgrammar 미지원 확정) / 자기검증 지시 / LLM repair·부분
JSON(기존 금지 재확인).

### 11.1 서버팀 전달 안건 (단일 절 — 협의 열리면 이 순서, 그 전에는 기록만)

`min_new_tokens` 기각(스모크 실측 미작동)으로 **축퇴의 유일한 방어는 뺄셈·행 재요청**이
됐다. 따라서 축퇴율 관측 지표를 상시 배선한다(운영 계약):

- **지표**: 함수별 `행 누락률 = (unresolved + missing) / 규칙 의무 행수` —
  `RowCollection`(rule_row_batching) 결과에서 fanout이 로그로 표면화하고
  `fn.extra["rule_grading"]`에 수치 보존(TS-4 직렬화에 포함).
- **승격 임계**: run 단위 축퇴율 >0.5% 또는 폭주 중단 >1회/run → 아래 안건을 서버팀에
  승격 보고한다.

| 순위 | 안건 | 근거 |
|---|---|---|
| 1 | **전용 Instruct-2507 교체** | Qwen 공식 하이브리드 thinking 폐기(2025-07-21) + "thinking off+문법 백엔드에서 문법이 조용히 꺼지는" 버그 클래스와 근거 일치 — 최강 |
| 2 | **min_new_tokens 수리** | 요청 파라미터가 서버에서 무시됨(2026-08-03 스모크: 200 요청→completion 2). 축퇴 방어의 서버측 보강 |
| 3 | **compact JSON 플래그**(`constrained_json_disable_any_whitespace`) | 공백 폭주의 서버측 차단 — 현재 클라이언트 반복가드만으로 방어 중(중단 13회/run 실측) |

## 12. Constitution Check (헌법 1.2.0 대조 — 위배 0, 주의 4)

| 원문 | 판정 | 근거·주의 |
|---|---|---|
| 헌법 I 결정론 우선 | **부합** | 정답지·채점·enrichment·배칭 전부 LLM 0 |
| 헌법 II 분기 0 | **주의** | 새 스키마 도입은 Strategy 선언·yaml 교체로만, step 코드에 전략 if 금지 |
| 헌법 III 단일 진실 | **주의** | OPS·action 목록·F() 규약의 정의처를 한 곳으로(스키마 모듈), 프롬프트는 거기서 생성 |
| 헌법 IV silent fail 금지 | **부합** | 행 거절·축퇴·빈칸은 로그+지표로 표면화(뺄셈이 그 장치) |
| 헌법 V KV캐시 | **주의** | SYSTEM_PY byte-identical + 해시 갱신. 배칭 suffix는 user 꼬리에만 |
| 헌법 VI 스키마 절제 | **부합** | leaf=단일 문자열, 자유키·깊은 반복 없음 — 4·5차 교훈 그대로 |
| 헌법 VII 구조화 출력 채널 | **부합(개정 완료)** | 사용자 결정(2026-08-03)으로 헌법 VII 개정·버전 1.3.0 — 단순 호출의 `response_format json_schema strict`를 공인 경로로 명문화(근거: 위반 유도 5/5 마스킹, hang 미재현, 프로브 상시 검증 수단). 과거 hang은 당시 스택 특정으로 추정 명기. 부속 계약: 이 경로의 검증 실패는 SDK raise — 에러 record가 `_extract_partial_content`로 입력 원문을 보존해야 함(0단계 수리) |
| 헌법 VIII 소유권 | plan에서 | 새 모듈(채점기·추적기) 배치를 plan의 소유권 절에서 확정 |
| 원칙 §8 구조화 출력 | **부합** | "폭주는 토큰 제한으로 숨기지 말고 스키마 평탄화·책임 분리" — 이번 전환의 요지 |
| no-gold-set | **부합** | 운영 정답지=파서 자동, 사람 표본은 장치 검증 전용 |
| 원칙 §3 근본해결·회귀금지 | **부합** | 프롬프트 땜질 대신 계약 교체. 회귀 기준: 기존 147 unittest + 신규 반증 테스트 |

각 단계 종료 감사에서 이 표를 갱신한다(§15 운영 규칙).

## 13. 금지 (전 단계)

- 특정 함수·코드값 하드코딩 / 별도 LLM repair·judge / 부분 JSON 채택
- 무작정 재시도(행 재요청 상한 3)
- 함수 임의 분할(의미 경계 포함 — "결정 단위 when" 반박 확정)
- dirty WIP(132) reset·checkout·clean
- GPU 직접 호출 시 `chat_template_kwargs.enable_thinking=false` 미해제(thinking이 토큰 전소)
- 자체 JSON DNF 회귀(OR 반전 실측으로 폐기된 설계)
- 서버 플래그·백엔드 변경(협의 불가 — 보류 3건은 §11에 기록만)
- 전체 Analyzer 재분석 — 로컬 회귀 완료 후에도 사용자 새 승인 없이 절대 실행 금지
- heredoc으로 파이썬 파일 패치(이스케이프 사고 3회 — Edit 도구로 정확 문자열 교체)

## 14. 반증(오류주입) 케이스 — 각 단계 검증에 필수

| 대상 | 주입 | 기대 |
|---|---|---|
| 0단계 raw 보존 | 검증 실패(ValidationError) 유발 응답 | raw 전문이 기록에 남음(response=None 아님) |
| 0단계 괄호검사 | 짝 없는 괄호를 담은 statement_nl | 통과(표시 전용 — 채점 제외). 단 nl 외 필드는 기존 검사 유지 |
| 1단계 분기표 | else-if 소스 | 부정 보존 표기(상속 아님), 종료문 행 존재, 8진 case 수치 병기 |
| 4단계 채점기 | 가짜 분기표 행 / OR→AND 반전 cond / 소스에 없는 lit / 어휘 밖 action / 축퇴(1행만) / 중복 anchor / 할당 밖 행 | 전부 검출·행 단위 거절. else_of·F()식은 오탐하지 않음 |
| 4단계 재요청 | 3회 연속 실패 행 | 상한 3에서 멈추고 누락으로 표면화(로그+지표) |
| 3단계 프로브 | 스키마 위반 토큰 유도 프롬프트 | 문법 강제 시 위반 토큰 마스킹 확인. 미마스킹이면 문법 강제 꺼짐 판정 → 후처리 ast 검증만으로 방어됨을 명시 |

## 15. 운영 규칙 (사용자 지시 2026-08-03 확정)

- **범위 확장 즉시 편입**: 작업 중 원 범위 밖 결함·위배·누락 발견 시 즉시 tasks.md에
  추가하고 이번 범위에 포함해 처리한다. "범위 밖이라 보류" 금지.
- 각 단계 종료 감사에 원칙·헌법 위배 점검(§12 갱신)을 포함한다.
- 새 사실·정정·실패·결정은 즉시 이 spec에 반영한다(구현/제안 구분 유지).

## 16. 단계 계획과 완료 증거 (tasks.md의 상위 계약)

| # | 작업 | 완료 증거 |
|---|---|---|
| 0 | **완료(2026-08-03, 판정 2차 정정)** raw 보존 수리 + 자연어 필드 괄호검사 제거. **경로 판정 최종**: 사용자 재지적 후 httpx MockTransport 실물 체인 주입으로 확정 — 현 스택 실경로(json_schema strict)에서 검증 실패·절단 JSON은 fallback이 아니라 **ainvoke가 ValidationError를 raise**한다(rerun6 사고 증거와 일치 — **핸드오프 주장이 옳았다**. 세션 중간의 "핸드오프 부정확" 판정은 `_agenerate` 우회 실험이 SDK 검증 계층을 건너뛴 오류였음). 수리 3건: ① ValidationError의 `errors()[*]['input']`에서 파싱 입력 원문을 복원해 에러 record에 보존(`_extract_partial_content` — 실경로 raw 소실 차단) ② `RepetitionLoopAborted`에 `partial_text` 탑재 ③ parsing_error 사유 표면화(`yaml_llm_runner.py` — parsed=None 보조 경로용). 괄호검사 삭제. 참고: fallback 경로는 현 스택에서 raw_msg 부재류 보조 경로로만 동작 | `test_failure_evidence_contract.py` — **실경로 raise 증명 2종**(검증 거절·절단 → ValidationError raise + `_extract_partial_content`로 원문 복원 확인, 경로가 바뀌면 테스트가 깨져 재판정 강제) + partial·괄호 테스트 — 전체 630/630 (2026-08-03) |
| 1 | **완료(2026-08-03)** 분기표 강화 — 종료문 행([exit], 언어 표 표준 키워드)·핸들러 행([handler], **SKILL 카드 fence→ScanResult→ctx 주입** — 최초 구현이 PFM_CATCH를 범용 C 언어 표에 굳혔던 원칙 §8 오염을 사용자 지적으로 즉시 정정)·else-if 부정 보존(파서의 형제/자식 두 형상 판정: 직접 짝 부정+외곽 성립/부정)·case 8진 수치 병기·`table_rows()` JSON 실체화+`branch-tables/` 기록. 실측 발견: 파서가 else를 IF의 형제로도, 자식으로도 내보냄 — 두 형상 모두 처리 필요(기존 코드는 자식 형상 전제) | zapamcom10020 실측 80→**87행**(+7: return 5·PFM_CATCH·catch-return — 사고① 소실 종료 계약 복원), 신설 단위테스트(부정 2형상·switch default·종료문 5종·8진 2종·스냅샷·마커 미주입 반증), 전체 회귀 593/593 (2026-08-03) |
| 2 | **완료(2026-08-03)** 값·객체 추적기 `value_tracking.py` — 위치별 마지막 대입·복사 관용구(언어 표 데이터, source_from 정밀화)·반환집합 bottom-up 동승(callee_search_texts `returns`)·proframe api_contracts fence·미상 카운터+경고 로그. 원칙 준수: 못 읽는 쓰기는 지어내지 않고 미상으로 센다 | 표 주도 13종 + 시료 대조 b100(6·4·미상0)·z10020(8·6·미상0, strncpy 6건 = py1 채점 사실과 일치) + 실측 오탐 2종 수리(문자열 내 `=`·snprintf size) + 전체 604/604 (b450 원본 소실 — z10020 대체 명시) |
| 3 | 출력 계약 교체 — **선행 프로브 완료(2026-08-03, 8콜 실측)**: ⓐ 문법강제 **작동 확인** — 단, 운영 경로의 실제 전송은 헌법 VII 전제(function_calling)와 달리 **`response_format: json_schema (strict)`**(langchain-openai 1.3.5 `with_structured_output`가 자동 선택, wire 캡처로 확정). 위반 유도 5/5 마스킹(enum gamma→alpha/beta, pattern abc→123 — **xgrammar pattern regex 지원 확인**, R′-③ 해소). hang 재현 없음 — 헌법 VII 개정 여부는 사용자 governance 안건으로 보고. 직접 tools+tool_choice 강제도 tool_calls 로 정상 응답. ⓑ **min_new_tokens 미작동 확정**(200 요청→completion 2) — 배선 취소, 축퇴 차단은 뺄셈·행 재요청 전담, §11 승격 임계는 "축퇴>0.5%면 서버 안건" 단독으로. → 본작업: SYSTEM_PY 프롬프트(evidence-first echo + 감사 블록 유/무 대조)·Pydantic(cond/then=str + leaf regex pattern, ast 후검증)·when/id/affected_tables 코드 채움·프롬프트 해시 갱신 | 프로브 로그(본 표) / harness py1 재현(신 계약 20/20·ast_fail 0) / `check_byte_identical` |
| 4 | **진행 중(2026-08-03)** 채점기 코어 완료: `rule_grading.py`(RowVerdict/GradeReport·확실 위반만 거절·오탐 2종 수리·retry_message 구체 사유)·ast 정규화(normalize — 반증이 이중부정 실결함 잡아 수리)·반증 17종 통과. 신 계약 실측: z10020 20행 **20/20·ast_fail 0**(운영 문법강제 경로) + 감사 블록 A/B로 제거 확정. 배칭·재요청 완료: `rule_row_batching.py`(plan_batches 20행/콜·collect_rules_by_rows — 실패 행만 구체 사유 재요청·상한 3·unresolved 표면화·통과 행 재생성 금지, 반증 4종). 남은 것: fanout 전환(yaml 교체와 원자적)·then↔효과 대조·b200급 실측 | test_rule_grading 17/17 + test_rule_row_batching 4/4, py2 증거 3종(evidence v2test/py2_*), 전체 회귀 625/625 |
| 5 | **완료(2026-08-03)** DBMS v2 전환(공유 leaf 계약 `rule_leaf_contract.md` 단일화, 배치 leaf 사전검사+노드별 채점) + **v1 잔재 전체 삭제·grep 0 게이트 통과** + 이름 정비(사용자 지적: "V2" 접미 폐기·본질 이름 계승 17파일, EXAMPLE `when_`/`then_` 밑줄 폐기 — todo 메모리 보류 트랙 해소) | SQL 시료 채점 테스트(실결함 검출·수리: 값 풀이 소수 0.8을 쪼개 창작 오판), 배치 키 계약 v2, 전체 회귀 그린 + 해시 32 OK |
| 6 | **완료(2026-08-03)** Neo4j 통째 교체 + 로컬 왕복(`spec116test` 별도 DB — 봉인 `neo4j` 체크포인트 비접촉): 실제 writer(write_example_mapping)→reader(read_rules) 전 필드 일치, 종료 후 생성 노드 정확 삭제(잔존 0). **왕복이 실결함 검출·수리**: AFFECTS Cypher가 옛 EXAMPLE 소스 라벨 MATCH → 조용한 엣지 0(v2는 RULE 소스) | roundtrip 출력 + AFFECTS 계약 테스트 갱신 |
| 7 | **완료(2026-08-03)** `stereotype_rules`(yaml 데이터: priority 동점 해소 + write_forbidden 배제 — 프롬프트 주입은 `{stereotype_priority}`, 정의처 ontology 하나)·모순 배제 강제(fanout·batch, 빈 값+표면화)·glossary **전역** 식별자 차단(`collect_global_identifier_names`)+사람용 주석·메시지 표기 생존(`global_forbidden_for`) | 표 주도 8종(mpfmdbio·테이블·RC_NRM 차단, 주석/메시지 생존, 우선순위 렌더) + 전체 회귀. d100·get_card_ser_num 교정 확인은 8단계 delta 몫(결정론은 모순 강제+데이터 주입까지) |
| 8 | **사용자 승인 후** 전체 재분석·고정20 재측정·delta 보고 | 새 run manifest+직접 판정 |

각 단계 = 구현→단위검증→반증검증→회귀(`unittest discover -s tests -t .` 관련 범위+프롬프트
해시)를 한 묶음으로 완료. 병행 가능: 7은 0 이후 언제든. 1↔2 순서 교환 가능.

## 17. 완료 기준

- **설계 완료**: 이 spec에 전 결정·금지·leaf/봉투 스키마·채점 규칙·반증 케이스가 있고
  숨은 대화 의존이 없다.
- **코드 작성 완료**: 하드코딩·LLM보정·부분JSON 없음, 소유 파일 명확(plan.md).
- **테스트 통과**: 단위+반증(오류주입)+unittest 회귀 관련 범위+프롬프트 해시.
- **실측 검증**: 신 계약으로 py1급 재현 + b200급 배칭 1회(소량 API 호출 — 대량이면 사용자 통지).
- **전체 완료**: 8단계(승인 후) delta까지. 그 전에는 "구현 완료"라고만 말한다.

## 18. 미확정 원장 (해소 이력 포함)

1. **해소(2026-08-03)** examples/given 형식 — 파이썬 등식 leaf 목록으로 확정
   (`ExampleV2Item`: kind + given[등식] + then[효과], 자유키·깊은 반복 없음).
2. **해소(2026-08-03)** ast 정규화 비교기 — `rule_grading.normalize` 구현·반증 검증
   (교환법칙 정렬·이중부정·not(a==b)→a!=b·상수측 통일. 상수 접기는 표기 통일로 충분해
   미도입 — 필요 시 4단계 잔여에서).
3. **해소(2026-08-03)** 대형 함수 배칭 — z10020 전 행 실측(b200 소실 대체): 의무 79행
   →4배치·5콜, 수확 78/79, 행 단위 재요청·창작 검출·상한 소진 표면화가 전부 계약대로
   동작. 부수 발견: array items pattern은 xgrammar 미강제 정황(클라이언트가 차단 —
   ValidationError 경로), 배칭의 ValidationError 정책 결함 1건 수리. 축퇴율 1.27%
   (§11.1 서버 안건 근거).
4. **해소(2026-08-03)** 문법강제 = 작동(response_format json_schema 경유, 위반 유도 5/5
   마스킹) / min_new_tokens = 미작동(기각). §16 3행 프로브 기록 참조.
5. **해소(2026-08-03)** "제출 직전 감사" 블록 — A/B 실측(z10020 20행): 무감사 ast_fail 0
   vs 감사 있음 ast_fail 11(then break 혼입). **제거 확정.**
6. **해소 → 재개정(2026-08-03, 사용자 질문 "when=함수 실행인데 왜 제어문이 있어야
   규칙이 있나")** — [exit]/[handler]는 분기 밖만 의무(기존 확정 유지, `in_branch`
   좌표 공유). **"직선 함수 = 규칙 0" 결정은 폐기**: when=함수 실행이므로 "도달하면
   항상 실행"도 계약이다(cond=null — v2 스키마가 원래 지원). 재개정: 분기 밖 최상위
   직접 효과가 존재하면 그 첫 활성 줄을 **[entry] 의무 행 1개**로 결정론 합성 —
   무조건 계약(cond=null 규칙 1개)이 복원되고 열거성(뺄셈)도 행 1개라 유지된다.
   분기 있는 함수의 최상위 효과(z10020 상단 strncpy류)도 이 행이 담는다.
   gate(골격) 노드는 [entry] 의무에서 제외(접힌 자식 내용 금지 원칙).
7. **해소(2026-08-03)** then ↔ 분기표 직접호출 대조 — `_compare_then_with_branch_callees`
   구현: 분기 본문 직접호출 목록 밖의 call(...)을 **findings**(거절 아님 — 소유권 확인
   표면화)로 남긴다. effect_atoms 등호 대조는 원자 표기 변형 소음이 커서 도입하지
   않음(필요성은 8단계 delta 관측 후 재판단).
8-a. **부분 해소(2026-08-03 저녁 — 사용자 지적 "직선 코드는 뺄셈의 기준점이 없다")**
   [entry] 규칙 then 의 최상위 효과 완전성 대조를 배선: `grade_rules` 가
   `value_tracking` 의 분기 밖 최상위 대입 목록과 then 을 대조해 **findings** 로
   표면화(거절 아님 — 판정 비확률화 유지), 미상 쓰기는 "완전성 대조 부분적"으로 병기.
   반증 4종. AI 로 정답지 빈칸을 채우는 방식은 8단계 재분석의 미상 규모 실측을 보고
   필요 여부를 판단한다(사용자 합의). 심층 결합의 나머지(cond 값 위치별 대조)는 아래
   8항대로 delta 후.
8. **미해소(2026-08-03 사용자 질문으로 등재)** 채점기 ↔ 값·객체 추적기 심층 결합 —
   `value_tracking` 은 "이 위치에서 이 심볼이 무엇이었나"(last_assignment_before)를
   답할 수 있게 준비됐고 반환집합은 프롬프트에 동승 중이지만, **채점기가 이를 물어
   대조하는 배선은 없다**(창작·모순 검사는 소스 문자열 풀까지). 같은 축: [entry]
   규칙 then 의 **최상위 효과 완전성**(추적기의 최상위 대입 목록과 등호 대조)도 미배선.
   판정이 확률적이 되지 않는 형태(findings 축)로 8단계 delta 관측과 함께 설계·도입한다.
   ※ §19 의 창작 검사 풀 확장(extra_values)은 이 심층 결합이 아니다 — 주입 정합을 위한
   최소 결합일 뿐, "채점기가 추적기에 위치별 값을 묻는" 배선은 여전히 delta 후 설계.

## 19. 값 사실 프롬프트 주입 (ⓐ — 2026-08-03 사용자 지시로 구현)

사용자 지시 원문 요지: "ⓐ 지금 구현. 단 전체 재분석으로 효과를 재지 말고, 구현 후
z10020·b100 소량 실측으로 주입 전/후를 비교해 소음(토큰 낭비·품질 저하)이 없는지 확인해
보고하라. 소스 원문과 중복되는 내용은 넣지 말고, 소스만 봐서는 모르는 값 사실 위주로
설계하고 그 설계 결정을 spec에 기록하라."

### 주입 사실 3종 — 전부 "자기 활성 소스에 없는" 사실만

1. **callee 반환집합 `returns`** — 기존(2단계) 유지. `callee_search_texts` JSON 동승.
2. **callee 의 caller-가시 객체 쓰기 `object_writes`** (신규) — callee `ValueTrace` 대입 중
   대상 base 심볼이 **callee 시그니처 파라미터**이고, (필드경로·인덱스 접근이거나 복사
   관용구 `via` 경유)인 것만. 대상별 값 원문 집합으로 집계(값 해석 없음 — 원문보존).
   callee 에 `unknown_writes`>0 이면 `writes_unknown` 수를 병기(목록이 불완전할 수 있다는
   정직 표기). 판별에 이름 관례(`g_` 접두 등) 사용 금지(원칙 §3 — 이름 패턴 상용 로직
   금지). 파라미터 경유가 아닌 전역 쓰기는 선언 파싱 없이 판별 불가라 **제외**(미상 절제
   원칙 — 한계 명시). 파라미터 단순 재대입(`param = x`, 필드·via 없음)은 callee 로컬로
   보고 제외.
3. **SKILL 카드 `api_contracts` fence** (신규 배선) — 문서 보증 사실만(mpfmdbio
   sql_key·success_value=RC_NRM, mpfm_dlcall callee_module). 지금까지 로더
   (`SKILL_CONTRACT_KEYS`)에만 있고 **소비자 0 이던 휴면 계약**을
   `_apply_skill_contract` → `ScanResult.api_contracts` → step_entry `ctx.extra` →
   rules 프롬프트 evidence 절 JSON 으로 배선.

### 비주입 (사용자 지시로 확정)

- 자기 함수의 위치별 대입·구조체 필드 요약 — [SOURCE] 로 활성 소스 전량이 이미
  프롬프트에 있다. 중복 주입은 토큰 낭비이자 소음(폐기 대안).

### 채점 결합 — 주입의 정합 조건

- 주입한 **값 문자열**(returns·object_writes 의 값·success_value)은 창작 검사 풀에
  합류한다(`grade_rules(extra_values=...)`) — 모델에게 증거로 준 값을 "소스에 없는
  창작"으로 오탐 거절하는 자기모순을 막는다. 프롬프트 렌더와 풀이 **같은 데이터**에서
  나온다(단일 정의 — 헌법 III).
- **F() 필드 원문 검사는 소스 전용 불변** — CALLEE_INTERFACE "callee 동작을 TARGET
  동작으로 복사 금지" 계약을 채점기가 계속 강제한다. callee 내부 필드(`itf->out.x` 등
  caller 소스에 없는 경로)로 cond/then 을 만드는 것은 여전히 거절.

### 배치와 캐시 (헌법 V)

- callee 항목 확장은 `callee_search_texts` JSON **값**만 변화 — `_common.yaml` 원문
  불변(해시 불변), system prefix byte-identical 불변.
- api_contracts 는 `code_rules_examples.yaml` evidence 절 placeholder 추가 — 해시 갱신
  1회(사유 기록). run 내 상수라 user prefix 캐시 친화.
- 비대상: DBMS 배치 경로(matched skill·callee 인터페이스 개념 없음).

### 효과 측정 계약 (사용자 지시 — 전체 재분석으로 재지 않는다)

z10020(대형)·b100(소형) 두 시료로 주입 전/후 각 1회 전 행 수집을 실행해
프롬프트/완성 토큰·행 수확률·ast_fail·창작/모순 거절·재요청 횟수·unresolved 를 대조해
보고한다. 소량(약 10~14콜) — 기존 허용 관례 내. **되돌릴 조건**: 이득 없이 소음
(수확률 하락, 거절 증가, 토큰 급증 대비 개선 0)이 관측되면 주입을 제거하고 증거를
evidence 폴더에 보존한다.

### 실측 결과 (2026-08-03, 총 18콜 — 하네스·원자료 `evidence-…/v19ab-값사실주입/`)

측정 방식: 제품 코드 그대로(`_load_rule`+`_safe_format` 프롬프트, 현행 분기표,
`collect_rules_by_rows` 배칭·채점·재요청, `response_format json_schema strict`,
thinking off). A=주입 전(callee returns 만·풀 확장 없음) / B=주입 후(§19 전체).

| 시료 | 암 | 콜 | 수확 | unresolved | 재요청 | prompt tok | completion tok |
|---|---|---:|---:|---|---:|---:|---:|
| b100 (의무 7행) | A | 3 | 5/7 | [220, 263] | 2 | 13,172 | 3,261 |
| b100 | B | **1** | **7/7** | [] | 0 | 4,429 | 1,538 |
| z10020 (의무 83행) | A | 7 | **83/83** | [] | 2 | 149,098 | 15,616 |
| z10020 | B | 7 | 81/83 | [512, 524] | 2 | 158,147 | 15,989 |

판정(1회 A/B — 표본 1이라 우열 확정 불가, delta 확정은 8단계 몫):
- **총 수확 동률 88/90 vs 88/90** — 주입으로 인한 품질 저하(소음) 관측 없음.
- **실패 사유에 주입 사실이 등장하지 않음**: B 의 z10020 잔여 실패(512·524)는 다절
  OR(strncmp "03"/"003" 류) 행에서 반례 값 창작('01', '1234…7890')·leaf 어휘 위반
  (strncmp/atoi 원호출) — p2 실측과 같은 기지 모델 오류 부류이고, A 도 같은 부류
  (창작 'Bank_002_CSB_Check(itf)' 등 24행)를 냈다가 재요청으로 회복. 채점기는 양쪽
  모두 계약대로 거절·표면화했다(주입값 오탐 거절 0 — 풀 확장 유효).
- 토큰 비용: z10020 프롬프트 +6.1%(object_writes 4.4천자). b100 은 B 가 오히려
  3콜→1콜(재요청 소멸)로 총 토큰 1/3.
- 부수 발견·즉시 수리: C 원문 인용 값(`"00"`)은 따옴표째 렌더되므로 창작 검사 풀에
  **벗긴 표기도 합류**해야 함(dry run 실측) — `_injected_value_facts` 수리+반증 테스트.
- 결론: **주입 유지**(되돌릴 조건 미충족). 8단계 delta 에서 run 단위 행 누락률·
  example 품질로 재판정.

## 20. 전체 재분석 1차 실측 — 비용 구조와 낭비 (2026-08-03, run `20260803_092019`)

사용자 지적: "원래 11~13분이던 run 이 52분". 기준선은 **캐시 없이** 잰 값이다
(`cached_tokens` 는 이 서버가 항상 0 을 주므로 클라이언트 캐시 지표는 정보가 아니다 —
실제 캐시 수는 GPU 서버 로그만 안다).

### 20.1 실측 (읽기 전용 진단: `logs/usage.jsonl`·`llm-prompts/`·`branch-tables/`·그래프)

| 항목 | 값 |
|---|---|
| 벽시계 / 콜 / 입력 / 출력 | 52분 / 1,310콜 / 8.70M tok / 1.04M tok |
| rules 가 차지한 LLM 시간 | **125,473초 = 전체의 91.6%** (725콜, 평균 173초) |
| 서버 처리량 | 집계 333 out-tok/s, 단일 스트림 실측 123 tok/s → **동시성 44배에 처리량 2.7배**(포화) |
| 벽시계 검산 | 1.04M ÷ 333 = 3,120초 ✅ 실제와 일치 — **디코드 바운드** |
| 의무 행 / 노드 | 4,109행 / 416노드 (평균 9.9 · 1~5행 노드가 208개) |
| 설계 배치 vs 실제 콜 | 481 vs 725 → **재요청 244콜** |
| 재요청 비중 | 콜 351/832 = **42.2%** · 출력 267k/957k = **27.8%** |
| 출력 구성 | 규칙코어 46.0% / nl 18.5% / **예시 35.4%** |

후속 동일 corpus/cache-off/concurrency-64 실측 행(성공 여부를 섞어 쓰지 않는다):

| run | 상태 | 벽시계 | successful usage | input/output | 판정 |
|---|---|---:|---:|---:|---|
| `spec116-cold-20260804-111000` | completed_with_findings | 31분 36.5초 | 1,025 | 7.939M/0.674M | prompt 순서만으로 input≤4M 가설 반증; rules decode 병목 |
| `spec116-cold-20260804-122400` | **정확한 PID 종료, 성공 아님** | 15분 초과 뒤 5분 이상 진전 0 | 745 | 4.420M/0.230M(중단 시점) | 주입 JSON 훼손과 무상한 stream tail 발견; 성능 채택 금지 |
| `spec116-cold-20260804-133619` | completed_with_findings | **17분 50.5초** | 1,006 | 6.035M/0.339M | NL-only로 43.6% 단축했으나 목표 미달; 36 RULE deadline이 695행 unavailable |
| `spec116-cold-20260804-140824` | completed_with_findings | **18분 6.6초** | 1,107 | 6.399M/0.378M | 13행 batch로 RULE unavailable 695→65행; 품질 개선, wall 16.1초 악화 |
| `20260804_124842_1f692aeb80b1` | completed_with_findings | **15분 14.1초** | 1,314 | 5.121M/0.493M | compact ref wire+LPT로 16분 통과; 의무 4,100 = trusted 4,008 + 격리 92, deadline 16행이 남아 정확도 미완료 |
| `20260804_131625_e409df61ffcc` | completed_with_findings | **17분 21.4초** | 1,316 | 5.147M/0.500M | 13K prompt 기준 반증; 의무 4,100 = trusted 4,005 + 격리 95, deadline 8행. RULE 출력 token↔latency 상관 0.96, 8행 평균/최대 97.27/180.63초 |
| `20260804_134508_290efc8217aa` | completed_with_findings | **18분 15.2초** | 1,403 | 5.250M/0.498M | 6행 주 batch 반증; 의무 4,100 = trusted 3,961 + 격리 139, Rule deadline 24행+summary 1건. 5행 최대 96.28초/deadline 0, 6행 최대 174.73초/deadline 4 |
| `20260804_141354_47d11b1be864` | completed_with_findings | **22분 5.4초** | 1,494 | 5.389M/0.601M | 5행+`then` union 동시 반증. Rule 988시도/927성공, 의무 4,100 = trusted 1,591 + 격리 2,509. 문자열 ref와 효과 객체를 한 배열에 섞자 direct return 누락 1,025행·`invalid_effect` 693행 등으로 정확도도 붕괴했으므로 둘 다 폐기 |
| `20260804_144504_260ab2bbc506` | completed_with_findings | **18분 37.8초** | 1,315 | 5.131M/0.500M | 분리 `extra`+8행/실제 ref48로 정확도 복구: 의무 4,100 = trusted 4,016 + 격리 84, unavailable 0. 22분 run보다 3분 27.6초 단축했으나 16분 미달. Rule raw 960,327자 중 의미 동일 pretty JSON 반복을 제외한 minified는 576,885자 |
| `20260804_151420_52e4849e7026` | completed_with_findings | **18분 10.6초** | 1,314 | 5.166M/0.422M | `cond/then` CSV wire 반증: Rule output 435,051→355,640(-18.3%)이나 trusted 4,016→3,962, finding 84→138로 정확도 악화. 16분도 넘었으므로 폐기하고 폐쇄 배열로 복구 |
| `20260804_154237_9ec4b55e17b1` | completed_with_findings | **18분 10.8초** | 1,314 | 5.137M/0.496M | sibling prefix-family 인접 priority 반증: Rule 747콜·433,722 output, trusted 4,006 + finding 94 = 4,100, unavailable 0. 기준보다 27.0초 빠르지만 trusted 10개 감소라 정확도 교환으로 폐기하고 evidence-size LPT 복구 |

**v1↔v2 동일 19함수 직접 대조**(v1 실적은 보존 사본 `rulecounts.txt`):
콜 19→90(**4.7배**), 출력 70,847→173,642 tok(**2.45배**).
증가의 정체는 **모든 분기 행의 규칙 의무화**다(z10020 규칙 48→83) — 사고①(48 상한 절단)을
없애려는 이 spec 의 목적 자체이므로 결함이 아니라 **계약의 구조적 비용**이다.

### 20.2 결함으로 판정해 수리한 낭비 3종

재요청 사유 전수 집계(2,300여 건) 기준:

| 사유 | 건수 | 판정 | 수리 |
|---|---:|---|---|
| 규칙 누락 | 878 | **일부 결함** — 거절된 행이 `missing` 에도 들어가 "틀렸다"+"누락됐다"가 함께 나가는 모순 신호 | `retry_message` 가 거절 행을 누락으로 재차 말하지 않음 |
| 창작 — `'\|'` 139·`'\x00'` 32 등 | 584 | **채점기 오탐** — 소스 `"%s\|%s"` 의 구분자 한 글자, 소스 `'\0'` ↔ 모델 `'\x00'` 표기 차 | 인용 리터럴의 **개별 문자**와 **이스케이프 해제 표기**를 창작 검사 풀에 합류("확실한 위반만 거절" 원칙의 정합) |
| then 한 원소에 효과 여럿 (`"set(...), ret(...)"`) | 449 | **계약 문구 누락** — 배열 원소 단위를 명시하지 않았다 | leaf 계약에 "원소 하나에 효과 하나, 쉼표 이어쓰기·후행 쉼표 금지" 명시 |

나머지(F() 원문 부재 195 · cond 어휘 밖 66 · 파싱 실패 ~100)는 계약대로의 정상 거절이다.

### 20.3 (해소) 원인별 수리와 실측 — §21 참조

아래 "선택지" 중 2(예시)는 **결정론 합성**으로, 나머지 낭비는 오탐 수리로 해소했다.
전/후 실측은 §21.

### 20.4 남은 판단 — 사용자 결정 사항

수리 3건으로 재요청 낭비(출력 27.8%)의 상당분이 줄지만 **11~13분 복귀는 계약을 바꾸지
않는 한 불가능**하다(잔여는 2.45배의 구조적 출력). 선택지와 실측 근거:

1. **현행 유지** — 전 행 열거(정확도)를 비용보다 우선. 예상 38~45분대.
2. **예시 축소** — 출력의 35.4%. 규칙당 1개 또는 조건부 제한 시 최대 ~1/3 절감.
   예시는 규칙 검증 시나리오이므로 채점·리뷰 가치와의 교환이다.
3. **배치 크기 상향** — 콜·입력은 줄지만 총 출력은 그대로라 효과 제한적(디코드 바운드).
   축퇴 위험은 실측 필요.
4. **서버** — 44배 동시성에 처리량 2.7배는 서버 병목이다(§11.1 안건 1·3의 실측 보강).

## 21. 비용 회복 — EXAMPLE 결정론 합성 + 창작 오탐 수리 (2026-08-03)

사용자 지시: "재분석해서 원인을 찾고 11~14분 언저리를 유지하라". §20 이 비용의 정체를
디코드(총 출력 토큰)로 확정했으므로 **출력에서 LLM 이 필요 없는 부분**을 없애고,
회복 불가능한 오탐 거절을 고쳤다.

### 21-1 EXAMPLE 은 LLM 산출이 아니다 (헌법 I)

판정 근거(전체 run 3,319건 전수 대조): **71.9% 가 cond/then 의 기계적 재진술**(새 정보 0).
"새 정보"로 분류된 나머지도 표본 확인 결과 ① ``else_of`` 여집합의 부정(형제 규칙이
이미 아는 사실) ② 소스에 없는 값을 지어낸 것(창작 — 우리가 금지한 그것)뿐이었다.
결정론이 할 수 있는 일을 LLM 에 시키는 것은 결함이라는 헌법 I 에 따라 코드가 만든다.

- 소유: `pipeline/quality_gates/example_synthesis.py` (branch_inventory·value_tracking 형제)
- 계약: cond 없으면 예시 없음 / satisfy = cond 를 참으로 만드는 한 경로(AND 전부·OR 첫 항·
  `else_of` 는 else-if 의미론) / not_satisfy = 첫 항의 부정, 효과는 비움 / 효과는 규칙의
  then 그대로(예시가 규칙에 없는 정책을 추가하지 않는다)
- **구현 중 검수가 잡은 실결함**: `else_of` 를 "참조 조건의 각 항을 부정"으로 접으면
  드모르간 위반이라 else-if 3단의 마지막 행이 **정반대 분기**(`bank == '03'`)를 낳았다.
  → satisfy/falsify 상호재귀로 정확히 분리(반증 테스트가 고정).
- LLM 스키마에서 `examples` 제거, 프롬프트 3종에서 예시 지시 삭제, 채점기의 예시 검사 은퇴.

### 21-2 창작 검사 오탐 — 회복 불가능한 거절이었다

`_string_constant_ok` 이 **순수 식별자만** 소스 대조를 해서, 소스에 실재하는 호출식
`Bank_002_CSB_Check(itf)`(ret/call 값 자리의 정당한 인용)이 창작으로 거절됐다. 사유가
틀렸으므로 재요청 3회를 태워도 회복되지 않는다 — z10020 에서 **행 18개 소실**.
수리: 인용 텍스트가 소스에 있으면(공백 정규화 부분문자열) 확실한 위반이 아니다.
함께: 리터럴 안 한 글자(`'|'`)·이스케이프 표기차(`'\0'`↔`'\x00'`)도 풀에 합류(§20.2).

### 21-3 소량 실측 (전/후, 같은 시료·같은 제품 경로)

| 시료 | 지표 | 수리 전 | 수리 후 |
|---|---|---:|---:|
| z10020 (의무 83행) | 콜 | 7 | **5** |
| | 완성 토큰 | 15,989 | **7,922 (-50%)** |
| | 수확 | 81/83 | **83/83** |
| | 재요청 | 2 | **0** |
| b100 (의무 7행) | 콜 | 3 | **1** |
| | 완성 토큰 | 1,538 | **931 (-39%)** |
| | 수확 | 5/7 | **7/7** |

정확도를 깎아 비용을 산 것이 아니다 — **수확은 올라가고 출력만 줄었다**(예시는 코드가
만들고, 오탐 거절이 사라져 재요청이 없어졌다).

## 22. 2026-08-04 독립 재감사 — 이전 결론의 정정과 현재 기준선

사용자 지시에 따라 이 절부터 핸드오프와 과거 완료 표기를 가설로만 취급한다. 상위 목표는
spec 116만의 국소 최적화가 아니라 analyzer 전체의 정확도·성능·속도·전 언어 확장성·
유지보수성이다. ontology와 그래프 구조도 생산자→저장→조회→프롬프트→검증 영향도를
끝까지 추적하며, 결정론 로직의 언어/케이스/이름 특화나 조용한 폴백으로 봉합하지 않는다.

### 22.1 지금 실행한 기준선

- HEAD `a3779491be6fc962834a95503332a39f73afa4dd`, dirty 165. reset/checkout/clean 금지.
- 전체 unittest: 668개, OK (skipped=5). 프롬프트 해시: 32 OK.
- 테스트가 `logs/branch-tables`를 416→418개로 늘리는 오염을 재현했다. 새 파일은
  `fn.json`, `code_shop.Order_run.json`이다. 아직 수리 전이다.
- 코드 수정 전 시작했던 콜드 run은 새 작업 순서에 따라 중단했다. 중단 시점에도 1,033콜,
  입력 7,767,306, 출력 620,461이었다. rules 444콜은 평균 153.9초·최장 780.4초,
  summary 405콜은 평균 51.3초·최장 269.0초였다.

### 22.2 반증된 주장

프롬프트의 바이트를 줄이지 않고 절 순서만 바꾼 것은 API가 보고하는 총 입력 토큰을
7.9M에서 4M 이하로 만들 수 없다. 실제 중단 run도 이미 7.77M이었다. 순서는 서버 KV
prefill 재사용과 벽시계에 영향을 줄 가능성만 있으며, 그 효과는 별도 계측 전까지
**미검증**이다. 따라서 기존의 `입력 ≤4M이면 KV 순서 수정 성립` 판정은 폐기한다.

### 22.3 검증 계약

CALL/R/W와 RULE↔분기표 뺄셈, EXAMPLE↔RULE Python 의미는 전 노드 결정론 정답지로
대조한다. 모든 LLM 산출 속성 중 구조화 가능한 축은 전수 기계 검증하고,
summary/search_text/stereotype/logical_name/용어/description은 원문과 의미가 같은지를
코드노드 20개와 모든 테이블에 대해 직접 판정한다. 문자열 일치가 아니라 의미 동치가
기준이며, 결과가 이상하면 모델보다 inventory·linker·grader·정답지를 먼저 검증한다.

## 23. leaf 창작 검사·효과 인자 계약 재감사 (2026-08-04)

현재 `_string_constant_ok`은 공백 제거한 원문에서 임의 부분문자열이면 통과시켰다. 최소
재현에서 소스에 `"PREMIUM_ACCOUNT"`·`Bank_002_CSB_Check`만 있어도 모델의
`"PREMIUM"`·`call("Bank")`가 모두 통과했다. 리터럴 내부 모든 한 글자를 풀에 넣은
완화도 `"03"`을 근거로 잘못된 `"3"`을 허용했다. 이는 "확실한 위반만 거절"이 아니라
검사 축 자체를 무력화한 원칙 위반이다.

수정 계약:

- 값은 정확 리터럴·선언값·검증된 외부 근거 또는 식별자 경계가 맞는 완전 토큰만 허용.
- `F`는 공백 차이만 접은 완전한 원문 조각을 원래 식별자 경계에서 대조.
- `call`은 source inventory의 정확한 callee 이름 한 개만, 테이블 효과는 결정론 R/W의
  정확 ID/마지막 한정 이름 한 개만 허용.
- `ret` 문자열은 실제 return 원자 또는 외부 근거 원자만 허용. 원문 호출·연산식은
  `ret(F("원문 식"))`로 구분하고 문자열 값으로 위장하지 않음.
- 효과별 arity·AST shape를 `rule_expression.py` 단일 정의처에서 강제. 복잡한 set 우변은
  여러 F/중첩 호출로 재조립하지 않고 `F("원문 우변식")` 하나로 보존.

2026-08-03 실 run의 parsed rules 444건을 새 `parse_effect`로 재채점한 결과, 효과 6,207개
중 계약 밖 306개(4.93%)가 확인됐다(set/복합 효과 173, call 등 기타 shape 99,
raise_error shape 20, syntax 8, ret shape 6). 표적 quality-gate 95개와 step7 83개,
prompt hash 31개가 통과했다. 이어 전체 unittest 652개도 통과했다(skipped=5). 새 콜드
run의 실 corpus 정확도·성능은 아직 미검증이다.

## 24. parser 지원과 소스 lexing 능력 분리 (2026-08-04)

fail-open R/W 링커를 제거한 뒤 전체 회귀에서 Pro*C(`.pc`)·COBOL(`.cbl`) 임베디드 SQL
4건이 `lang_of`의 `NoSupportedSourceError`로 실패했다. 이전 raw-source 폴백은 이 결함을
숨기고 주석 속 가짜 SQL도 실행 근거로 만들 수 있었다. 원인은 `languages.py` 코드 상수가
AST parser 지원 확장자와 주석/문자열 분리에 필요한 lexing 능력을 하나로 취급한 것이다.

수정 계약:

- `ontology_roles.yaml.source_extensions`가 확장자 정책의 데이터 단일 진실이다.
- `parser.framework`/`parser.dbms`는 실제 AST reader가 지원하는 확장자만 담는다.
- `lexing_only`는 parser 지원을 주장하지 않고, 이미 존재하는 CodeNode의 링킹에 쓸 어휘
  프로필만 연결한다. `.pc→c`, `.cbl/.cob/.cpy→cobol`이 여기에 속한다.
- 확장자 중복·빈 목록·연결된 `source_lexicon` 누락은 import 시 명시 실패한다.
- COBOL의 `*>` 인라인 주석과 고정형 7열 `*`/`/` 주석은 데이터 규칙으로 가려, 주석 속
  `EXEC SQL`이 R/W 관계로 생성되지 않는다.
- `.pc`는 C lexing을 쓰지만 `is_supported('.pc') == false`다. C parser가 Pro*C를 실제로
  지원한다는 증거 없이 분석 지원 범위를 부풀리지 않는다.

검증: 임베디드 SQL/lexing/grounding/언어 일반성 표적 66개 통과. 전체 unittest
652개 통과(skipped=5), prompt hash 31개 일치, `logs/branch-tables` 416→416.

## 25. referenced_symbols 공백 재판정과 scope 보존 (2026-08-04)

옛 문서의 “481개 중 138개(29%) 공백 = 수집 누락 가능성”을 현재 AST와 prompt로 전수
반증했다. 481은 고유 함수가 아니라 rules 호출 수(재시도 포함)였고, 고유 target은 416개다.
오프라인으로 제품 `load_ast_step → REFERENCES/INIT_BY → _build_reference_context`를 다시
실행해 당시 prompt의 심볼 ID와 대조한 결과:

- 고유 함수 416, prompt target 416
- 정확히 일치 416
- 빈 context 135개는 AST REFERENCES도 빈 정상 함수
- 참조가 있는데 prompt가 빈 함수 0
- 누락 심볼 ID 0, 과잉 심볼 ID 0

실제 결함은 공백이 아니라 scope 손실이었다. 고유 참조 심볼 411개는 AST에서
`GLOBAL_VARIABLE 147 / MEMBER 104 / DEFINE 159 / CONSTANT_FIELD 1`인데 prompt는 이를
`variable 251 / constant 160`으로 축약했다. 또한 DBMS reader가
`PARAMETER/VARIABLE/PACKAGE_VARIABLE`을 코드 frozenset으로 소유해 ontology 원칙을
위반했다.

수정 계약:

- `ontology_roles.yaml.roles`가 C/Java/Python/SQL의 variable/constant parser label을 소유.
- 같은 파일의 `symbol_kinds`가 언어별 parser label을 `global/field/local/parameter/constant`
  중 하나로 전수 분류한다. roles와 누락·과잉·중복이면 명시 실패한다.
- DBMS reader도 같은 `role_of(..., "sql")`를 소비하고 코드 label 목록을 갖지 않는다.
- prompt kind는 이름·접두가 아니라 `파일 확장자→언어 + AST label`로만 결정한다.

변경 후 현재 corpus 오프라인 재구성은 `global 147 / field 104 / constant 160`, 정상 공백
135개다. C/Java/Python/SQL 및 DBMS reader 표적 29개 통과, 전체 unittest 653개 통과
(skipped=5), hash 31개 일치, branch-table 416→416. 새 콜드 run의 실제 prompt/LLM 영향은
아직 미검증이다.

## 26. 할당 좌표 기반 강제 출력과 DBMS 누락 차단 (2026-08-04)

기존 framework `rules: list`와 DBMS `nodes: list`는 “모든 할당 행/노드를 내라”는 자연어
요청일 뿐이어서 문법 디코더가 누락을 막지 못했다. DBMS 적용부는 `rules=[]`이면 채점 자체를
건너뛰어, schema를 우회한 빈 결과가 의무 분기 행 누락인데도 정상 저장될 수 있었다. 응답
캐시도 Pydantic 클래스 이름만 사용해 같은 이름의 서로 다른 동적 schema를 같은 요청으로
오인할 수 있었다.

수정 계약:

- 분기 inventory의 단일 진실 `required_rule_rows`에서 호출별 할당 행을 만든다.
- framework는 `rules.{R<line>}`, DBMS는 `nodes.{node_key}.rules.{R<line>}`를 동적으로
  만들고 모든 키를 required로 선언한다. 각 `anchor_line`은 해당 행의 const이며 extra는
  모든 층에서 금지한다. 의무 행이 없는 DBMS node도 빈 객체 `{}` 자체를 required로 둔다.
- LLM 응답은 동적 schema로 검증한 뒤 기존 canonical `RulesExamplesOutput`/
  `BatchRulesOutput`으로 명시 정규화한다. grader·serializer·writer는 canonical 모델만
  소비해 책임을 중복하지 않는다.
- DBMS grader는 빈 rules도 건너뛰지 않는다. 요청된 모든 node에서 inventory 뺄셈을 항상
  수행하고 누락·거절·테이블 불일치를 명시 실패시킨다.
- response cache key는 모델명 대신 정렬 가능한 실제 `model_json_schema()`를 포함한다.

반증 테스트에서 행 누락·과잉 키·가짜 anchor·DBMS node 누락·schema를 우회한 빈 rules가
모두 실패했다. 표적 33개와 전체 unittest **659개 OK(skipped=5)**, prompt hash 31개,
branch-table 416→416이다. schema 직렬화 크기는 1행 2,194 bytes, 3행 4,204 bytes,
24행 25,414 bytes다. 이 크기가 실제 API 입력 토큰·문법 컴파일·벽시계에 주는 영향과 실제
서버의 strict-schema 호환은 콜드 run 전까지 **미검증**이며, 무료라고 가정하지 않는다.

## 27. 오류 효과의 텍스트 추측 제거 (2026-08-04)

첫 동적-schema 콜드 run은 106콜/3분 6초에서 규칙 채점 실패로 중단됐다. schema 누락이
아니라 오류 출력 호출을 모델이 소스 포맷 문자열의 짧은 문구로 요약해
`raise_error("요약")`으로 낸 것이 정확 리터럴 검사에 걸렸다.

이는 모델만의 문제가 아니었다. 공용 leaf 프롬프트는 `raise_error("메시지ID")`를
요구하면서 메시지 문장은 복사하지 말라고 했고, 실제 소스에는 별도 ID 없이 포맷 문장만
있는 일반 경로가 존재했다. grader는 정확한 전체 source literal만 허용하므로 계약을 그대로
따라 통과할 대표 표현이 없었다.

오류 효과는 인자 없는 `raise_error()`로 고정한다. 좌표·조건·`nl`이 어느 오류 경로인지
보존하며, 메시지 ID·문장·포맷 문자열은 의미 효과 인자로 요약하거나 창작하지 않는다.
단순 진단/디버그 로그는 오류 효과로 보지 않는다. parser는 0개 인자만 받고 텍스트 인자가
하나라도 있으면 거절한다. 이 변경은 출력 토큰도 줄이고 언어/프레임워크별 메시지 체계에
결정론 코드를 특화하지 않는다.

## 28. 도메인 용어 의미 출력의 fail-closed·원자 적용 (2026-08-04)

세 번째 partial 콜드 run에서 `domain_terms_dropped`가 반복됐다. 기존 정규화기는 source
evidence 밖 항목·구조 식별자·중복 용어만 삭제하고 같은 응답의 summary/search_text는
정상 저장했다. 경고를 남겨도 LLM이 낸 의미를 코드가 몰래 바꾸고 내용 오류를 부분 성공으로
만드는 계약 위반이다.

수정 계약:

- `domain_terms`는 선택 필드다. 모델은 근거가 없으면 빈 목록을 낼 수 있다.
- 모델이 항목을 냈다면 term/meaning/evidence, source membership, 정확 evidence 구절,
  구조 식별자 금지, 중복을 전수 검증한다.
- 한 항목이라도 실패하면 `code_id + item[index] + reason`으로 semantic response 전체를
  실패시킨다. invalid 항목만 삭제하거나 남은 항목을 저장하지 않는다.
- framework와 DBMS 모두 summary/stereotype/logical_name/search_text/domain_terms를 먼저
  임시값으로 계산·검증하고, 전부 통과한 뒤 CodeNode와 AnalysisContext에 적용한다.
  검증 실패 뒤 기존 객체의 어떤 semantic 필드도 바뀌어서는 안 된다.
- transport 오류가 아니므로 내용 재요청은 하지 않는다.

유틸리티 검증뿐 아니라 framework/DBMS 실제 fan-in 경계에서 invalid term이 예외로 전파되고
기존 노드/context가 동일한 의미 상태를 유지하는 반증을 추가했다. 이 반증이 framework의
context 선행 변이를 실제로 잡아 수정했다. 전체 663 tests OK(skipped=5), prompt hash 31,
branch-table 312→312. 새 콜드 run은 아직 미검증이다.

## 29. import/상속 참조의 데이터 계약과 parser-owned CASE (2026-08-04)

module-boundary REFERENCES/INIT_BY가 Java/Python/C 언어명 분기와 raw import 정규식을 코드에
직접 소유했고, 나머지 언어는 로그만 남기고 Pass 2를 생략했다. 같은 matcher의 식별자·주석·
문자열·상속 선언도 하드코딩돼 여러 줄 블록 주석 안 식별자를 실행 참조로 볼 수 있었다.
C parser의 CASE 소유권 결함은 analyzer가 AST를 변형해 숨겼다.

수정 계약:

- `ontology_roles.yaml`의 `source_lexicon.identifier_pattern`, `reference_imports`,
  `reference_inheritance`, `control_ownership`이 언어 구문/소유권의 데이터 단일 진실이다.
- analyzer는 언어명이 아니라 namespace/source-module/local-file resolver profile을
  dispatch한다. import 단서를 emit하는 언어와 profile 집합이 다르거나 resolver가
  미등록이면 같은 파일만 남기지 않고 명시 실패한다.
- 주석·비활성·문자열 제거는 기존 `split_source`/`without_string_literals`를 재사용해
  여러 줄 상태와 언어 lexicon을 보존한다.
- C CASE 평탄화·문장 재귀속·범위 확정은 `antlr-code-parser`의 walk 종료 단계가 소유한다.
  analyzer는 `control_ownership`을 검증할 뿐 AST를 수선하지 않는다.

새 parser AST와 옛 AST 구조 대조에서 파일 23, 노드 17,046, 함수 416, 호출 12,112,
SWITCH 147, CASE 756은 보존됐고 `첫 CASE 뒤 비-CASE 형제 303 / 중첩 CASE 254`만 모두 0이
됐다. parser 전체 Maven tests와 analyzer 전체 670 tests OK(skipped=5), prompt hash 31,
branch-table 312→312가 통과했다. 새 AST 기반 콜드 결과는 아직 미검증이다.

## 30. 실행별 corpus의 source/AST 원자 결합 (2026-08-04)

새 parser 복제본으로 콜드 run을 시작했을 때 preflight의 source/DDL과 manifest는 복제본을
가리켰지만 step 2의 AST는 전역 SETTINGS의 옛 `analysis/`를 읽었다. 새 source와 옛 AST가
섞여 canonical control ownership 검사에서 6.5초 만에 실패했으며 LLM 호출은 0이었다. 이는
성능 결과가 아니라 실행 경로 소유권 결함의 최소 재현이다.

수정 계약:

- CLI가 만든 `Codebase.corpus_root`를 step 2까지 전달해 해당 실행의 `source/`와
  `analysis/`가 같은 corpus root에서만 파생되게 한다.
- 명시한 corpus 밖 source는 canonical data root나 다른 AST로 폴백하지 않고 즉시 실패한다.
- corpus가 명시되지 않은 API/Electron 경로만 기존 canonical SETTINGS 경로 계약을 유지한다.
- manifest, DDL, source, AST가 공유하는 run-scoped corpus root를 단일 진실로 사용한다.

신선 복제본 경로가 `analysis/project`와 `source` 쌍으로 해석되는 정상 반증과 교차 corpus
source가 거절되는 실패 반증을 추가했다. 표적 10 tests와 전체 analyzer **672 tests
OK(skipped=5)**, prompt hash 31, branch-table 0→0이 통과했다. 신선 AST 기반 콜드 전체
run의 정확도·성능은 아직 미검증이다.

## 31. 구조화 submit 오류의 정확한 표면화 (2026-08-04)

TA-29 수정 후 신선 AST 콜드 run은 38.5초/6콜(입력 120,756·출력 1,957·cache hit 0)에서
중단됐다. 로그에는 `submit_scan 완료`가 있었지만 runtime은 submit 미호출로 보고했다. 실제
tool 결과는 필수 `table_access_patterns` 누락 Pydantic 오류였다. ToolNode가 이를 status=error
ToolMessage로 바꾼 뒤 두 관측 경계가 각각 성공과 미호출로 오판했다.

수정 계약:

- 공용 구조화 agent 경계는 submit 미호출, 호출했지만 schema-invalid, payload는 유효하지만
  state가 사라진 내부 배선 오류를 서로 다른 예외로 표면화한다.
- validation 오류에는 고객 payload를 복사하지 않고 실패 필드 좌표만 남긴다.
- status=error ToolMessage는 tool 완료가 아니라 tool error로 기록한다.
- Pydantic `default_factory` 필드는 동적 tool schema에서도 실제 optional/default 계약을
  보존한다. genuinely required 필드만 required이며 내용 오류 재시도는 하지 않는다.

같은 run에서 fatal 오류를 받은 `analyze_data_dir.py`가 exit code 0을 반환한 원인은
`execute_run`이 아니라 CLI 프로젝트 루프의 예외 삼키기였다. 배치의 나머지 대상은 처리하되
마지막에 실패 목록을 `AnalysisBatchError`로 올리고, 잘못된 target과 빈 source도 입력 오류로
실패시킨다.

## 32. SKILL 선택과 결정론 계약 주입의 책임 분리 (2026-08-04)

Scan Agent는 매칭한 SKILL의 YAML 계약 전체를 LLM 출력으로 다시 복사했고, `_finalize`는 그
복사본을 검증한 뒤 step 경계 `_apply_skill_contract`가 원문 계약으로 다시 덮었다. 이는 약한
모델의 받아쓰기 누락으로 실행을 죽이고 출력 토큰을 낭비하며, 검증한 값과 최종 주입값이
달라질 수 있는 DRY/정확도 결함이었다.

수정 계약:

- LLM은 실제 소스를 보고 `matched_skill`과 근거만 판단한다.
- 매칭되면 코드는 SKILL 원문의 rules/patterns/DDL transform/wrapper/runtime/handler/API/
  notes를 즉시 hydrate한다. LLM은 이 결정론 데이터를 출력하지 않는다.
- hydrate된 **최종 계약**을 기존 compile→source/DDL 검증 관문에 넣은 뒤 downstream에 준다.
- 미매칭일 때만 LLM이 custom DSL/raw pattern을 출력한다. 둘 다 없으면 `no_match_reason`이
  필수이고, 모든 경로의 evidence location은 최소 3개다.
- step 사후 덮어쓰기와 받아쓰기 누락 경고를 제거하고 DSL dict parser는 compiler 모듈 한
  정의처로 통합한다.

공용 경계 38 tests, scan/skill/linker 통합 57 tests, CLI 4 tests와 전체 analyzer **679 tests
OK(skipped=5)**, prompt hash 31, branch-table 0→0이 통과했다. 실제 모델 submit과 전체 콜드
성능은 다음 run 전까지 미검증이다.

## 33. residual 제안과 결정론 검증의 단일 책임 (2026-08-04)

TA-30~TA-32 후 신선 AST 콜드 run은 scan과 CALL 분류를 통과했지만 residual 단계에서
실패했다. residual 1콜은 입력 23,824토큰, 출력 8,000토큰(max), 56.125초를 사용하고
24,433자의 자유문을 낸 채 구조화 submit을 호출하지 않았다. 이는 전체 성능 결과가 아니라
실패 run의 병목 증거다.

생산 흐름을 역추적하면 residual Agent의 `verify_table_regex`와 호출 직후의
`verify_table_patterns`가 같은 production linker로 제안을 중복 검증한다. 도구 루프는 최종
정확도를 추가로 보장하지 않으면서 자유문·반복·submit 누락 경로를 만든다. 또한 최종 결정론
검증이 실패하면 현재 구현은 경고 후 제안을 버리고 잔여를 정상 결과처럼 반환해 내용 오류를
부분 성공으로 바꾼다.

수정 계약:

- LLM은 제공된 잔여 샘플에서 패턴/DDL 변환을 **한 번만** strict schema로 제안한다.
- 전용 YAML rule을 `run_yaml_rule`로 호출해 prompt 증거, 사용량, 응답 캐시 schema 계약,
  transport-only 재시도를 공용 경계에서 재사용한다. 자유문/도구 호출/max-call 루프는 없다.
- production `verify_table_patterns`가 제안 검증의 단일 진실이다. 통과한 제안만 적용한다.
- 패턴이 없으면 공백이 아닌 `no_pattern_reason`이 필수다. 패턴과 사유의 모순도 schema에서
  거절한다.
- 모델이 패턴을 제안했는데 결정론 검증이 실패하면 조용히 미해결로 바꾸지 않고 전체 분석을
  명시 실패시킨다. 이는 내용 재시도 대상이 아니다.
- 정상 제안·명시적 무패턴·schema-invalid·결정론 거절·옛 Agent 배선 제거를 표적 반증하고,
  전체 회귀와 실제 콜드 run 전에는 성능/정확도 개선으로 완료 판정하지 않는다.

## 34. 패턴별 검증과 fail-closed 적용 (2026-08-04)

공용 `verify_table_patterns`를 production 객체로 최소 재현한 결과, 실제 매치/edge가 있는 정상
패턴과 매치 0인 죽은 패턴을 한 목록으로 주면 집계 지표가 `ok=True`를 반환했다. 결과는 매치
1건, edge 1건, token 인식/테이블 해소 100%였고 죽은 패턴의 실패는 지표에 나타나지 않았다.
따라서 “목록 검증 통과”는 “목록의 모든 패턴 검증 통과”를 뜻하지 않았다.

추가로 Scan `_apply_verdict`는 패턴 목록 검증이 실패해도 confidence를 0.2로 낮추고 사유를
붙일 뿐, 실패한 패턴 목록 자체를 최종 `ScanResult`에 유지한다. downstream 결정론 링커가 이
패턴을 실제 적용하므로 검증은 차단기가 아니라 경고에 불과했다.

수정 계약:

- production linker의 한 파일 read/한 pattern pass에서 전체 지표와 패턴별 지표를 동시에
  보존한다. 검증을 위해 소스를 패턴 수만큼 다시 읽는 우회는 만들지 않는다.
- 패턴 ID는 결과 좌표의 단일 키이므로 중복을 거절한다.
- 모든 패턴은 각각 매치 support, edge support, 부분 추출 0, token 인식률, 단어장 모순률,
  중의율 계약을 통과해야 한다. 하나라도 실패하면 전체 verdict는 실패하고 실패 ID별 집계가
  오류에 남는다.
- Scan과 residual은 제안한 패턴의 검증 실패를 confidence 저하나 미해결 반환으로 바꾸지
  않고 내용 오류로 중단한다. 실패 패턴의 downstream 적용은 0이다.
- 패턴을 제안하지 않고 비어 있지 않은 사유를 낸 경로와 embedded rule 전용 검증은 별도의
  정상 경로로 보존한다.

## 35. Agent 최초 구조 입력의 증거 보존 (2026-08-04)

최신 실패 run의 Agent prompt 파일을 원문 노출 없이 구조 계측한 결과 scan은 7턴 중 6턴,
call-review는 3턴 중 2턴만 user 내용이 있었고 residual의 유일한 첫 턴은 0자였다. 토큰 메타는
각각 존재하므로 호출은 일어났지만 최초 구조 입력만 로그에서 사라졌다.

원인은 `append_conversation_turn`이 첫 request의 index 0을 system message로 가정해
`start=1`부터 기록한 것이다. Agent의 system prompt는 `create_agent`와 invoke 상위 필드로
별도 전달되고 request state index 0은 HumanMessage다.

수정 계약:

- 첫 model request는 state message index 0부터 모두 기록한다.
- 반환 offset은 model response가 state에 추가될 위치까지 포함해 유지하고, 다음 turn은 새
  Tool/Human 메시지만 기록한다. 이전 입력이나 AI 응답을 user 내용으로 중복 기록하지 않는다.
- 빈 request는 기존 offset을 보존한다.
- prompt 기록 실패를 성공처럼 숨기지 않는 기존 계약을 유지하며, 최초 입력·후속 tool turn을
  단위 반증하고 다음 실제 run에서 Agent 첫 user 내용이 비어 있지 않은지 확인한다.

## 36. code-node 컬럼 귀속의 구조 근거 (2026-08-04)

전역 원칙 감사에서 `table_prompt_context.py`의 오늘 변경이 비-SQL 언어를 하드코딩된
언어명으로 분기하고, member owner 이름이 DDL table 이름으로 시작하면 그 member를 해당
table 컬럼으로 간주하며, 인접 중복 문자 하나를 제거한 이름도 같은 컬럼으로 인정함을
확인했다. 이 결과는 framework/DBMS 공통 prompt와 LLM 컬럼 allowlist에 들어간다.

수정 계약:

- 결정론 코드가 언어명, table 접두, 철자 유사도로 컬럼 소유권을 만들지 않는다.
- 컬럼→table 귀속은 parser/AST, linker evidence, DDL `HAS_COLUMN`처럼 생산자가 보존한
  명시적 구조 관계만 소비한다.
- 현재 구조로 code-node별 컬럼 소유권을 증명할 수 없다면 거짓 후보를 만들지 않는다.
  table-level R/W와 DDL 소유 컬럼은 보존하되, 함수별 컬럼 사용 후보는 미입증 상태로 둔다.
- 객체/런타임 값 추적이나 특정 framework 이름 규칙을 새 결정론 분기로 추가하지 않는다.
- framework/DBMS prompt, 동적 출력 schema, 컬럼 validator, step8 후보 통합과 최종 DDL
  전 컬럼 출력을 같은 계약으로 검증한다.

## 37. 비-TABLE DB 객체를 TABLE 관계로 단일화 금지 (2026-08-04)

TA-34의 패턴별 검증을 적용한 첫 콜드 run에서 `proframe_mpfmdbio_seq`가 13회 매치 중
1회만 DDL table로 해소되고 12회는 단어장 모순으로 판정됐다. 전체 run은 31.6초,
7 LLM calls, input 159,448, output 1,534, response-cache hit 0에서 exit 1로 종료됐다.
집계형 검증이 숨기던 기존 SKILL 계약 오류를 새 fail-closed 관문이 드러낸 것이다.

해당 SKILL은 중간 `_s_` key를 별도 SEQUENCE 객체라고 설명하면서도 분석 편의를 이유로
본 table의 READ edge로 저장한다. 존재하지 않는 TABLE 접근을 만들어 그래프 영역을 줄이는
것은 정확도 보존이 아니다.

수정 계약:

- 실제 TABLE을 나타내는 정상 wrapper key 규칙은 유지한다.
- SEQUENCE 등 다른 DB 객체 key는 명시적 table 매핑 근거가 없으면 TABLE edge를 만들지 않는다.
- 현재 graph ontology에 SEQUENCE가 없다면 이번 범위에서 이름으로 TABLE에 투영하지 않고,
  별도 객체 모델링은 parser/DDL/graph/reader 전 계약을 갖춘 독립 변경에서만 한다.
- SKILL, DSL compiler, linker, residual 입력, 검증기에서 정상 key·비-table key·DDL 동명이인
  경계를 반증하고 옛 “본 table READ 단일화” 현재 참조를 0으로 만든다.

## 38. source 의미 보존과 LLM 출력의 책임 분리 (2026-08-04)

step8의 오늘 변경 `_preserve_source_column_meaning`는 source description의 괄호 앞 구절을
영숫자 정규화해 LLM description의 부분문자열인지 검사하고, 포함되지 않으면 LLM의
`logical_name`과 `description`을 source 문장으로 바꾼다. 이는 결정론 코드가 의미 동치를
판정할 수 없는데도 문자열 유사도로 의미를 판단하고, 원래 모델 출력을 조용히 다른 값으로
바꾸는 계약 위반이다.

수정 계약:

- DDL/source가 권위 있게 제공한 의미는 prompt 입력과 schema/결정론 파생 단계에서 처음부터
  보존한다. 모델이 다시 추측한 뒤 사후 보정하는 우회 흐름을 만들지 않는다.
- LLM이 맡은 필드는 구조·근거 계약을 통과한 원래 출력만 저장한다. 의미 위반을 부분문자열
  검사로 고치거나 sibling/source 값으로 덮지 않는다.
- source 설명이 있는 컬럼과 없는 컬럼의 LLM 책임을 출력 schema에서 분명히 나누고,
  누락·중복·허용 밖 이름은 strict schema/validator에서 명시 실패시킨다.
- source 설명 보존, 의미 동치의 다른 표현, 대상·보호방식 변경, 빈 근거, Neo4j/state 원자
  적용을 함께 반증한다.

## 39. table insights의 고정키 schema와 fail-closed 의미 계약 (2026-08-04)

TA-36 소비자 추적에서 `TableInsightsOutput`이 자유 배열이고, 후단
`constrain_tables_to_rw`가 R/W 밖 table, 모순 role, 허용 밖 column을 로그 후 삭제한 뒤
나머지 응답을 저장함을 확인했다. 모델 출력의 일부를 조용히 제거하는 방식은 structured
output 강제도 아니고 한 번 호출·한 번 판정도 아니다.

수정 계약:

- 현재 code-node의 직접 `reads ∪ writes` table마다 고정키를 만들고 모두 required로 한다.
- 각 항목의 table ID는 입력 exact ID const이며 추가 table key는 금지한다.
- role은 결정론 read/write와 권위 operation이 허용하는 폐쇄 집합만 schema에 넣는다.
- 현재 생산자에 증명 가능한 column-level 관계가 없다면 code-node `columns`는 빈 목록 const로
  강제한다. 이름 유사도로 채우거나 모델의 허용 밖 항목을 사후 삭제하지 않는다.
- 동적 schema 결과를 canonical `TableInsightsOutput`으로 한 번 정규화하고 framework/DBMS
  양쪽이 같은 factory를 사용한다.
- table/role/column 누락·과잉·중복·모순은 내용 오류로 명시 실패하며 partial 저장은 0이다.

## 40. 권위 unittest 러너의 함수식 테스트 전수 수집 (2026-08-04)

TA-37 표적 명령은 9 tests만 실행했고 같은 명령에 지정한 `test_rule_compiler.py`의 top-level
함수 10개는 한 건도 실행하지 않았다. 저장소 전체 AST 계측 결과 top-level test 함수를 가진
18개 파일 중 7개, 총 44개가 unittest bridge/load hook 없이 누락됐다. 12개는 pytest
`monkeypatch`와 async marker 인자를 요구해 단순 함수 호출도 할 수 없다.

수정 계약:

- 프로젝트 권위 명령 `python -m unittest discover -s tests -p "test_*.py" -t .` 하나가
  저장소의 모든 활성 테스트를 실제 실행한다.
- pytest 전용 fixture/marker에 의존한 12개는 `unittest.IsolatedAsyncioTestCase`와
  `unittest.mock` 등 표준 격리 계약으로 옮긴다.
- 인자 없는 함수식 테스트도 누락되지 않으며 parametrize 4 cases는 각각 실행 근거를 남긴다.
- 수집 개수만 늘리는 공백 wrapper를 만들지 않고 각 assertion이 실제로 실행되는 red/green을
  확인한다. 완료 후 전체 기준선 수를 새로 기록한다.

## 41. 의미 update의 end-to-end 원자성 (2026-08-04)

TA-38의 동적 schema를 통과한 table/column 의미가 step8 조립, workflow state, Neo4j SET을
지나는 동안 None·누락·중복·허용 밖 ID·빈 의미·DB 미매칭을 `continue`/INFO로 버리는
부분 성공 경로가 있었다. required key를 강제해도 저장 경계가 결과를 조용히 줄이면 같은
계약이 아니다.

수정 계약:

- table/column 결과 ID는 DDL/HAS_COLUMN 입력 집합과 정확히 일치해야 하며 None·중복·누락·
  과잉은 쓰기 전에 실패한다.
- source 의미가 있는 값은 schema const로 보존하고, 근거가 없어 빈 의미를 낸 값은 key가
  존재하는 유효 응답으로 그대로 state/graph에 반영한다. 빈값을 누락으로 바꾸지 않는다.
- state node/update ID도 중복 없이 정확히 대응해야 한다.
- Neo4j match count guard와 SET은 같은 managed transaction 안에서 수행한다. guard 불일치면
  어떤 노드도 부분 갱신하지 않는다.
- 정상·None·누락·중복·unknown·빈 의미·state 불일치·DB 미매칭을 반증하고 실제 콜드
  round-trip 전에는 완료로 판정하지 않는다.

## 42. 구조화된 비-TABLE 형식과 residual 금지 영역 (2026-08-04)

TA-36~TA-41 후 신선 AST 콜드 run은 57.3초, 10 LLM calls, input 160,281,
output 3,005, response-cache hit 0에서 residual 제안 검증으로 exit 1 종료됐다. 성공한 초기
호출은 1.4~6.2초였으므로 이 조기 실패 run은 최종 벽시계나 1~2분 tail의 해소 근거가 아니다.

실제 residual 입력 표본은 정상 table-access 규칙이 매치했으나 DDL에 없는 entity를 가진
일반 미해결 호출이었다. 비-TABLE sequence 호출이 residual 입력으로 다시 들어갔다는 초기
해석은 로그 원문과 달라 폐기한다. 그러나 약한 모델은 SKILL `codebase_notes`의 명시적
비-TABLE 설명을 무시하고 sequence 형식을 TABLE pattern으로 새로 제안했다. production
전 파일 검증은 이 제안이 12개 sequence 지점을 매치하면서 edge 0, DDL 모순 100%임을 찾아
정상적으로 전체 분석을 중단했다.

근본 결함은 “이 구문은 TABLE이 아니다”라는 권위 계약이 자연어에만 있고 ScanResult,
linker, residual output 검증이 소비할 구조화된 금지 영역은 없다는 점이다.

수정 계약:

- SKILL이 아는 비-TABLE wrapper 형식을 언어·고객 이름과 무관한 폐쇄 DSL 데이터로
  표현한다. generic 코드에 `_s_`, ProFrame, sequence 이름 분기를 넣지 않는다.
- 해당 계약은 SKILL loader → ScanResult hydrate/finalize → scan 검증 → 결정론 linker →
  residual prompt/production 검증/재적용까지 한 값으로 흐른다.
- 제외 규칙 자체도 실제 source support가 있어야 하며 죽은/중복/형태 오류는 실패한다.
- TABLE 규칙과 제외 규칙이 같은 물리 접근 지점을 동시에 주장하면 조용히 한쪽을 고르지
  않고 계약 충돌로 실패한다.
- 제외 지점은 TABLE edge와 residual 후보를 만들지 않는다. residual raw 탈출구가 제외
  지점과 겹치면 DDL 우연 일치나 일부 정상 edge 유무와 상관없이 제안 전체를 거절한다.
- 정상 TABLE access, DDL에 없는 정상 access(잔여 유지), 비-TABLE access, TABLE/제외 충돌,
  residual overlap을 production linker 경로로 반증한다.

## 43. hydrated SKILL 계약의 무손실 finalize (2026-08-04)

현재 `_hydrate_skill_contract`는 `handler_markers`와 `api_contracts`를 구조화해 넣지만,
`_finalize`가 새 `ScanResult`를 만들 때 두 필드를 전달하지 않아 기본 빈 배열로 조용히
소실한다. 전자는 branch inventory의 handler 종료행, 후자는 rules prompt와 창작 근거 풀의
생산자이므로 정확도 계약 손실이다.

수정 계약:

- hydrate된 ScanResult의 모든 결정론 필드를 finalize가 명시적으로 무손실 보존한다.
- SKILL loader, hydrate, finalize, workflow dict 직렬화, step7 context, branch inventory와
  prompt/grounding 소비자를 같은 fixture로 대조한다.
- 필드를 옮겨 적는 중복 생성자가 다시 빠뜨리지 않도록 계약 복사 책임을 단일화하거나
  보존해야 할 전체 필드를 검증하는 회귀를 둔다.

## 44. framework runtime 충돌의 fail-closed 검증 (2026-08-04)

`scan_codebase_step._remove_project_functions_from_framework_drop`은
`framework_runtime_functions`와 프로젝트 정의 함수의 정확 이름이 겹치면 경고 후 해당
항목을 삭제하고 성공 결과로 진행한다. 충돌은 SKILL 데이터 또는 LLM custom 계약 오류인데,
후단 코드가 출력을 바꿔 원인을 숨기는 부분 성공이다.

수정 계약:

- 프로젝트 정의 routine과 framework/runtime drop 계약의 정확 이름 충돌은 최종 scan
  검증에서 좌표와 이름을 남기고 실패한다.
- matched SKILL과 custom scan 모두 같은 검증을 사용한다. glob은 실제 매칭 의미와 프로젝트
  routine 집합을 기준으로 명시 검증하며 임의 예외를 추가하지 않는다.
- step4는 검증된 ScanResult를 직렬화만 하고 의미를 사후 삭제·보정하지 않는다.
- 정상 runtime, exact 충돌, glob 충돌, 빈 목록을 반증하고 CALL 분류까지 계약 양끝을 확인한다.

## 45. matched SKILL 소유 필드의 폐쇄 hydration (2026-08-04)

TA-42 배선 재감사에서 `_hydrate_skill_contract`가 fence에 **존재하는 key만** 덮고,
존재하지 않는 SKILL 소유 필드는 LLM 초안 값을 그대로 남김을 확인했다. 따라서 matched
SKILL이 exclusion/runtime/handler/API 등을 정의하지 않아도 모델이 임의 값을 주입할 수 있다.
특히 비-TABLE exclusion은 source match만으로 객체 의미가 입증되지 않으므로 custom LLM이
만들면 실제 TABLE 접근을 숨길 수 있다.

수정 계약:

- matched SKILL 경로의 rules/patterns/exclusions/DDL transform/wrappers/runtime/handler/API/
  notes는 모두 SKILL 문서만 소유한다. fence에 없는 선택 필드는 명시적 빈값/기본값이며 LLM
  초안을 보존하지 않는다.
- 비-TABLE exclusion은 문서로 검토 가능한 matched SKILL 데이터에서만 허용한다. 미매칭
  custom ScanResult가 exclusion을 제출하면 schema/content 오류로 실패한다.
- key 부재, 빈 list, agent 주입값, 정상 fence 값의 반대 사례를 검증하고 hydrate 이후 각
  필드의 출처가 하나뿐인지 전수 대조한다.

TA-42~TA-45 구현 후 generic wrapper-key exclusion DSL, 별도 non-edge compiled pattern,
source support와 TABLE span 충돌 검증, residual 구조화 exclusion 입력과 전 경로 전달을
적용했다. finalize는 전체 validated ScanResult를 복사해 handler/API를 포함한 모든 필드를
보존하며, matched hydrate는 문서에 없는 key도 빈/default로 폐쇄한다. custom unmatched
exclusion은 schema에서 거절하고 runtime/project routine exact·glob 충돌은 CALL 분류 전에
실패한다. 표적 27건, agents 67 + step4 53 + step5 107과 전체 **776 tests OK(skipped=5)**,
compileall, prompt hash 31, diff-check가 통과했다. 실제 corpus의 exclusion support 수,
residual round-trip과 콜드 전체 성공은 아직 미검증이다.

## 46. known SKILL의 결정론 선택과 unknown 전용 LLM 탐색 (2026-08-04)

TA-42~TA-45 후 콜드는 약 18.1초에 scan 단계에서 exit 1 종료됐다. 응답 캐시 hit 0,
scan 2콜 input 26,014/output 60, 실제 latency 합 1.499초였다. 첫 응답은 `ls`를 정상
호출했지만 두 번째 응답은 content `"[Collapse]"`, tool calls 0으로 끝나 submit이 없었다.
transport 오류가 아니므로 같은 내용을 재요청해 숨기지 않았다.

현재 known framework에서도 Agent가 파일 탐색→skill 목록→문서 읽기→submit을 수행한다.
그러나 각 SKILL은 이미 구조화 access/exclusion/DDL 계약을 갖고 `_finalize`가 production
linker로 source/DDL을 검증한다. “어떤 SKILL이 실제로 맞는가”를 같은 계약으로 먼저 판정할
수 있는데 LLM 도구 루프를 통과시키는 것은 토큰·지연·임의 종료 경로를 추가한다.

수정 계약:

- 등록된 각 SKILL의 access/exclusion/DDL 계약을 generic loader/compiler와 production
  verifier로 실제 source/DDL에 적용한다. skill 이름·언어·wrapper를 코드 분기하지 않는다.
- pattern support, edge support, partial 0, token 인식, DDL 모순/중의, exclusion support와
  충돌 0을 기존 단일 검증 기준 그대로 사용한다. 매치 수만으로 선택하지 않는다.
- 검증을 정확히 통과한 SKILL이 하나이고 실제 서로 다른 source 좌표 근거가 3개 이상이면
  LLM 0회로 선택·hydrate·finalize한다.
- 통과 후보 0개는 unknown/custom 탐색 경로로 보낸다. 후보 2개 이상은 LLM tie-break나
  첫 항목 선택으로 숨기지 않고 후보와 근거를 남겨 실패한다.
- 결정론 선택 뒤 같은 계약을 다시 파일 스캔하지 않도록 검증 verdict를 finalize에 넘겨
  단일 pass 책임을 유지한다.
- known 선택 LLM 0회, unknown fallback, 다중 후보, 죽은/깨진 SKILL, evidence 좌표,
  handler/API/exclusion 무손실을 반증한다. 이번 조기 실패는 성능 성공 자료가 아니다.

구현 후 production verifier가 bounded distinct source 좌표를 verdict에 보존하고, 등록 SKILL
계약을 안정 순서로 검증해 유일 후보의 기존 verdict를 finalize에 재사용한다. known fixture는
실제 TABLE 3좌표와 non-TABLE 1좌표로 ProFrame을 LLM 0회 선택했고, unknown은 Agent에 한 번
위임됐으며 동일 계약 후보 2개는 명시 실패했다. agents 70, step4 53, step5 107과 전체
**779 tests OK(skipped=5)**, compileall, prompt hash 31, diff-check가 통과했다. 실제 corpus의
유일 후보·0콜과 전체 콜드는 아직 미검증이다.

## 47. matched SKILL의 residual runtime override 금지 (2026-08-04)

TA-46 후 콜드는 약 49초, 4 LLM calls, input 27,903, output 1,259, cache hit 0에서
residual 제안 검증으로 exit 1 종료됐다. deterministic scan은 ProFrame을 25개 source 좌표로
LLM 0회 선택했고 정상 pattern 291매치/edge 98/완전 not-found 193, exclusion support를
통과했다. residual LLM은 구조화된 non-TABLE exclusion과 동일 ID의 pattern을 다시 제안했고
공용 duplicate-ID 검증이 적용 전에 중단했다.

생산 흐름상 residual 입력은 기존 pattern이 **이미 매치한** 지점 중 table/caller 해소가
실패한 사례뿐이다. matched SKILL의 pattern과 DDL transform은 문서가 권위인데 residual이
새 raw pattern이나 transform으로 이를 runtime override하면, 완전하게 추출됐지만 DDL에 없는
entity를 다른 TABLE로 재해석할 수 있다. 이는 known 계약을 LLM이 하이재킹하는 구조다.

수정 계약:

- matched SKILL이면 access/exclusion/DDL transform은 hydrate된 문서 계약으로 닫힌다.
  residual LLM은 호출하지 않고 기존 pattern의 완전 not-found는 정직한 unresolved로 보존한다.
- unresolved를 삭제하거나 빈 성공으로 바꾸지 않는다. 원래 case 좌표·attempt evidence를
  그대로 downstream 결과에 남긴다.
- known SKILL 변종이 실제로 필요하면 runtime 패치가 아니라 source/DDL 증거로 SKILL 데이터와
  회귀를 갱신한 뒤 전체 scan 검증을 다시 통과시킨다.
- matched 0콜/잔여 보존과 unmatched custom의 기존 제안·검증·실패 경로를 함께 반증한다.
  이번 실패 run은 성능 성공 자료가 아니다.

구현 후 `run_residual_loop`의 최초 책임 경계가 `matched_skill`을 판정해 baseline 재스캔과
residual LLM을 모두 건너뛰고, 입력 unresolved case 목록의 순서·dict 객체·attempt evidence를
그대로 반환한다. unmatched/custom 경로의 단일 strict-schema 제안과 production 검증/fatal
계약은 유지했다. red 최소 재현은 수정 전 known 경로가 `RegexTableLinker.link`를 호출함을
잡았고, 수정 후 linker/LLM 0회·잔여 무손실을 확인했다. step5 108 tests와 전체 **780 tests
OK(skipped=5)**, compileall, prompt hash 31, diff-check가 통과했다. 실제 corpus의 0콜 로그와
193건 보존, 전체 콜드 성공은 아직 미검증이다.

## 48. known SKILL 후보 검증의 source 준비 단일화 (2026-08-04)

TA-47 후 콜드에서 Scan 결정론 선택은 LLM 0회였지만 17.8초가 걸렸다. 코드 역추적 결과
`_select_verified_skill`은 ambiguity를 검출하기 위해 등록 계약 6개를 모두 판정하며, 각
후보의 `verify_table_patterns`가 동일한 CodeNode 소유 파일 23개를 다시 읽고 language lexing,
C adjacent/nested SQL view를 다시 만든다. 후보별 pattern과 DDL transform 판정은 독립이어야
하지만, source bytes와 lexed views는 같은 run의 불변 입력이다.

수정 계약:

- 첫 통과 후보에서 멈추지 않는다. 모든 후보를 판정해 다중 후보 fatal 계약을 보존한다.
- skill 이름·언어·wrapper 이름으로 후보를 사전 제외하지 않는다.
- source read, language lookup, live-source split, adjacent/nested view 생성을 프로젝트당 한 번
  수행한 불변 prepared input으로 만들고 모든 후보 verifier가 재사용한다.
- 후보별 regex compile, exclusion, DDL transform, table resolve, rule별 지표와 verdict는
  기존 production 기준 그대로 독립 계산한다.
- source read/UTF-8/language/lexing 실패는 준비 단계에서 종전처럼 fail-closed하고 캐시로
  숨기지 않는다. restrict-to-files residual 경로의 계약도 변하지 않는다.
- 2개 이상의 후보 fixture에서 파일 read/lexing 1회와 verdict 동등성, unknown/unique/
  ambiguity를 반증하고 실제 Scan 벽시계를 전후 대조한다.

현재 run의 17.8초는 병목 기여의 실측이지만, TA-48 수정 후 수치는 아직 모른다.

## 49. domain_terms 후보 좌표의 구조화 입력·폐쇄 출력 (2026-08-04)

TA-47 후 콜드는 known SKILL residual 193건을 0.0초·LLM 0회로 보존하고 실제 전역 LLM
동시성 peak 64까지 진행했지만, code summary 한 건이 구조 식별자를 domain term으로
제출해 92.3초에 exit 1 종료됐다. 성공한 code-summary 28건을 전수 분류하면 용어를 낸
응답은 이 한 건뿐이고, 그 표제어는 현재 source의 실제 call identifier였다. 이 run은 전체
성능 자료가 아니며, TA-47의 real-corpus 0-call/잔여 보존만 확인한 증거다.

생산자·소비자 역추적 결과 현재 `SummaryOutput`/`BatchSummaryItem`은 `term`, `meaning`,
`evidence` 자유 문자열 배열과 최대 10개라는 모양만 강제한다. 실제 parser/linker 기반
금지 식별자 집합은 LLM 입력과 JSON schema 어디에도 없고, 자연어 prompt만 금지를 부탁한다.
따라서 약한 모델은 문법적으로 완전한 구조 식별자를 만들 수 있고, 호출이 끝난 뒤
`validate_domain_terms`에서야 전체 semantic response가 실패한다. 검증기를 완화하거나 해당
항목만 버리면 TA-27의 조용한 부분 성공을 되살리므로 금지한다.

별도 계약 모순도 확인했다. prompt는 연결된 외부 원문을 domain-term 근거로 명시하지만
validator는 `source_text`/`numbered_source_text`만 membership·evidence 후보로 받아, 외부
원문에만 있는 정당한 후보도 무조건 거절한다. prompt와 validator 중 한쪽만 바꾸지 않고
모델 가시 근거의 생산자부터 맞춘다.

수정 계약:

- 현재 TARGET의 source와 결정론적으로 연결된 `external_evidence`에서 exact lexical span을
  전수 inventory한다. source comment·문자열·외부 원문의 연속 token span은 60자 경계까지
  원래 구두점·공백을 보존한다. 활성 코드 identifier는 후보가 아니라 구조 제외집합과 의미
  corroboration에만 쓰며, 언어별 keyword·이름 접두·대소문자 모양으로 의미를 추측하지 않는다.
  source comment/string/identifier 분리는 `ontology_roles.yaml`의 language lexicon만 소비한다.
- inventory는 `collect_structural_identifiers | global_forbidden_for`의 현재 exact 구조
  금지집합을 같은 정의처에서 적용한다. 구조 식별자를 후보로 주지 않되 snake_case·대문자
  같은 모양만으로 정상 후보를 버리지 않는다. 동일 표제어는 첫 모델-visible 좌표 하나로
  안정 dedup하고 전체 후보를 임의 상위 N개로 절단하지 않는다.
- 후보의 exact term 자체를 JSON object key로 쓰는 폐쇄 selection map을 만든다. 원문과
  evidence는 기존 SOURCE/external block에 이미 있으므로 후보 목록이나 evidence 문장을 user
  prompt에 다시 복사하지 않는다. `propertyNames.enum`이 허용 term 전체를, `maxProperties=10`이
  선택 상한을 강제한다.
- framework summary의 `domain_terms`는 위 selection map으로 바꾼다. 후보 0개면 `{}`만
  가능하고 unknown key·자유 term·자유 evidence·중복 term은 grammar 단계에서 만들 수 없다.
  모델은 선택 term별 `meaning`만 판단하고 코드는 exact evidence를 inventory 좌표에서 파생한다.
- DBMS summary도 입력 node key별 required fixed-key envelope와 노드별 domain-term candidate
  map을 사용해 node 누락·과잉과 자유 term을 함께 문법적으로 막는다. framework/DBMS는
  candidate inventory·selection schema·canonical normalization을 공유한다.
- canonical `SummaryOutput`/`BatchSummaryOutput` 뒤의 fail-closed validator는 유지한다.
  다만 membership/evidence source에는 실제 모델에 보인 linked external raw/numbered text도
  같은 범위로 포함한다. 내용 오류 재시도, 사후 drop, 빈값 대체는 추가하지 않는다.
- 정상 source term, local/global structural identifier, 주석의 사람용 동명이인, linked
  external-only term, Unicode·공백·구두점 span, 빈 후보, 10/11 선택, unknown key,
  framework/DBMS node 누락·과잉, cache schema fingerprint를 반증한다.
- 변경 전후 prompt+schema byte/추정 token 크기와 실제 cold input/output token·latency를
  비교한다. 후보 inventory가 정확도 손실 없이 자유 출력과 장문 지시를 대체하는지 직접
  의미 검증 전에는 완료로 판정하지 않는다.

초기 optional fixed-field 설계는 실제 실패 대상에서 schema가 약 15.4KB로 커져 폐기했다.
폐쇄 `propertyNames.enum` map은 같은 상한 진단에서 241후보·7,816 bytes였고, 실제 서버
synthetic 호출에서 `maxProperties`와 함께 정상 강제됐다. 이 수치는 전체 production 금지집합을
적용하지 않은 진단값이므로 최종 corpus 비용으로 일반화하지 않는다.

## 50. 공급자 JSON Schema와 내부 Pydantic 검증 경계 분리 (2026-08-04)

TA-49 synthetic 실제 서버 호출은 결과를 정상 생성했지만 OpenAI 호환 streaming chunk를
LangChain이 `model_dump()`할 때 `PydanticSerializationUnexpectedValue` 경고를 냈다. 스택은
`langchain_openai.chat_models.base._astream`의 SDK chunk 직렬화이며, SDK chunk의
`parsed=None` 선언에 동적 Pydantic instance가 들어간 타입 불일치다. 로컬 Pydantic dump와
MockTransport에서는 재현되지 않았고 실제 서버 역실험에서 Pydantic class 경계는 경고 1건,
동일 canonical JSON Schema dict 경계는 경고 0건이었다.

수정 계약:

- 공급자 `with_structured_output` 경계에는 response model의 canonical JSON Schema dict를
  전달한다. schema 강제 내용과 cache fingerprint는 종전 Pydantic schema와 같아야 한다.
- 공급자가 반환한 parsed JSON은 raw/usage를 확보한 뒤 runner가 원래 response model의
  `model_validate`로 정확히 한 번 검증한다. 자유 dict를 내부 계약으로 흘려보내지 않는다.
- JSON parse 실패와 Pydantic validation 실패를 각각 원인·raw와 함께 prompt evidence에 남기고
  내용 재시도나 부분 성공을 만들지 않는다.
- streaming repetition guard와 stream usage 계측은 유지한다. 경고만 숨기거나 structured
  streaming을 끄는 수정은 금지한다.
- static/dynamic schema, cache hit, JSON parse 실패, semantic validation 실패, raw/usage 보존,
  actual OpenAI-compatible streaming warning 0을 반증한다. Anthropic을 포함한 builder 소비자는
  설치된 adapter 계약과 단위 테스트로 확인하며 검증하지 않은 provider 실호출은 미검증으로 남긴다.

구현 결과 공급자에는 canonical JSON Schema dict만 전달하고, raw 문자열을 `json.loads`로 먼저
엄격 파싱한 뒤 provider parsed 값과 동일성을 확인하고 원 Pydantic model로 복원한다. LangChain
parser가 잘린 JSON을 보정한 반대 사례도 runner가 거절한다. 실제 OpenAI-compatible streaming
호출은 동적 model을 복원하면서 직렬화 경고 0건·1.1초였고, 전체 792 tests와 prompt hash 31이
통과했다. Anthropic 실호출은 여전히 미검증이다.

## 51. domain_terms 활성 코드 식별자의 schema 후보 유입 차단 (2026-08-04)

TA-49 구현 재감사에서 candidate builder가 활성 코드의 모든 lexical atom을 schema 후보로
넣음을 확인했다. parser/context 금지집합에 없는 지역변수·예약어·미해소 호출명은 그대로
선택 가능하다. prompt는 변수·상수·함수 식별자를 금지하지만 schema가 허용하므로 약한 모델
설계 원칙과 모순이며, 일반 구현 token이 후보 수와 schema 크기도 키운다.

수정 계약:

- 활성 코드의 identifier는 domain term 후보가 아니다. `source_lexicon.<lang>.identifier_pattern`을
  단일 진실로 사용해 활성 코드 식별자 exact set을 만들고 후보/validator 공통 금지집합에 합친다.
- 문자열 리터럴은 identifier 수집 전에 ontology quote 규칙으로 가린다. 따라서 한 단어 문자열
  업무 표기와 다단어 상태명은 보존한다. 주석·비활성 설명·linked external의 사람용 span도
  종전대로 보존한다.
- snake_case·대문자·접두 같은 모양으로 후보를 버리지 않는다. 동일 표기가 실제 활성 identifier면
  구조 근거로 제외할 뿐이다.
- keyword·local variable·unknown call, 주석 고유명, 한 단어/다단어 문자열, Unicode, 새 언어
  ontology profile을 반증하고 변경 전후 후보 수·schema bytes를 콜드 로그로 계측한다.

## 52. domain_terms 선택 순서의 source 좌표 canonicalization (2026-08-04)

폐쇄 selection map의 JSON property 순서는 의미 계약이 아닌데 `_selected_items`가 모델 응답
순서를 canonical list와 최종 dict 순서로 보존한다. 같은 후보 집합도 key 순서만 바뀌면 저장·
후속 glossary 입력 순서가 달라지는 비결정성이다. 선택 여부와 meaning은 모델 값을 보존하되,
정규화 결과는 candidate inventory의 첫 source 좌표 순서로 조립한다. framework/DBMS와 cache
round-trip에서 역순 입력이 동일 canonical 결과가 되는지 반증한다.

## 53. domain_terms n-gram schema 문자열 폭증 제거 (2026-08-04)

TA-51 후 최신 partial summary 28건을 고객 원문 비출력 계측한 결과, external evidence가 0인데도
후보 수는 min 0/median 114/p95 587/max 678이고 response schema는 1,128/5,424/26,175/
28,703 bytes였다. 모든 연속 span을 보존한 점은 정확하지만 각 원문 term을 `propertyNames.enum`에
반복해 입력 토큰과 grammar 크기를 다시 키운다. 임의 상위 N 절단이나 단일어 전용 축소는
정확도 손실이므로 금지한다.

수정 계약:

- 주석·문자열·external의 각 연속 human evidence segment를 source 순서의 짧은 ref(`E1`...)와
  lexical token 배열로 한 번만 summary 전용 user 입력에 준다. SOURCE/external 원문은 그대로
  유지하고 evidence 문장을 다시 복사하지 않는다.
- 60자 이하의 모든 허용 연속 token span은 짧은 좌표 key `E<segment>:<start>-<end>`로
  inventory한다. schema `propertyNames.enum`에는 term 문자열 대신 이 key만 넣고
  `maxProperties=10`과 meaning-only value를 유지한다.
- 모델은 token-index 표에서 좌표 key를 선택한다. 코드는 key와 immutable inventory에서 exact
  원문 term·evidence를 파생하며 자유 term/evidence를 받지 않는다.
- 활성 구조 identifier token을 하나라도 포함한 span은 후보 key에서 제외한다. 동일 term은 첫
  모델-visible source 좌표로 casefold dedup한다. 언어/이름 휴리스틱과 후보 절단은 없다.
- framework 입력에는 framework summary만, DBMS 입력에는 node별 evidence map만 넣어 rules/
  insights prompt에 불필요한 후보가 새지 않게 한다.
- single/multiword·구두점·Unicode·숫자 포함 term, source/external 좌표, 중복, 10/11,
  unknown key, 역순 normalization, 빈 inventory, schema/cache fingerprint, 실제 서버 grammar와
  변경 전후 prompt+schema bytes/token/latency를 반증한다.

구현 후 같은 partial summary 28건의 비내용 재계측은 candidate 0/84/490/573,
schema 1,128/2,139/6,393/7,218 bytes, evidence index 2/827/3,327/4,894 bytes였고 합계는
1,130/3,132/9,308/12,112 bytes였다(min/median/p95/max). 후보 감소는 임의 절단이 아니라 활성
구조 identifier를 포함한 span의 계약상 제거다. 실제 서버 좌표 schema 호출은 내부 동적 model,
warning 0, 1.0초로 통과했다. 전체 792 tests와 prompt hash 31이 통과했지만 cold token/latency와
직접 의미 정확도는 아직 미검증이다.

## 54. domain-term ontology/lexer 실패의 조용한 blanket 금지 제거 (2026-08-04)

전역 구조 이름과 사람용 동명이인을 분리하는 `global_forbidden_for`가 `split_source`의 모든
예외를 잡아 전역 이름 전체를 금지집합으로 반환했다. 이 fallback은 ontology/언어 계약 실패를
숨기고 주석·메시지의 정상 표제어를 조용히 잃게 한다. 바로 뒤 inventory builder도 같은 lexer를
호출하므로 유효한 복구 경계가 아니다. 광범위 catch를 제거해 원인 그대로 fail-closed하고,
정상 comment/string 생존과 lexer 실패 표면화 반증을 추가했다. 전체 793 tests, compileall,
prompt hash 31, diff-check가 통과했다.

## 55. 외부 연결·scanner 구조 지식의 ontology 단일 진실 (2026-08-04)

전역 원칙 감사에서 external resolver method/argument positions, XML address roles,
manifest names와 scanner category extensions가 Python 상수에 있음을 확인했다. 표처럼 보이지만
새 언어·프레임워크/형식 지원마다 코드를 수정해야 하므로 데이터 주도 전 언어 확장 계약이
아니다. 또한 manifest read의 `errors="ignore"`는 손실을 감춘다. 이 구조 지식을
`ontology_roles.yaml`의 검증된 section으로 옮기고 모든 소비자가 같은 loader를 사용한다.
누락/unknown/중복/범위 밖 argument position/UTF-8 실패는 조용히 저하하지 않고 표면화한다.
현재 콜드가 끝난 뒤 구현·회귀하며, 이전 동작과 실제 external link 결과를 전수 대조한다.

구현은 resolver name/argument positions, XML address attribute/child/kind, scanner manifests/
categories를 `ontology_roles.yaml`로 옮기고 Python의 중복 표를 제거했다. loader는 빈 값, duplicate
position/extension, 음수·bool argument position, 경로가 섞인 manifest를 거절한다. manifest는
strict UTF-8로 읽어 손실을 숨기지 않는다. 데이터만 바꾼 consumer 반영과 실패 표면화를 포함한
표적 27 tests는 통과했으나 전체 회귀와 실제 external link 동등성은 아직 미검증이다.

## 56. 동적 response schema를 포함한 exact replay evidence (2026-08-04)

현재 replay 도구는 실제 prompt를 재사용하지만 동적 summary schema 대신 정적 `SummaryOutput`을
전송한다. 따라서 target별 좌표 enum 문법에서 난 반복/지연을 반증할 수 없다. YAML 호출마다
공급자에 실제 전달한 canonical JSON Schema와 SHA-256/byte size를 record에 보존하고 replay는
그 schema dict를 그대로 전송한다. 동적 Pydantic class를 직렬화할 필요는 없으며, 성공 replay의
내부 Pydantic 복원까지 검증하지 못하면 그 한계를 명시한다. schema 적용/미적용 같은 prompt
대조로 문법 제약의 인과를 분리한다.

## 57. prompt evidence O(n²) rewrite 제거 (2026-08-04)

`write_rule_record`는 같은 rule 파일 전체를 호출마다 다시 읽고 정렬·들여쓰기·원자 교체한다.
평균 28KB 합성 record 416건 실측은 최종 12.33MB에 17.23초와 약 208.5배 누적 rewrite였다.
이 동기 I/O는 전역 lock 안에서 event loop까지 막고 호출 latency 계측 뒤에 있어 기존 지표에도
드러나지 않는다. canonical evidence를 호출당 O(1) immutable append 형식 하나로 바꾸고, 공용
strict reader가 필요할 때만 sort한다. 모든 소비자를 동시에 전환하고 구형 병행 format/alias는
남기지 않는다. 병렬 append, crash/corrupt record, write failure, stable sort와 기존 evidence
consumer를 반증한다.

구현은 rule별 canonical `.jsonl` 한 줄 append와 공용 strict reader의 read-time sort로 전환했다.
각 record에는 실제 공급자 schema·SHA-256·canonical byte 수도 함께 저장한다. 병렬 기록, corrupt
line, 숫자 sort key, exact replay schema 무결성의 표적 18 tests는 통과했다. 합성 성능 재계측과
전체 회귀는 아직 미검증이다. 같은 416건·11.78MB 합성 record 재계측은 append 0.186초,
read+sort 0.065초로 기존 전체 rewrite 17.23초 대비 append 기준 92.6배 빨랐다.

## 58. 반복 폭주의 필드·입력·문법 특성 분리 (2026-08-04)

첫 콜드 실패의 구형 aggregate prompt 파일은 동시 전체 재작성 중 JSON이 깨졌지만, 오류 record의
연속 조각은 복원 가능했다. period-1 반복은 `domain_terms`가 아니라 첫 `search_text` 문자열에서,
소스의 따옴표 포함 리터럴을 서술하던 도중 탭 문자로 시작했다. 그러므로 domain-term 후보 수나
목록 상한은 이번 사건의 충분한 원인이 아니다. 정상 summary 두 건도 8,479 input token/2.36초와
5,029 token/27.94초여서 입력 크기가 단독으로 latency를 설명하지 못한다. 표본 2건이므로 역상관을
일반화하지도 않는다.

다음 관측은 호출별 input token/characters, schema bytes/depth/property/enum 수, source/branch/
candidate 수, 첫 token/전체 latency, partial의 반복 시작 필드·문자를 함께 저장한다. 동일 prompt를
canonical schema와 schema 없음으로 재생하고, 충분한 의미 표현 길이를 보장하는 문자열 상한이
실제 서버 xgrammar에서 문법적으로 강제되는지도 별도 대조한다. 구조가 평탄·유한하고 입력 크기나
동시성과 무관한 비결정적 반복만 고정 표본에 남으면 모델/서빙 한계로 판정한다. 이 경우 client
stream guard가 즉시 취소하고 완전한 실패 증거를 남기되, 빈값 성공이나 동일 내용 재시도는 만들지
않는다.

## 59. 활성 run 분기 정답지 적재 실패의 fail-closed 전환 (2026-08-04)

`_persist_branch_table`은 활성 run에서도 branch-table directory/file write의 모든 예외를 잡아
경고만 남긴다. 이 파일은 단순 디버그 로그가 아니라 RULE schema 좌표와 의무행 뺄셈의 정답지다.
누락된 채 graph write가 진행되면 결과를 검증할 수 없는 성공이 된다. run 없는 독립 함수 호출은
파일을 만들지 않는 계약을 유지하되, 활성 run의 적재 실패는 원 예외를 전파해 전체 분석을
fail-closed한다. 정상 파일 내용과 write failure의 fan-out 전파를 반증한다.

구현에서 활성 run의 광범위 catch를 제거했고, run 없는 독립 호출만 적재를 생략한다. 정상·
run 없음·write failure 표적 테스트가 통과했다.

## 60. tool-call raw·stream 관측성과 canonical schema 불변성 (2026-08-04)

LangChain function-calling 응답은 provider 원형
`additional_kwargs.tool_calls[*].function.arguments` 또는 정규화된
`AIMessage.tool_calls[*].args`에 인자가 있다. 진단기와 반복 guard가 content만 읽으면 정상 응답을
raw 없음으로 오판하고 tool argument 반복을 놓친다. 두 표현을 중복 없이 읽으며
`ChatGenerationChunk.message.tool_call_chunks[*].args` delta도 실시간 guard에 공급한다.

strict tool 변환이 전달받은 schema dict를 제자리 변경하는 실측도 확인했다. canonical evidence
schema는 deep copy 뒤 provider adapter에 전달하고, 사후 JSON Schema/Pydantic 검증과 fingerprint는
원본을 사용한다. 구조화 채널은 캐시 키에 포함한다. raw 양형·tool delta·mutation·cache 분리의
표적 회귀가 통과했다.

## 61. xgrammar 교착의 확정 원인과 구조화 채널 범위 (2026-08-04)

새 immutable record의 같은 target에서 두 번 재현된 period-1 반복은 `search_text` 내부 리터럴을
설명하려던 quote가 JSON 문자열 종료로 해석된 뒤 시작했다. partial을 문자 단위로 detector에 다시
주입하면 230자에서 동일 period-1이 재현된다. grammar는 쉼표를 기다리고 모델은 리터럴 공백을
계속 원해, 문자열 밖 구조 공백만 허용되는 교착이다. 입력 크기·domain-term 수는 충분 원인이
아니다. regex pattern과 prompt 지시 변경도 같은 위치에서 실패했고 더 시도하지 않는다.

동일 실제 prompt/schema의 strict function call은 원본 schema 사후검증까지 3.1초에 통과했고,
enum·pattern·literal 위반 유도 1콜도 0.7초에 세 제약을 모두 만족했다. 반대로 실제 24행 RULE
required schema는 0.5초에 서버 `Invalid grammar request`로 거절됐다. 따라서 function calling을
전역 대체로 채택하지 않는다. 배포 기본은 json_schema로 유지하고, 이 교착이 재현됐으며 실제
schema가 검증된 framework `code_summary` 출력 계약만 function_calling을 선언한다. DBMS summary,
RULE, table insight는 실증 없이 전환하지 않는다. 요청별 whitespace 금지는 공식 server-start
option뿐이고 서버 설정 변경은 금지되어 사용하지 않는다.

## 62. domain_terms strict-tool 호환 평탄화 (2026-08-04)

종전 `dict[Literal[keys], Meaning]`은 표준 JSON Schema에서는
`propertyNames.enum + additionalProperties:$ref`로 유효하지만 strict function tool 변환이
`additionalProperties:false`로 바꿔 모든 동적 key를 금지했다. 후보 좌표를 한 번만 enum으로 갖는
`[{key, meaning}]` 최대 10개 배열로 바꾼다. unknown key·11개·중복 key·extra field는 Pydantic
경계에서 거절하고, term/evidence와 canonical 순서는 계속 immutable inventory가 파생한다.

241후보 schema는 4,239 bytes이고 strict tool wire를 표준 JSON Schema validator가 통과했다.
framework/DBMS가 같은 factory와 prompt 계약을 사용한다. 이 변경은 후보를 자르거나 의미를
축약하지 않으며, 구조화 표현의 provider 호환 결함만 제거한다.

## 63. 구조화 출력 채널의 재현 증거 계약 (2026-08-04)

config 기본과 rule override가 공존하므로 canonical schema만으로는 실제 wire 채널을 재현할 수
없다. 성공·cache hit·전송 재시도·최종 실패·usage 부재의 모든 prompt record에 resolve된
`structured_output_method`를 남긴다. schema replay는 CLI override가 없으면 기록된 채널만 쓰며,
구형 record에 채널이 없을 때 현행 config로 추정하지 않고 실패한다. `--no-schema`는 채널이 없는
의도적 대조이므로 예외다. 이 계약은 같은 prompt/schema를 다른 grammar 경로로 보내고도 동일
재현이라 보고하는 것을 막는다.

## 64. 현행 계약의 상대 버전명 제거 (2026-08-04)

생산 코드·graph schema·LLM prompt·오류·테스트에서 현행 Rule·Example 계약을 `v1/v2`로
부르지 않는다. “구조화 Rule·Example”, “행 채점”, “폐기된 statement 계약”처럼 책임과 상태를
직접 표현한다. 외부 API/model/document parser의 실제 고유 버전은 이 규칙의 대상이 아니다.

## 65. function-tool provider schema의 local-ref 정규화 (2026-08-04)

Pydantic의 평탄 domain-term 배열은 표준 JSON Schema로 유효하지만 SGLang function-tool grammar가
모델 생성 전에 nested `$defs/$ref`를 거절한다. 동일 recorded prompt에서 local ref만 inline하자
정상 완료되어 원인을 분리했다. function-calling adapter는 deep-copied provider schema에서만
RFC 6901 local pointer를 해소하고 `$defs/definitions` container를 제거한다. canonical schema는
evidence·fingerprint·JSON Schema/Pydantic 사후 검증의 단일 진실로 보존한다. 누락·외부·순환 ref와
semantic sibling keyword는 조용히 변환하지 않고 실패한다.

## 66. domain_terms 후보의 정수 ID 구조화 계약 (2026-08-04)

concurrency 64 cold에서 strict function tool이 124개 string enum 밖의 좌표를 실제로 반환했고,
같은 prompt/schema의 단독 replay는 통과했다. 따라서 큰 문자열 enum은 배칭 조건에서 신뢰 가능한
강제 경계가 아니다. 후보를 자르거나 잘못된 값을 drop하지 않고, immutable inventory의 모든
후보에 source 순서의 연속 정수 ID `0..N-1`을 부여한다. 입력은 `evidence`와
`candidate_ids: [[id, 좌표], ...]`를 한 JSON으로 제공하고 출력은
`[{candidate_id, meaning}]`만 허용한다. 코드는 ID를 원래 key/term/evidence로 복원하며 기존
canonical graph 계약과 source 순서를 바꾸지 않는다.

동일 실패 record의 124후보 제한 역실험은 4.2초, input 9,139/output 464로 완료됐고 ID 복원 뒤
기존 canonical schema 검증도 통과했다. 음수·범위 밖·문자열·bool·중복 ID, 11개, 빈 inventory,
역순을 모두 fail-closed한다. 단건 성공은 운영 안정성 증거가 아니므로 전체 회귀 뒤 cache false,
concurrency 64 cold에서 다시 판정한다. 그 실행에서도 강제가 무너지면 자동 용어 추출의 통제 불가능한
모델 한계로 확정하고 프롬프트 조정을 중단하며, 별도 책임인 사람 제공 glossary 또는 명시적 종료를
채택한다.

## 67. 외부 text inventory 형식 지식의 ontology 소유권 (2026-08-04)

config/document/XML 일반 text 분류 확장자를 Python 상수와 if 분기에 두지 않는다.
`ontology_roles.yaml.external_file_categories`가 suffix와 exact-name 규칙을 소유하고 loader는 빈 규칙,
잘못된 확장자·경로형 이름, category 중복, source 확장자와의 충돌을 거절한다. 실제 PDF/Office/HWP/
image parser 보유 여부는 실행 capability이므로 document adapter registry가 계속 소유하며 binary 판정
전에 적용한다. 숨김 `.env`는 보안 inventory skip이 권위이고, 종전 name suffix 특례는 나머지
`.env` suffix 분기와 중복이라 제거한다.

## 68. 코드·모듈 의미 출력의 required·ontology enum 강제 (2026-08-04)

prompt만 다섯 필드를 요구하고 schema default로 누락을 허용하지 않는다. framework/DBMS code summary는
`search_text`, `domain_terms`, `logical_name`, `summary`, `stereotype` 전부를 required로, module summary는
세 필드 전부를 required로 만들며 extra field를 금지한다. stereotype은 호출 strategy의 ontology
목록을 JSON Schema enum으로 직접 사용한다. 누락·빈 값·unknown·다른 strategy 값은 빈 문자열로
지우지 않고 provider/Pydantic 경계에서 실패한다. 결정론 write 증거와 stereotype의 사실 모순은
다른 역할로 바꾸지 않고 구조화 경고+빈 값으로 사람 검토를 표면화하는 기존 안전 경계를 유지한다.

## 69. stream idle의 제한 transport retry와 partial 증거 (2026-08-04)

동일 요청의 단독 exact replay가 3.8초에 성공한 반면 concurrency 64에서 62 chunks 뒤 120초 idle로
실패했으므로 `StreamChunkTimeoutError`는 이 실행에서 내용 오류가 아니라 transport/server 정체다.
이 SDK 타입만 기존 transport allowlist에 포함해 최대 3회·고정 backoff로 재시도한다. schema,
Pydantic, repetition, length/content 오류는 재시도하지 않는다. callback detector를 호출 동안 보존해
SDK 예외가 partial을 싣지 않아도 실제 수신한 content/tool-argument tail을 각 실패 record에 남긴다.
server/model/concurrency/timeout 설정은 변경하지 않는다.

## 70. 자동 domain-term 선택의 모델 한계와 사람 제공 경계 (2026-08-04)

정수 후보 ID 계약의 concurrency 64 cold에서도 모델은 4개 선택 중 같은 ID를 서로 다른 meaning으로
두 번 반환했다. 범위·개수·필수 키는 문법으로 강제됐지만 배열 항목 간 ID 유일성은 provider grammar가
강제하지 못했고, Pydantic 사후 검증이 정확히 실패시켰다. 입력 9,224/output 430이라 길이·반복 폭주가
아니며 프롬프트를 더 바꿀 근거도 없다. §66의 종료선에 따라 자동 용어 선택의 추가 prompt/schema
변형을 중단한다.

임의로 선택 상한을 낮추거나 중복 항목을 drop/merge하면 의미 정확도를 희생하거나 LLM 출력을 코드가
하이재킹하므로 허용하지 않는다. 프로젝트가 명시적으로 제공한 glossary를 권위 있는 폐쇄 입력으로
받고, analyzer는 source evidence와의 연결·provenance·충돌만 결정론적으로 검증한다. glossary가 없으면
`domain_terms=[]`를 명시 계약으로 사용해 자동 용어를 만들지 않되 나머지 code summary 의미 필드는
계속 required·fail-closed로 생성한다. 사람 입력 형식과 기존 자동 step8 glossary synthesis의 책임은
섞지 않으며, 실제 입력/config/graph 소비자를 전수 추적한 뒤 일반 언어 독립 계약으로 구현한다.

## 71. 사람 제공 glossary의 폐쇄 입력 계약 (2026-08-04)

권위 입력은 코퍼스 루트의 `analysis/glossary.yaml` 하나다. `schema_version: "1.0"`과 `terms`를
필수로 하고, 각 항목은 `canonical`, `aliases`, `meaning`, `category` 네 키를 모두 가져야 한다.
추가 키, 잘못된 타입, 앞뒤 공백, 빈 별칭, Unicode casefold 기준 canonical/alias 충돌, strict UTF-8
위반은 그래프 wipe와 LLM 호출 전에 실패한다. 파일은 1MiB, 용어 1000개, 항목당 별칭 20개의 운영
상한을 갖지만 이는 모델 출력을 잘라 정확도를 숨기는 상한이 아니라 명시적 사람 입력의 유한 계약이다.
검증 오류 로그에는 용어 원문을 싣지 않는다.

사람이 승인한 glossary 파일 자체가 용어 의미의 권위 증거다. Analyzer는 코드 문자열 출현을 근거로
전역 용어를 특정 CodeNode에 귀속하거나 언어별 이름 패턴으로 배제하지 않는다. 파일이 없으면 glossary
노드는 만들지 않고, framework/DBMS code summary는 required `domain_terms=[]`와 `maxItems=0`을
provider schema에서 강제한다. 자동 후보 좌표·식별자 필터·per-node domain_terms graph 속성·용어별
LLM synthesis는 모두 제거한다. 파일이 있으면 기존 검색 소비자가 읽는 GLOSSARY payload에 사람 값을
그대로 저장하고 `source_kind=human`, 파일 SHA-256, schema version을 graph와 run manifest에 남긴다.
응답 cache는 prompt/schema fingerprint가 이미 빈 배열 계약 변경을 포함하므로 별도 은닉 flag를 두지 않는다.

## 72. 평탄한 RULE leaf 내부의 통제 불가능한 의미 오류와 중단선 (2026-08-04)

사람 glossary/자동 용어 비활성화 뒤의 다섯 번째 cache-off·concurrency 64 cold는 215.4초에
RULE leaf 내용 오류로 fail-closed했다. 외곽 응답은 할당 행 고정키, required `anchor_line`/`cond`/
`then`/`nl`, required `questions`, extra-forbid를 모두 지켰다. 그러나 소스의 `strlen(instr) != 13`을
prompt 계약의 `F("strlen(instr)") != 13`이 아니라 폐쇄 어휘 밖 `len(F("instr")) != 13`으로
번역했다. 의미 설명과 반환 효과는 맞았지만 표현 의미를 모델이 임의 해석했고 AST 채점기가 이를
정확히 거절했다.

이 run은 319콜, input 2,284,997/output 89,137/cache hit 0이었다. summary 278콜 latency는 median
27.327초/p95 45.280초/max 72.000초, rules 11콜은 median 42.609초/p95 55.641초/max 57.468초였다.
동일 record의 저부하 exact replay는 2.9초였으므로 한 요청의 1분 tail에는 고동시성 server queue/batch
정체가 크게 관여한다. 다만 run이 조기 종료됐으므로 총 입력 token과 전체 벽시계 목표의 판정값은 아니다.

허용 어휘에 `len`을 무조건 추가하거나 C 호출을 자동 치환하면 언어 의미를 코드에 특화하고 LLM 출력을
하이재킹한다. 내용 오류 재시도도 폐기한 계약을 되살린다. 문자열 enum 불이행, 정수 ID 중복, 이번 leaf
폐쇄 어휘 불이행까지 평탄한 schema 내부의 서로 다른 의미 오류가 반복됐으므로 추가 prompt/schema
문구 실험과 전체 cold 반복은 중단한다. 서버/모델 변경 권한이나 Rule 표현 계약의 근본 재설계 승인 없이
11~15분 성공 run과 후속 graph 검증을 주장할 수 없다.

## 73. cold 대기 중 원칙 감사에서 수리한 일반 결함 (2026-08-04)

- 상대 source-module import resolver의 정의되지 않은 `mod_path` 참조를 일반 입력 `module_path`로
  바로잡고 상대 import 반증을 추가했다.
- R/W `CodeLocator`의 문자열 suffix 첫 후보·겹친 범위 첫 후보 선택을 제거했다. 경로 구성요소 기준
  유일한 suffix만 해소하고 중첩 범위는 가장 좁은 소유자를 쓰며 동률은 명시 실패한다.
- 외부 REFER_TO의 Java/C/PLSQL 지역 대입 정규식, `final|const` 판정, 상수 연결식/INIT_BY 재귀 값
  폴딩을 제거했다. 직접 호출 리터럴과 유일한 REFERENCES가 지목한 완전한 단일 리터럴 Constant 선언만
  언어 독립 사실로 보존한다.
- 변경 뒤 독립 전체 suite는 807 tests OK, skipped=5다.

## 74. 2026-08-04 재개 정정 — 검증 finding은 전체 run 실패가 아니다

다섯 번째 cold에서 `strlen(instr) != 13`을 `len(F("instr")) != 13`으로 옮긴 후보가
고정 helper 목록에 없다는 이유 하나로 319콜 전체를 폐기했다. 이는 두 계약 결함을 드러냈다.

1. 모델 산출물의 품질 위반과 분석기가 실행 불가능한 운영 실패를 같은 `raise` 경로에 넣었다.
2. Python 표기법을 요구하면서 원문↔Python 의미 정규화는 제품에 배선하지 않고 고정 함수명
   whitelist만 검사했다.

### 74.1 상태 계약

- `accepted`: 결정론 검증을 통과해 신뢰 graph와 파생 EXAMPLE에 사용할 수 있다.
- `rejected`: 결정론으로 틀림을 증명했다. 후보·좌표·사유·evidence record를 보존하되 신뢰
  graph 파생에서 격리한다.
- `unverified`: 문법상 후보는 있으나 현재 결정론 정규화가 의미 동등/부등을 증명하지 못했다.
  오답이나 정답으로 가장하지 않고 같은 방식으로 표면화·격리한다.
- `unavailable`: 반복/길이/schema 파싱처럼 후보를 신뢰 가능한 구조로 얻지 못했다. 할당 좌표와
  호출 evidence를 보존하고 독립 분석은 계속한다.
- `fatal`: 전송 고갈, 예산 초과, raw/usage/evidence 부재, 필수 입력 부재, 결정론 불변식 붕괴처럼
  해당 phase의 감사 가능한 실행 자체가 불가능한 경우만 해당한다.

run은 quality finding이 있으면 성공을 가장하지 않고 집계에 명시한다. content 재시도, 빈 규칙
제조, invalid 후보에서 RULE/EXAMPLE/AFFECTS 생성, finding 누락은 모두 금지한다.

### 74.2 Python 의미 계약

`cond`가 Python 문법인 이유는 문자열 취향이 아니라 언어가 다른 원문 조건을 한 AST 정규형으로
결정론 비교하기 위해서다. 일반 Python builtin을 무조건 허용하지 않는다. 각 언어의 source
intrinsic/관용구와 canonical Python shape를 ontology 데이터가 선언하고 범용 compiler가 원문
행에서 정확한 인자·연산자·상수를 추출해 canonical AST를 만들 수 있을 때만 동등 비교한다.
코드에는 `strlen`, Java 이름, 프로젝트 함수 접두 같은 지식을 넣지 않는다. 정규화 불가능한
조건은 `unverified`이지 전체 run 실패가 아니다.

### 74.3 수용 시나리오

- C 원문 `strlen(instr) != 13`과 후보 `len(F("instr")) != 13`은 ontology가 length intrinsic을
  선언하고 인자/연산자/상수가 같을 때 accepted다.
- 같은 후보가 length 근거 없는 행, 다른 인자, 다른 상수, 반전 연산자에 붙으면 accepted가 아니다.
- decision/LOOP 행의 `cond=null`은 좌표 있는 rejected finding이며 sibling 행과 run은 계속된다.
- schema/반복/length로 한 배치를 구조화하지 못하면 그 할당 행은 unavailable이고 다른 배치·관심사·
  노드는 계속된다. 전송 고갈과 audit evidence 실패는 계속 fatal이다.
- rejected/unverified/unavailable 행에서는 파생 EXAMPLE과 신뢰 graph 관계가 생기지 않는다.

## 75. 2026-08-04 첫 완주 반증 — RULE 재생성은 정확도와 속도를 함께 해친다

### 75.1 현재 실행이 확정한 사실

canonical parser corpus, response cache false, concurrency 64, Neo4j `test`의 run
`spec116-cold-20260804-111000`은 모델 품질 오류를 finding으로 격리한 채 처음 끝까지 완주했다.
벽시계 1,896.5초, 1,025 successful calls, input 7,939,273/output 674,393, cache hit 0,
LLM slot peak 64다. run status는 `completed_with_findings`이며 rejected 1,036,
unverified 1,223, unavailable 373을 숨기지 않았다. 4,109 의무 행은 trusted RULE 1,500과
rules finding 2,609로 정확히 보존됐다.

프롬프트 바이트를 줄이지 않은 절 순서는 API input token을 4M 이하로 만들지 않았다. 이 판정은
§22.2를 재확인하며, KV cache 효과는 서버가 cached token을 보고하지 않으므로 별도 서버 계측 없이는
모른다. 현재 run의 API input만으로 KV hit/miss를 원인처럼 말하지 않는다.

### 75.2 병목과 품질 결함

rules의 successful 478콜은 output 518,179 tokens와 누적 latency 82,260.9초를 썼다. median
124.0초, p95 440.2초, max 920.6초이며 365콜이 60초를 넘었다. summary median 31.8초,
table insight median 20.4초와 달리 rules 전체 계약이 decode 병목이다. 한 4행 응답은 최상위
필드 복사 70개를 `then`에 재열거해 3,231 tokens/593.2초를 썼다.

branch inventory와 grader는 같은 source 좌표에서 조건·직접 return/assignment/call을 이미
결정론적으로 추출한다. 이를 정답지로 검사하면서 모델에게 다시 타이핑시키는 구조는 다음 두 문제를
동시에 만든다.

1. 새 의미가 없는 출력 토큰과 GPU decode를 소비한다.
2. 모델의 개행·누락·오귀속이 결정론 사실을 훼손하고 한 행 오류가 정상 sibling까지 폐기한다.

### 75.3 수정 계약

- 범용 source-condition compiler와 direct-effect materializer가 `anchor_line`, Python AST로
  비교 가능한 `cond`, `then`을 source/AST/ontology에서 만든다. 코드에는 언어명·함수명·프로젝트
  접두 지식을 두지 않는다.
- 구조를 완전히 canonicalize할 수 없는 source condition은 값을 추측하지 않고 원문 fragment를
  보존하며 정규화 수준과 이유를 기록한다. 임의 상수·런타임 값·객체 상태 추적은 하지 않는다.
- LLM은 required 행 고정키 아래 `nl`과 실제 증거 공백을 묻는 `questions`만 생성한다. NL은
  결정론 cond/effect와 direct source를 구조화 입력으로 받아 설명하며, 코드가 NL 의미를 바꾸거나
  빈값으로 성공시키지 않는다.
- EXAMPLE은 materialized cond/then에서 계속 결정론 파생한다. RULE↔분기표와 EXAMPLE↔RULE 뺄셈은
  전 행을 대상으로 유지한다.
- 한 행의 모델 NL/schema 의미 오류는 그 좌표만 finding으로 격리한다. 정상 sibling을 함께
  unavailable로 만들지 않는다.

이 변경은 모든 분기 행 1규칙 계약을 줄이는 것이 아니다. 행 수와 직접 효과를 그대로 보존하면서
결정론이 아는 사실을 LLM decode에서 제거해 정확도와 속도를 함께 개선하는 계약 재배치다.

## 76. RULE NL-only wire 계약 구현과 반증 수리 (2026-08-04)

### 76.1 구현된 책임 경계

- provider response schema는 할당된 모든 `R<행>`을 required object key로 두고 각 값에 `nl`만
  요구한다. `anchor_line/cond/then`을 모델이 출력하면 extra-forbid로 거절한다.
- `rule_materialization.py`가 같은 `BranchInventory`에서 행 좌표, 조건, 직접 assignment/call/return을
  materialize한다. entry는 첫 행 하나만 읽지 않고 분기·최종 exit를 제외한 모든 최상위 직접 효과를
  소유한다. child call은 부모/entry로 새지 않는다.
- C/Python/SQL의 ontology-declared length intrinsic과 boolean/comparison shell은 같은 Python AST
  leaf로 정규화한다. 문자열 리터럴 안 연산자는 건드리지 않는다. 완전 구조화가 불가능하거나
  연쇄/미등록 구문이면 부분 변환으로 뜻을 바꾸지 않고 정확한 원문 predicate 하나를 `F(...)`로
  보존하며 `opaque_source`를 기록한다.
- source-owned RULE leaf에는 모델 디코딩 방어용 500자 상한을 적용하지 않는다. 의미 절단 없는
  한 줄만 강제한다. 500자 상한은 결정론 EXAMPLE 표시 leaf에만 남는다.

### 76.2 토큰 절감과 의미 입력의 동시 계약

RULE prompt는 공통 전체 SOURCE를 다시 받지 않는다. 대신 모델이 사람용 NL을 정확히 설명하는 데
필요한 권위 사실만 compact 구조로 받는다: current search_text, referenced symbols와 선언값,
external evidence, 직접 R/W와 table op, callee search_text/return 집합, SKILL api_contracts,
codebase/module 문맥, 행별 source/canonical condition과 direct assignments/calls/returns. 이 문맥은
행의 결정론 effect를 대체하거나 확대할 권한이 없다.

### 76.3 현재 검증 상태

다언어/인용/미지원 fallback/500자 초과/entry-child 소유권/고정키 schema 반증을 추가했다. 전체
`840 tests OK (skipped=5)`, compileall, `check_byte_identical.py` 28, `git diff --check`를 통과했다.
이는 구현 회귀 증거일 뿐 11~15분 벽시계나 실제 NL 의미 정확도 증거가 아니다. 동일 canonical
corpus, response cache false, concurrency 64, Neo4j `test`의 다음 cold에서 call/token/latency와
RULE↔분기표·EXAMPLE 파생을 다시 판정한다. 검증 전에는 속도 목표 달성이라고 보고하지 않는다.

### 76.4 cold 직전 전수 반증이 잡은 정답지/경로 결함

이전 run의 branch-table decision 2,547행을 compiler에 전수 입력하자 9행이 빈 조건으로 실패했다.
source를 직접 대조하니 모두 `#if 0` 안의 비활성 과거 코드였고 parser 좌표만 남아 있었다. 이런
좌표를 “모든 분기 행”으로 세는 것은 정확도가 아니라 inactive 코드를 실행 규칙으로 만드는 오염이다.
inventory는 기존 ontology-driven lexer의 live range가 완전히 빈 subtree를 제외한다. 첫 재실행은
이 발견 때문에 정확한 process만 중단했으며 성능 자료로 사용하지 않는다.

같은 전수 감사에서 CASE와 nested reachability도 최초 materializer가 불완전했다. CASE는
`F("case 1")`이 아니라 controller selector와 exact label의 비교여야 하고, else-if/nested decision은
local predicate만이 아니라 조상 established/negated path를 포함해야 한다. case prefix는 언어
ontology 데이터, path는 parser parent/coordinate로 결정한다. complement는 direct `else_of`와 outer
path를 함께 보존한다. grader도 같은 materializer expected AST와 비교해 정답지와 채점기의 정의처를
하나로 맞췄다. 반증 후 전체 840 tests OK(skipped=5)다.

## 77. 두 번째 cold 반증 — prompt JSON 무결성·유한성·NL 증거 정규화 (2026-08-04)

`spec116-cold-20260804-122400`은 15분을 넘긴 뒤에도 51개 의미 호출이 완료되지 않았고 일부는
8분 이상 진행 중이었다. stream idle timeout은 청크가 계속 오는 생성에는 무력했다. run은 정확한
parent/child PID만 종료했으며 성공 성능 자료로 채택하지 않는다.

저장 prompt를 직접 파싱해 1차 원인을 코드에서 확정했다. `_safe_format`이 placeholder 치환 후
`}}`를 전역 치환해 주입된 `rule_evidence_json`의 nested closing brace까지 삭제했다. 316개 rules
prompt가 행 경계마다 malformed JSON이었다. 포매터를 template 단일 pass로 바꿔 삽입값을 opaque로
보존하고, nested JSON을 기계 파싱하는 반증을 추가했다.

동시에 다음 범용 수리를 적용했다.

- 최종 materialized Rule cond는 유지하되, NL prompt에서 sibling마다 반복하던 완전 확장 path를
  local `condition_definitions` 한 벌과 `condition_refs(satisfy/not_satisfy)`로 바꿨다. 큰 완료
  시료의 행 증거 52,227자 중 45,783자 중복을 제거한다.
- framework/DBMS 의미 호출에 180초 application wall deadline을 둔다. timeout 대상만
  `ModelOutputError(kind=deadline)` finding으로 격리하며 transport 재시도나 전체 run close를 하지 않는다.
- Python scalar 의미 비교로 C `0x00`과 materialized `0`을 동등 처리한다. opaque expression은 exact
  `F` 비교를 유지하고 quoted numeric과 number는 같은 값으로 보지 않는다.
- inventory의 바깥 조건 성립 상태 `holding`을 materializer가 누락하던 결함을 고쳐 nested complement를
  `outer AND else_of(inner)`로 복원했다.

전체 `846 tests OK (skipped=5)`, compileall, prompt hash 28, diff-check가 통과했다. 수정 후 cold의
벽시계·input/output·deadline 건수와 모든 의미 정확도는 아직 미검증이며 다음 성공 run에서 판정한다.

## 78. NL-only cold 완주와 구조화 batch 경계 (2026-08-04)

`spec116-cold-20260804-133619`은 response cache false, concurrency 64, Neo4j `test`에서
`completed_with_findings`로 완주했다. 벽시계는 **1,070.5초(17분 50.5초)**, successful usage는
1,006콜, input 6,034,517/output 339,365, API cached token 0이었다. 서버 로그에서는 KV cache가
실제로 동작하므로 API의 cached token 0은 cache miss 증거로 사용하지 않는다. 첫 31분 36.5초
완주보다 43.6% 짧지만 11~15분 목표에는 미달했다.

RULE은 성공 445콜(input 1.858M/output 179.5k), latency p50 40.1초/p95 156.5초/max 179.4초였고
별도 36콜이 180초 deadline으로 끝나 695행을 unavailable로 격리했다. 저장된 481개 prompt를
required 행 수별로 직접 대조하면 1~13행 364콜은 deadline 0, 14행부터 실패가 시작됐으며
20행은 68콜 중 28콜(41.2%)이 deadline이었다. 따라서 fixed-key schema 상한을 13행으로 낮춘다.
이는 언어·함수·값 특화가 아니라 구조화 응답 복잡도의 실측 경계다.

동일 target summary/RULE 481쌍의 실제 prompt bytes를 대조하면 system prompt 2,815자는 전부
동일하지만 user prompt 공통 prefix는 정확히 3자뿐이다. 서버 KV cache는 첫 전역 system 워밍으로
유지하되, 매 노드 summary를 기다리는 순서는 node-specific prefix를 추가하지 않는다. 확정된 callee
search_text와 결정론 evidence는 유지하고, 아직 생성되지 않은 현재 노드 search_text의 순환 주입만
제거해 summary/RULE/insight를 동시에 fan-out한다. 전체 854 tests OK(skipped=5), compileall,
prompt hash 28, diff-check가 통과했다. 새 cold의 벽시계와 deadline 감소는 아직 미검증이다.

## 79. 13행 batch 재측정과 search_text 책임 재배치 (2026-08-04)

`spec116-cold-20260804-140824`은 같은 canonical corpus, response cache false, concurrency 64,
Neo4j `test`에서 `completed_with_findings`로 완주했다. 벽시계는 **1,086.6초(18분 6.6초)**,
successful usage는 1,107콜, input 6,398,830/output 378,439였다. API의 cached token은 다시 0이나
사용자 확인상 서버 로그에는 KV reuse가 있다. 이 배포에서는 API cached token과 총 input token을
KV hit/miss 판정에 사용하지 않는다. input은 full prompt 논리 크기, 서버 로그는 실제 reuse 관측이다.

13행 상한은 RULE deadline을 36콜/695행에서 5콜/65행으로 줄였다. 전체 finding도 769에서
147(unavailable 80, rejected 67)로 줄어 구조화 batch 실패 폭을 크게 낮췄다. 반면 RULE 성공콜은
445→551, 전체 output은 339,365→378,439로 늘고 wall은 16.1초 악화됐다. 따라서 13행은 정확도·
격리 보호 경계로 유지하지만 속도 개선으로 판정하지 않는다. concern fan-out도 이 cold에서는
벽시계 개선 증거가 없다.

남은 출력 지배항을 원자료로 분해하면 성공 summary 406건의 `summary`는 67,184자, LLM 생성
`search_text`는 146,590자다. 여섯 장문 시료의 제한 실험에서 schema hard max 240은 대부분 경계에
잘려 문장이 끝나지 않고 식별자 보존율이 낮았다. prompt-only 240은 6개 중 5개가 상한을 넘었다.
이는 의미 손실 없는 길이 제어가 아니므로 폐기하고 같은 실험을 반복하지 않는다.

후속 제한 실험은 `summary` 자유서술 하나로의 병합도 반증했다. 간단 summary 20건은 거절 경로와
상태→callee 매핑을 생략했고, 필수 자유 문자열 5-slot은 19건에서 한 글자 채움으로 축퇴했으며
1건은 1,500 token을 소진했다. 단일 상세 summary와 단일 상세 search_text 역시 복잡 노드의 분기·
상태 매핑을 노드별로 무작위 생략했다. 따라서 summary 뒤에 signature/RW/CALL/RETURN을 원시
목록으로 덧붙이는 결정론 materializer와 자유서술 병합은 채택하지 않는다. 독립 5-field wire는
복원했다.

사용자가 재확정한 다음 후보는 **좌표 강제 의미 템플릿**이다. 코드가 확정한 분기 행·상태코드·
직접 callee·READ/WRITE·return을 동적 스키마의 required key로 만들고, 모델은 정확한 구조를 다시
서술하지 않고 각 권위 식별자/효과의 사람용 의미 라벨과 목적만 한 번 채운다. 공용 renderer가
결정론 cond/effect의 부정·연산자·값→결과 매핑을 그대로 유지한 채 상세 summary를 만들고 같은
의미를 검색에도 쓴다. 추가 LLM 호출이나 런타임 값/객체 추적은 없다.

기존 fixed-key RULE `nl`은 키 누락을 막지만 의미 정확성을 보장하지 않았다. 현재 graph의
`apascrtu:c000_main_proc` R666은 권위 cond가 `not IS_LINK`뿐인데 NL이 다음 분기의 “협력사 B가
아닌 경우”를 끌어왔다. 따라서 `nl` 문장을 검증 없이 복사하지 않으며, 정확한 조건/효과 골격은
반드시 코드가 소유한다. 별도 동적 schema의 비용을 20노드로 대조해 required 좌표 누락 0, 직접
의미 동일성, 출력 token/latency가 모두 성립할 때만 채택한다. 검증 전에는 설계 후보일 뿐이다.

### 79.1 좌표 라벨 제한 반증과 신뢰 경계

대표 4함수에서 condition atom, 내부 callee, 직접 table 좌표를 required property로 만든 실제 서버
probe는 3회 모두 4/4 필수 키를 생성했고 최종 범용 run은 6.3~16.2초, 193~1,307 output token으로
종료됐다. 구조화 키 강제는 누락·폭주에 유효하다. 그러나 모델은 같은 `"01"` 조건을 서로 다른
업무 경로로 해석하거나, 범용 지침에서 C/D 코드에 유선/무선 의미를 추측했다. 특정 corpus 식별자
예를 넣었을 때만 안정된 결과는 일반화 증거가 아니므로 폐기한다.

따라서 모델이 생성한 condition label은 권위 Rule 의미가 아니다. parser/ontology가 이미 증명한
operator shell과 exact source atom, branch inventory의 cond/then/call/return/RW가 정본이다. 제품은
언어별 조건 문자열을 재파싱하거나 이름·코드값 패턴으로 업무 뜻을 보정하지 않는다. 모델은 목적,
업무 표시명, 닫힌 stereotype, 실제 질문처럼 결정론으로 만들 수 없는 최소 의미 헤더만 한 번
출력한다. 공용 템플릿은 정본의 모든 분기 행·극성·상태코드·효과를 그대로 렌더링해 summary와
검색 문서를 만든다. 모델 실패는 해당 의미 필드 finding으로 표면화하되 결정론 Rule/Example을
없애거나 전체 run을 종료하지 않는다.

함수 `logical_name`과 코드값의 업무 뜻은 모델 책임에서 제외한다. 테이블·컬럼·변수의 논리명은
DDL/source 선언/사람 glossary에 명시된 값만 사용하고, 명시 근거가 없으면 물리 식별자를 그대로
쓴다. 상태코드·문자열·숫자 리터럴은 항상 exact 표기를 보존하며, 명시 mapping이 있을 때만 그
뜻을 괄호 밖에 병기한다. 의미 헤더의 닫힌 wire는 `purpose`, ontology `stereotype`, `questions`다.

## 80. 역사 기록 — 당시 최종 권위 계약 (2026-08-04)

이 절은 §75~§79.1의 `결정론 코드가 최종 cond/then을 만들고 RULE LLM 호출을 제거한다` 및
`의미 출력은 purpose/stereotype/questions만 남긴다`는 결론을 폐기하고 대체한다.

1. cache OFF, concurrency 64, Neo4j `test`의 콜드 재분석을 최신 18분 6.6초에서 **11~15분**으로
   줄이는 것이 최우선이다. 서버·모델 설정은 바꾸지 않으며 RULE/EXAMPLE·의미 정확도를 낮춘 시간은
   성공으로 인정하지 않는다.
2. 최신 log의 콜별 prompt/response/input/output/latency/deadline과 Neo4j query에서 60~180초 tail,
   반복 출력, 동일 SOURCE 중복, batch/semaphore 점유의 벽시계 기여를 먼저 수치화한다. 추정으로
   프롬프트나 batch를 바꾸지 않는다.
3. SOURCE는 한 번만 보낸다. 보충 입력은 범위 밖 선언·상수의 명시 설명, 직접 DDL/comment, 즉시
   callee의 canonical summary, 외부/SKILL 계약처럼 SOURCE에 실제로 없는 증거만 둔다. CALL site,
   literal, branch 원문과 동일 사실의 자연어 목록을 반복하지 않는다.
4. callee는 `summary + search_text + return + rules`로 중복 전달하지 않는다. 목적·핵심 처리·직접
   데이터 영향·하위 위임·호출자 출력/반환 역할·미확정을 포함한 canonical summary 하나를 사람 표시,
   embedding/search, bottom-up에 공용한다.
5. 상태코드·숫자·문자열의 뜻은 추론하지 않는다. exact는 보존하고 명시 enum/상수 설명/DDL/glossary가
   있을 때만 뜻을 병기한다. 정보가 없으면 `null/needs_context`로 표면화한다.

### 80.1 LLM과 결정론 코드의 책임

- parser/AST/ontology/branch inventory는 활성 branch 좌표·행 수·exact source evidence와 파서가 이미
  제공한 CALL/R/W/RETURN, 물리 식별자/literal을 제공한다.
- LLM은 각 `R<anchor>` required key의 Python Rule DSL `cond`, `then`, `nl`을 채운다. key가 anchor를
  유일하게 나타내므로 line을 응답에서 복창하지 않아도 코드가 불변 매핑을 보유한다. 누락·추가·병합은
  dynamic closed schema로 금지한다.
- 결정론 코드는 CPython `ast.parse` 문법, 제한 DSL allowlist, 좌표·허용 symbol/literal·직접 구조 사실,
  ID/관계를 검사하고 accepted Rule에서 EXAMPLE을 파생한다. LLM cond/then을 생성·수정·대체하지 않는다.
  언어 중립 expression IR이 없는 의미 동등성은 `unverified`와 직접 검증 대상으로 남긴다.
- runtime/local 값 추적, 객체 상태 추적, analyzer의 언어별 raw-condition parser, 이름/접두/코드값
  패턴 추론, opaque source를 의미 정답지로 쓰는 행위는 금지한다.

### 80.2 최소 wire 후보와 채택 기준

한 평탄 호출은 SOURCE와 최소 보충 증거를 한 번 받고 `purpose`, 근거 있는 `logical_name`, ontology
`stereotype`, 필요한 table/callee/output 의미 좌표의 짧은 문자열 또는 null, 모든 `R<anchor>`의
`cond/then/nl`, `questions`만 반환한다. 공통 Rule leaf는 `$defs`로 한 번만 선언하고 model은 exact,
source, line, evidence, status를 복창하지 않는다. 별도 free `search_text`·callee 설명·return 목록 및
같은 SOURCE를 받는 독립 summary/rules/insight 호출은 만들지 않는다. renderer가 canonical summary를
한 번 조립한다.

제품 채택 전 20노드에서 주 에이전트가 GPU와 동일한 입력만 보고 직접 의미 정답을 만든다. 주 에이전트도
판단할 수 없으면 범용 증거원을 보충하거나 미확정으로 남긴다. 주 에이전트는 가능한데 GPU만 실패하면
입력 좌표·schema·prompt를 한 번 개선하고, 그래도 실패하면 모델 한계로 기록한다. RULE/EXAMPLE 의미,
required 완전성, 창작, input/output token, p50/p95/max latency 중 하나라도 부당하게 악화되면 채택하지 않는다.

## 81. 현재 권위 포인터 — 2026-08-05

현재 계약은 이 문서 §0과
`docs/handovers/analysis-performance-validation-canonical/README.md`에서 시작한다.
§1~§80의 명령형 문장·완료 체크·“최종/현행” 표기는 역사 증거일 뿐 현재 구현 지시가 아니다.
