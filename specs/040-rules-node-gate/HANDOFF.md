# HANDOFF — spec 040 (DBMS rules 레벨1 재설계) 다음 세션 이어가기

**작성 2026-07-08. 이 문서 하나로 다음 세션이 완벽히 이어가도록 자족 작성.**
관련: [spec.md](./spec.md) · [plan.md](./plan.md) · [작업보고.md](./작업보고.md)

## 0. 한 줄 요약
DBMS 비즈니스 규칙(rules_examples) 추출을 **문장노드마다 → 루틴의 톱레벨 문장(레벨1) 단위**로 바꿨다.
프롬프트 튜닝으로는 과추출/파편/중복이 안 잡혀(4회 실패 실증), **로직(파이프라인)을 고치는 게 정답**이었다.
FN_EDIT_YN 원문대조로 구조 성공 확인(D1~D5 완결·무중복). **단 아직 미완** — 과소추출·compact 전환·summary 조이기·검증정답지가 남음.

## 0.6 ★ 2026-07-08 2차 세션 결론 (검증 완료 → 확정 솔루션) — 여기가 최신 진실
정답지(`answer_key.md`) 대조로 레벨1을 검증한 결과, **파편·중복은 잡았으나 새 4문제 확인**. 그리고 **원인이 전부 프롬프트가 아니라 구조**임을 실측 확정. 배경·근거·해결 = [작업보고.md](./작업보고.md), 설계 = [spec.md](./spec.md).

**확정 문제(실측):**
- ① **과소추출** — `MINUTE_2` LOOP 6결정인데 규칙 1(일데이터 등 손실). `DATA_ANALYSYS` LOOP는 3 성공. 차이 = LOOP 내부 크기·중첩(RAW 175줄이 뭉개짐).
- ② **커서 손실** — CURSOR_VARIABLE 5개 전부 요약·규칙 0(선언 취급으로 분석 제외). `antlr_json_reader.py:38 _DBMS_VAR_TYPES` 에 `CURSOR_VARIABLE` 포함 = Variable 수집(CodeNode 아님)이 원인.
- ③ **죽은코드 환각** — `TB2` 'P=대수평균'(주석 죽은코드). 규칙 입력이 원문 재슬라이스(`raw_body=True`)라 주석 되살아남.
- ④ **값 매핑 뭉갬** — `TB2` "Q/S/D 집계"로 Q=평균/S·D=합산 소실.

**확정 솔루션(구조 수정 — 뿌리 2 + 요약):**
- **뿌리B(재료)**: 규칙 입력 **RAW → compact**(자식 요약 결정지도). `batch_analyze.py:265-269` 의 `raw_body=True` 제거 = `body_for` 기본 compact(`range_body.compact_body`). 실측 근거: LOOP 아래 DML 전부 요약 보유(MERGE[234-264]="일별 집계 반영"=정답지 일데이터). 결정은 이미 있고 열거만 실패 → 지도 주면 해결. ①③④ + 크기안전 동시.
- **뿌리A(프롬프트)**: `prompts/dbms/code_rules_examples_batch.yaml` — "각 노드=완결된 하나의 문장" 프레임 제거, "결정 N개면 규칙 N개·반복문은 다수 정상" 명시, 입력이 compact 결정지도임을 반영, "무폭주=중복금지지 규칙축소 아님"·잔심부름 제외(⑤).
- **커서(②)**: `_DBMS_VAR_TYPES` 에서 `CURSOR_VARIABLE`(쿼리 로직 보유) 제거 → CodeNode 로 승격 → 유효트리에서 루틴 톱레벨 문장으로 분석(요약·규칙 자동). 별도 인라인·짝짓기·순서제어 불필요(자기 규칙을 자기 노드에). ★ 단순 변수와 구분 확인 필요(CURSOR_VARIABLE 은 SELECT 바디 보유).
- **요약 조이기(FR-005)**: `[핵심]` 한 줄(UI)·값·테이블작업 유지 + 노이즈(커밋/롤백/예외 반복·코드토큰·산문) 제거. **⚠️ `code_summary_batch.yaml` = 사용자 spec 029 WIP — 조율 필요.**

**검증(SC)**: MINUTE_2 규칙 1→정답지커버 / 커서 규칙 등장 / 'P=대수평균' 0 / Q·S·D 매핑 정확 / 요약 길이↓+값보존 / FW 무회귀.

## 0.7 ★★ 구현 완료 + 6패스 실측 최종 결론 (2026-07-08 3차 세션) — 최신 진실
§0.6 솔루션 **전부 구현·실측 완료**(6회 재분석, 각 패스 정답지·examples 레벨 대조). 원인 규명은 `logs/llm_prompts/latest/` 실입력 대조로 확정.

**구현된 것(미커밋)**:
- `batch_analyze.py` — rules RAW→compact(요약콜과 user_values 공유), `raw_body` 파라미터 제거, `_apply_effective_tree` 에 `ctx.extra["label"]` 주입.
- `range_body.py` — compact 자식 치환 `[범위] TYPE: 요약`(타입=문장 경계 신호) + **`_strip_sql_comments`**: 골격(ancestor)에서 주석 결정론 제거(문자열 리터럴 보존·빈줄 압축). body 주석은 유지(사용자 결정).
- `antlr_json_reader.py` — `_DBMS_VAR_TYPES` 에서 `CURSOR_VARIABLE` 제거 = 커서(선별 쿼리 보유)를 CodeNode 로 분석(자기 규칙을 자기 노드에, 타이밍 의존 0).
- `prompts/dbms/code_rules_examples_batch.yaml` — **전면 재탄생**(옛 345줄의 깊이 복원 + 현행 스키마 + compact 전제): 모든 지침 밑 인라인 범용 예시(25섹션/71예시), §2 "언제 RULE 발동"(리트머스+7패턴+경계6짝) 최대상세, §1.2~1.4 서로다른작업=분리/같은정책값분기=통합/정책 다른 상위축=분리, §1.6 잔심부름 타입+용도 필터, §3.2.5 example 5단계 절차. byte-identical 유지.
- `prompts/dbms/code_summary_batch.yaml`(029 겹침, 사용자 승인) — 집합 모든 값 나열 강제·괄호병기 금지·기술노드 [핵심] 한줄 강제·예시 전부 범용화(RDD·VAL/CNT 등 제거 — **옛 "좋은 예"가 괄호병기를 가르치고 있었음**).
- `prompts/dbms/_common.yaml` — 단계4 compact 소비를 분할별로(요약=재서술X / rules=결정마다 규칙, 톱레벨=유일 소유자).

**6패스 실측 결론(정답지 대조)**:
- ✅ **구조(결정론)가 잡은 것 = 안정**: 규칙 위치(레벨1)·파편/중복 0·커서 규칙 등장·잔심부름(FETCH/EXIT/COMMIT/제어COUNT/로그) 배제·**죽은코드 환각 구조적 완치**(골격 스트립 후 '대수' 0건, 오염됐던 자식요약까지 정화. 스트립 전 지침 준수율 25%). B2EN2 IF 5~7(붕괴 1규칙에서 회복·유지), WATERSUISUL IF=6, 4RVR 1/1/1.
- ❌ **프롬프트 강화로도 진동 = 소형(36B) 잔여 한계 확정**: 축 라벨 정확성(FN_EDIT else사유가 given '60'으로 오염 지속)·깊은 중첩 열거(MIN2 LOOP 4→2→1 진동 — 결정이 레벨1보다 2~3층 깊어 지도에 요약 1줄로 압축)·괄호병기(요약 84건 잔존)·집합 완전('F'). 매 패스 한 곳 고치면 다른 곳 흔들림 — 수확 체감 확정.
- **RULE 추이**: 244(옛) → 38 → 44 → 62 → 39 → 37 → 34+α (개수 아닌 정답지 커버가 기준).

**남은 레버(후속 — 본 spec 범위 밖 옵션)**: (a) rules 분할만 대형 모델 (b) 결정지도 2레벨 펼침(깊은 결정 노출) (c) 사후 결정론 검증(값 집합 원문 대조) (d) 요약 괄호병기 결정론 후처리.
**미해결 잔여(HANDOFF §4 잔여)**: 그래프 계약 소비처 견적(rules attach 위치 이동) / ontology `rule_bearing` UNUSED 제거 / FW(zapamcom) 무회귀 재분석 미실행(SC-006) — 다음 세션.

**다음 = 구현 완료됨.** 남은 것: FW 무회귀 확인 + 잔여 정리 + 커밋(사용자 요청 시).

## 0.8 ★★★ 최종 아키텍처 확정 + 9패스 실측 (2026-07-08 4차 세션) — 이것이 최신 진실
§0.7의 "compact 결정지도" 접근을 **supersede** — 원인 재규명(요약=전달게임 손실) 후 **"예산 내 원문(1차 증거)" 통합 원리**로 재설계, 9차까지 실측 완료. **정확도: 엄격 58%→71%→78% / 부분 74%→80%→86%** (정답지 45결정, answer_key.md).

**최종 아키텍처 (전부 범용·결정론, 미커밋)**:
1. **`body_for` 단일 원리**(range_body.py): 분석 입력 = **원문이 예산(batch_max_tokens=1500tok)에 들어가면 원문, 초과 시만 compact**(자식요약 치환). 요약·테이블·규칙 3채널 공통 — 전달게임(요약 경유 손실: F누락·매핑뭉갬·괄호병기, LLM 입력로그로 실증) 제거.
2. **규칙 단위 = budget descent**(`_plan_rule_units`): 레벨1에서 시작, 예산 초과 컨테이너만 자식으로 하강(RWIS 461노드 중 gates 4개뿐, 최대 2단계). 단위 아래 규칙 금지(파편 차단) 유지.
3. **게이트 규칙**(descent의 마지막 조각): 하강한 컨테이너는 **골격**(자식 `....` 접힘, 주석 스트립)으로 자기 줄 진입조건만 규칙화. ★같은 rules 프롬프트 재사용 시 4/4 빈배열(§1.5 "빈배열 정답" 신호가 골격 상황에서 승리 — 8차 실측) → **전용 프롬프트 `code_gate_rules_batch.yaml` 분리**("기본값=규칙 1개") → 9차 4/4 회수("건수 1↑ 且 작업일 오늘 이전일 때만" 등 정답지 게이트 전부).
4. §0.7의 골격 주석 스트립·커서 CodeNode·프롬프트(발동기준 §2 최대상세·전 지침 인라인 범용예시·example 5단계)·요약 조이기 전부 유지. 4분할(요약/테이블/규칙/게이트) system 바이트동일 확인.

**9차 파일별**: FN_CALC·TRG·4RVR 만점 / MINUTE_2 6.5/7(음수보정·일데이터·게이트) / B2EN2 6/7 / WATERSUISUL 7/9 / FN_EDIT 3.5/4(3규칙 축 정확) / TB2 2.5/4.
**잔여 실점(~14%)**: 호출연쇄(CALL)·전역예외 규칙의 패스별 진동 / TB2 집계방식 then 뭉갬 / 유역 축 뭉갬 2건 / examples 코드토큰 누출(정밀도) — 소형(36B) 진동 영역. 레버: rules 분할만 대형 모델 / 사후 결정론 검증.
**FW 점검(사용자 요청)**: 특화어휘 grep 0·바이트동일 ✓·rules 정독 — 실측 결함 0이라 무변경(원칙). 잠재 이식 목록(결함 실측 시): example 5단계 절차·죽은코드블록 명시·✅❌ 형식 통일. **FW 무회귀 재분석(zapamcom)은 미실행** — 이번 변경은 전부 DBMS 경로지만 실증 필요(잔여).

## 0.9 최종 마감 (10차 + FW 무회귀, 2026-07-08 밤)
- **10차(경계 지침: "후속 위임 호출·전역 예외 복구 = 규칙 O" §1.6 추가)**: 흔들리던 4지점(TB2 CALL×2·WATERSUISUL EXCEPTION·DATA CALL) 전부 회수 + 무회귀(게이트4·FN_EDIT3·F집합·음수보정 유지, TB2 then에 집계방식까지 등장). **최종 정확도: 엄격 84% / 부분 92%** (58→92, 목표 90 달성).
- **FW 무회귀(사용자 요청, `data_fwo_zap`=원본 zapamcom 동일본 확인)**: `ROBO_DATA_DIR=/d/work/robo/project/data_fwo_zap ROBO_NEO4J_DATABASE=test … zapamcom --strategy framework` 실행 정상. 38/38 함수 요약·규칙(RULE90·EXAMPLE189·Q1), 빈요약 0·폭주 0, **goldbar 3/3 일치**(b100=Query·b210=Command·b700=Query·[핵심] 형식). 괄호병기 6건은 FW 프롬프트 기존 특성(오늘 미변경 — 이식 목록 항목)이지 회귀 아님.
- **★기준선 부재 발견**: `neo4j` DB는 zapamcom이 아니라 **다른 FW 코퍼스**(1804노드·RULE377·함수118, 겹치는 함수명 0)로 실측 확정 — 옛 메모리("neo4j=zapamcom 537·RULE71") 폐기·정정함. 1:1 동결 기준선이 없어 SC-006은 "코드경로 영향 0 + goldbar 일치"로 판정.
- **test DB 현재 상태 = zapamcom FW**(마지막 실행). DBMS 결과 재현 = RWIS 레시피 3분.
- 잔여: 그래프 계약 소비처 견적 / ontology `rule_bearing` 제거 / (옵션) 사후 결정론 검증·FW 이식 목록. **전부 미커밋.**

> ↓ 아래 §1~§7 은 1차 세션(레벨1 전환) 기록 — 배경 참고용. **현재 진실 = 위 §0.8·§0.9 + spec.md.**

## 1. 핵심 결론 (왜 이렇게 했나 — 다음 세션이 흔들리지 말 것)
- **비즈니스 규칙 = 제어흐름을 관통하는 결정**("바깥 IF → 안쪽 IF → 결과"가 한 덩어리). 이걸 IF/ELSIF/ELSE 문장노드로 쪼개 각각 뽑고 자식규칙을 부모로 롤업하면 → **파편·중복·손실**. 이건 프롬프트로 안정적으로 못 잡는다(granularity 불일치).
- **프롬프트 4회 다 실패(실측)**: 강화 per-node=168(중복), 재작성=275(중복↑), 조임=116(핵심 손실), OWN줄=253(3중 중복+오류). → 소형(GPU) 모델은 이 판단을 못 함.
- **정답 = 안 쪼개는 것.** 프레임워크가 함수단위라 오류 적은 이유와 같다. DBMS도 **루틴(PROCEDURE/FUNCTION/TRIGGER)의 톱레벨 문장을 통째로** 분석 → 조건경로 완결.
- **AST 근거**: ELSE/ELSIF는 IF의 **자식**(형제 아님, END IF 별도노드 없음). IF[35-55] 하나가 ELSIF·ELSE·중첩IF·대입을 다 품는다. 서브쿼리도 SELECT 트리 = 한 조회. → "톱레벨 문장 = 완결 결정 단위"가 자연스럽다.

## 2. 오늘 실제로 바뀐 코드 (내 변경분)
| 파일 | 변경 | 상태 |
|---|---|---|
| `analysis_flow/step6_analyze_code/batch_analyze.py` | **레벨1 게이트**: rules는 배치의 부모가 루틴(`_ROUTINE_TYPES`)일 때만 = 톱레벨 문장. `_build_batch_values(raw_body=True)` = 자식 서브트리까지 RAW 통째. 더 깊은 배치는 rules skip. | ✅ 적용·검증 |
| `prompts/dbms/code_rules_examples_batch.yaml` | **레벨1용 리빌딩**: 부모-자식·OWN줄·게이트 같은 구조 얘기 전부 제거. 순수 규칙품질만(값집합·DECODE·주석·도메인어·questions). "각 노드=완결 문장, 모든 조건경로 담아라". | ✅ |
| `prompts/framework/code_rules_examples.yaml` | 섹션화 리빌딩(fetch·writer·callee·op방향·관용구·토큰 블랙리스트). FW는 게이트 무관(three_split). | ✅ |
| `shared/lang/ontology_roles.yaml` · `shared/lang/ontology.py` | `rule_bearing`/`is_rule_bearing` 추가 — **옛(노드타입) 게이트용. 레벨1 전환으로 지금 UNUSED → 제거 대상.** | ⚠️ 정리필요 |
| `three_split_llm_fanout.py` | docstring만(게이트 원복). framework 무변경. | ✅ |
| `specs/040-rules-node-gate/` | spec·plan·이 문서. | — |
- **⚠️ 내 변경 아님(건드리지 말 것)**: `analyze_code_step.py`·`rw_table_filter*.py`·`code_summary*.yaml`·`specs/029`·`specs/039` = 사용자 기존 WIP.
- **미커밋.** 커밋은 사용자 요청 시만.

## 3. 검증 결과 (레벨1 run, Neo4j `test` DB)
- FN_EDIT_YN: **2노드/4규칙** — L35-55 IF(3규칙), L59-99 IF(1규칙). 깊은 노드(L36·L50·L61…) 규칙 **0**.
- **원문대조 D1~D5 전부 완결**: R1 `IN_DATAKIND='60' AND NOT IN('Q','P') AND ≠'SQ' AND NOT(D&D)→'Y'`(바깥+안쪽 조건 합침), R2 15분, R3 else(사유 포함), L59 하위조회. **무파편·무중복·무손실.** ← 구조 성공 증거.
- 전체 RULE 244→**38**, 전부 톱레벨 문장(IF/LOOP/MERGE/SELECT)에만.

## 3.5 ★ 핵심 설계 결정 — 톱레벨 문장을 "RAW 통째"로 넣는다 (현재) → "compact"로 전환 (다음)
- **지금 구현 = RAW 통째**: 톱레벨 문장(IF[35-55] 등)을 분석에 줄 때 `_build_batch_values(raw_body=True)` 로
  **자식 서브트리까지 원문 소스 그대로**(자식 요약 치환 X) 준다. 근거 = 조건경로(바깥 IF+안쪽 IF)를 **끊김 없이
  완결로** 보게 하려고. compact(자식→요약)면 요약이 값·매핑을 잃을까 봐 raw를 택했다.
- **RAW 의 문제 = 크기**: 프로시저가 거대(1000줄+, 500줄짜리 톱레벨 MERGE)하면 raw 한 덩어리가 토큰 폭발.
  RWIS(100~350줄)는 톱레벨 문장이 최대 ~100줄이라 raw가 한 콜에 들어가 **지금은 문제 없음**. 하지만 **범용성엔 부족.**
- **해결 = compact 로 전환(다음 세션)**: 자식 summary 가 결정·값을 이미 담는 것을 실측 확인
  (L36="처리 Q/P아님 且 SQ아님 且 적산차아님 → 'Y'", L84="DECODE 로 생활'LIFE'/공업'INST' 구분 조회").
  → **compact 써도 완전성 유지 + 거대 프로시저 안전.** `raw_body=True` 제거하면 `body_for` 기본이 compact.
  전제조건 = summary 가 "요약답게 + 결정·값·상호작용 테이블은 담게"(§4-3). 전환 후 원문 대조로 완전성 재검증.
- **요약**: RAW=완전하지만 크기위험(현재) / COMPACT=크기안전, summary 품질 의존(다음 목표). RWIS만 보면 raw로 충분하나, 범용·안전을 위해 compact 가 최종 방향.

## 4. ★ 남은 작업 (다음 세션, 우선순위 순)
1. **[최우선] 38 과소추출 해결.** 노드당 1규칙 고집 = 잘못. 톱레벨 문장 하나에 **서로 다른 결정이 N개면 규칙 N개**를 내도록 프롬프트 강화. 특히 MINUTE_2(2규칙/296줄)·WATERSUISUL(8/341줄)이 의심 → **원문 대조로 완전성 확인**(아래 §5 정답지 방식으로).
2. **rules raw→compact 전환.** 거대 프로시저(1000줄+)면 raw가 너무 큼. 자식 summary가 결정·값을 담으므로(검증됨: L36="Q/P아님 且 SQ아님…→Y", L84="DECODE 생활/공업") **compact로 충분·안전**. `batch_analyze.py`에서 `raw_body=True` 제거(= `body_for` 기본 compact). 전환 후 완전성 재검증.
3. **summary 조이기.** 현재 435노드 중앙144·최대860자·200자+ 107개 + 코드토큰 누출. **필수정보는 유지**(상태코드값·초기값·상호작용 테이블) **하되 노이즈·장황함 제거**(요약 본질). `prompts/dbms/code_summary_batch.yaml`(단 이건 사용자 spec 029 WIP와 겹침 — 조율 필요).
4. **example 토큰누출.** given/then의 raw 변수·테이블명(`V_FLAG`·`IN_DATAKIND`·`RDITOTALSQ_TB`·`LI_GUBUN`) → 도메인어. 현재 example의 **52%(35/67) 누출.** statement는 도메인어로 깨끗 → example만 조이면 됨.
5. **죽은코드 'P'** (RDD01DD_TB2 대수평균): 원문 자체 모순(헤더주석+죽은블록=대수평균 vs 활성=단순평균). 프롬프트로는 불가 실증. → 활성코드 우선 + 모순 시 question, 또는 파이프라인에서 주석 제거 검토(단 antlr가 이미 주석 버리는데 `code_text`가 raw 재슬라이스로 되살림 = `analyze_code_step.py:380` `source_cache.slice`).
6. **그래프 계약 변경 견적.** rules가 문장노드→톱레벨 문장에 attach로 이동. 프론트/카탈로그 등 HAS_RULE 소비처 blast radius 확인(별도 SDD 필요할 수 있음).
7. **정리(클린)**: `rule_bearing`/`is_rule_bearing`(ontology) 제거 — 레벨1 후 UNUSED.

## 5. ★★ 효율적 검증 방법 (사용자 지시 — 매번 원문 재독 금지 = 토큰 절약)
**원문에서 비즈니스 룰을 한 번에 싹 다 뽑아 "정답지" 파일로 만들고 → 그래프와 대조.**
- 다음 세션 **첫 작업 = RWIS 9파일 원문 정독 → 파일별 비즈니스 룰 목록(정답지)을 `specs/040-rules-node-gate/answer_key.md` 에 작성.** 이후엔 원문 안 읽고 정답지↔그래프만 비교.
- **정답지 seed(이미 추출됨) — FN_EDIT_YN.sql**:
  - D1 기본 = 수정불가('N').
  - D2 자료='60' & 처리 NOT IN('Q','P') & 변량≠'SQ' & NOT(처리='D' 且 기능='D') → 수정가능('Y').
  - D3 자료='15' & 처리 IN('Q','P') → 수정가능('Y').
  - D4a else(그 외 자료) & 처리 IN('Q','P') → 수정불가 + '- 15분 테이블을 수정하세요' 사유.
  - D4b else & 변량='SQ'(공급량) → 수정불가 + '- 공급량 태그는 수정할 수 없습니다' 사유.
  - D4c else & 처리='D' 且 기능='D'(적산차) → 수정불가 + '- 적산차 태그는 수정할 수 없습니다' 사유.
  - D5 수정불가('N')면 → 통합공급량/적산차 태그의 하위 적산 TAG 목록 조회해 플래그에 콤마로 추가(하위 수정 유도).
  - (나머지 8파일 = 다음 세션에서 원문 읽어 채울 것.)

## 6. 실행·검증 레시피 (환경)
- **재분석(DBMS)**: `cd robo-data-analyzer && ROBO_NEO4J_DATABASE=test ./.venv/Scripts/python.exe cli_scripts/analyze_data_dir.py RWIS --strategy dbms`  (~3분, 사내 SGLang LLM 필요, 시작 시 test DB wipe 후 재생성).
- **FW**: `ROBO_NEO4J_DATABASE=neo4j ... zapamcom --strategy framework`.
- **Neo4j**: `.env` 자격(`bolt://127.0.0.1:7687`, DB=test/neo4j). 조회 헬퍼 = `<scratchpad>/neo4j_helper.py`(run(db,cypher)). Neo4j 6.x = `AS` 별칭 필수, cp949 콘솔 → `PYTHONIOENCODING=utf-8`.
- **원문**: `d:/work/robo/project/data/source/RWIS/*.sql`(9), AST=`data/analysis/RWIS/*.json`(재파싱 불요).
- **정답지 DB**: `test`=RWIS(DBMS), `neo4j`=zapamcom(FW).
- **AST 트리 덤프**(구조 확인): 노드 type+startLine+children 재귀출력 스크립트 이미 사용함.

## 7. 검증 히스토리 (프롬프트가 왜 안 됐나 — 재시도 말 것)
| 시도 | RWIS RULE | FN_EDIT_YN | 문제 |
|---|---|---|---|
| 원본 | 244 | 26 | 파편·중복·환각 |
| 강화 per-node | 168 | 20 | D1~D5 有, 중복 中 |
| 프롬프트 재작성 | 275 | 29 | 중복 ↑ |
| restrictiveness 조임 | 116 | 5 | **핵심 결정 손실** |
| OWN줄 원칙 | 253 | 25 | 3중 중복 + 역전오류 |
| **레벨1 로직(현재)** | **38** | **4** | **완결·무중복 ✅ (단 과소추출 의심 — §4-1)** |
→ 결론: 프롬프트는 4번 다 실패. 로직 수정이 정답. 다음 세션은 §4 남은작업(특히 과소추출·compact·정답지검증)부터.
