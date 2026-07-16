# Domain terms 5회 독립 검증 핸드오프

작성 시각: 2026-07-14 20:50 KST
저장소: `D:\work\robo\project\robo-data-analyzer`
브랜치: `main` (dirty worktree, 사용자 WIP 포함, commit/push 금지)

## 1. 새 세션의 목표

Shopmall **Framework 전략만** 캐시를 명시적으로 비운 독립 조건에서 5회 실행한다.
각 회차에서 다음을 함께 검증한다.

1. `code_summary`가 용어·문구를 출력 상한까지 반복하지 않는가.
2. 노드별 `domain_terms`가 0~10개이며 10개를 채우기 위해 생성하지 않는가.
3. 남은 용어가 실제 검색에 도움이 되는 업무 표제어인가.
4. 원문에 있는 중요한 검색 연결 용어가 빠지지 않는가.
5. 5회 결과가 우연한 1회 성공이 아니라 안정적으로 같은 계약을 지키는가.

5회 동안 수정이 필요하면 **summary 프롬프트만** 수정한다. 분석 로직, 스키마,
정규화 필터, Scan Skill, R/W/DDL 로직은 변경하지 않는다. Framework에서 확인한
공통 규칙은 DBMS summary 프롬프트에도 충돌 없이 반영하되 DBMS CLI는 실행하지 않는다.

## 2. 매 작업 시작 전 필수

워크스페이스 `AGENTS.md`에 따라 아래 4개를 매 작업마다 원문 전체로 다시 읽는다.

1. `C:\Users\roede\.claude\projects\d--work-robo\memory\manual.md`
2. `C:\Users\roede\.claude\projects\d--work-robo\memory\principles.md`
3. `C:\Users\roede\.claude\projects\d--work-robo\memory\speckit.md`
4. `C:\Users\roede\.claude\projects\d--work-robo\memory\todo.md`

그다음 저장소의 `CLAUDE.md`, 현재 Git 상태, 현재 코드와 실제 로그를 다시 확인한다.
이 문서는 핸드오프이지 현재 진실을 대신하지 않는다.

## 3. 사용자 확정 계약

- `domain_terms`는 노드별 **0~10개**다.
- **10개는 목표가 아니라 절대 상한/마지노선**이다.
- 후보가 0개면 `[]`, 3개면 3개, 7개면 7개가 정답이다.
- 정상 후보가 11개 이상이면 아래 우선순위 상위 10개만 고르고 배열을 닫는다.
  같은 순위에서는 해당 원문에 먼저 등장한 후보를 우선한다.
- 우선순위:
  1. 외부 시스템·기관·결제망·프로토콜·공식 규격의 고유명
  2. 현업이 그대로 질문하는 낯선 업무 약어·업무어
  3. 이름 붙은 업무 정책·상태 체계
  4. 그 밖의 검색 연결 가치가 있는 고유 표제어
- 변수·함수·상수·테이블·물리 컬럼명과 단순 번역, SQL/프로그래밍 용어,
  일반 기술 배관은 용어가 아니다.
- 표제어 글자는 해당 함수 body·설명 주석·업무 문자열에 연속된 그대로 있어야 한다.
- `상세 → 상세 코드 → 상세 메시지`처럼 기존 항목을 늘여 만든 파생 체인은 금지한다.
- 의미는 원문에서 확인 가능한 사실만 쓴다.
- 용어 수가 적다는 이유로 일반어·코드값·합성어를 채워 넣지 않는다.

## 4. 변경 허용 범위

판정 실패 시 의미 규칙을 바꿀 수 있는 파일은 아래 summary 프롬프트뿐이다.

- `prompts/framework/code_summary.yaml`
- `prompts/dbms/code_summary_batch.yaml` — Framework와 같은 공통 계약만 동기화
- `prompts/framework/code_summary_core.yaml` — 축소 폴백 자체가 반복할 때만

프롬프트 변경 후 생성되는 `cli_scripts/prompt_hashes.json` 갱신은 기계적 산출물로 허용한다.
그 밖의 `.py`, 모델, 정규화, Skill, Scan, R/W/DDL 파일은 이번 5회 실험에서 변경 금지다.
특정 Shopmall 표제어 목록을 프롬프트에 정답처럼 하드코딩하지 않는다.

## 5. 현재 구현 상태

이미 적용된 핵심 변경:

- Framework/DBMS summary 프롬프트에 0~10 절대 상한, 비충전 원칙, 우선순위,
  정상/실패/10개 경계 예시가 있다.
- `SummaryOutput`과 `BatchSummaryItem`은 `max_length=10`이다.
- `shared/domain_terms.py`도 정규화/저장 경계에서 11개를 거부한다.
- 표제어가 원문에 없거나 중복/코드 식별자로 판정되면 결정론적으로 제거하고
  `domain_terms_dropped`를 기록한다.
- Framework 본 summary 실패 시 `code_summary_core.yaml`로 핵심 3필드만 1회 폴백한다.
  이 폴백은 `search_text`와 `domain_terms`를 만들지 않는다.
- Scan Skill 계약은 그대로 복사하고 정규식을 실제 소스에서 검증하도록 정리되어 있다.
  식별자의 `_`, 인용된 `-`, `.`, `$`를 분해/정규화하지 않는다.
- 연결된 R/W 테이블과 DDL 전체 컬럼은 구조화 컨텍스트로 전달하며, 출력에서는 실제
  연결 테이블과 DDL에 정확히 존재하는 컬럼만 보존한다.
- 불변 상수는 구조화 값으로 전달되어 Example이 `RET_DENIED=-4`, `ST_ORDER_SHIP="40"`
  같은 실제값을 사용할 수 있다. 가변 초기값은 런타임 실제값으로 간주하지 않는다.

관련 명세:

- `specs/050-bounded-domain-terms/`
- `specs/051-ddl-logical-name-coverage/`
- `specs/052-structured-symbol-values/`

## 6. 캐시 계약 — 혼동 금지

현재 `.env`는 사용자 요구대로 다음 값이다.

```dotenv
ROBO_CACHE_ENABLED=true
```

이 값과 GPU 서버 캐시는 서로 다르다.

- `ROBO_CACHE_ENABLED=false`는 `shared/llm/client.py`에서 LangChain 프로세스 내부
  응답 캐시를 `None`으로 강제할 뿐이다.
- `true`는 내부 캐시를 강제로 끄지 않는다는 뜻이다. 현재 저장소에는 SQLite/InMemory
  cache를 새로 설치하는 `set_llm_cache(...)` 코드가 없다.
- GPU 서버는 SGLang RadixAttention prefix cache를 별도로 운용한다.
- 서버가 `cached_tokens`를 응답하지 않으므로 로컬 로그의 `cached_tokens=0`은
  **캐시 미사용 증거가 아니다**.
- SGLang 공식 관리 엔드포인트는 `POST /flush_cache`다.
  현재 OpenAI 호환 base URL은 `http://ai-server.dream-flow.com:30000/v1`이므로 후보
  관리 URL은 `http://ai-server.dream-flow.com:30000/flush_cache`다.

매 회 실행 직전에 분석 요청이 진행 중이지 않은지 확인하고, 아래처럼 비밀값을 출력하지
않는 방식으로 flush한다. HTTP 200이 아니면 캐시를 비웠다고 주장하지 않는다.

```powershell
@'
from dotenv import load_dotenv
import os
import httpx

load_dotenv()
token = os.environ["ROBO_LLM_API_KEY"]
response = httpx.post(
    "http://ai-server.dream-flow.com:30000/flush_cache",
    headers={"Authorization": f"Bearer {token}"},
    timeout=30,
)
print(response.status_code, response.text[:200])
response.raise_for_status()
'@ | .venv\Scripts\python.exe -
```

주의: 이 엔드포인트가 여러 사용자가 공유하는 서버 전체 캐시를 비우는 구조라면 다른 작업에
영향을 줄 수 있다. 사용자가 이 5회 검증을 위해 flush를 명시했지만, 서버가 공유 운영 자원이고
현재 인증에 관리 권한이 없으면 401/403을 우회하지 말고 전용 endpoint 또는 운영자 승인을
받는다. 404/405도 무시하지 않는다.

또한 현재 모델 설정은 `top_k: 1`, `temperature: 0.1`이라 캐시를 비워도 같은 입력에 같은
결과가 나올 수 있다. 그것은 캐시 재사용이 아니라 결정적 디코딩의 정상 결과다. sampling 설정은
이번 작업에서 변경하지 않는다. 검증 대상은 **같은 정상 결과의 재현성**과 **응답 내부 반복 폭주**다.

## 7. 직전 실제 Framework 실행 기준선

실행 명령:

```powershell
.venv\Scripts\python.exe cli_scripts\analyze_data_dir.py shop_mall --strategy framework
```

직전 run:

- run id: `20260714_113646`
- 소요: 365.6초
- CodeNode: 124개
- `rule:code_summary`: 124회
- summary 출력 토큰: 총 36,308, 평균 292.8, 최대 774, p95 533
- `search_field_overflow`: 0
- `summary_failed`: 0
- `LengthFinishReason`: 0
- `error.log`: 140줄이지만 ERROR severity 0
- domain terms: 총 230개, 47개 노드는 0개, 최대 10개, 정확히 10개인 노드 1개
- `domain_terms_dropped`: 87개 노드 경고, 원문 부재 176개 + 코드 식별자 72개 제거
- glossary: collected 160, synthesized 124, rejected 36
- 전체 완료 및 산출물 저장 성공

직전 문제 run에서는 `upsert_settlement` summary가 1,500 출력 토큰까지 용어를 연쇄 생성하고,
같은 `code_summary` 프롬프트로 재시도한 폴백도 다시 1,500 토큰에 도달해 해당 노드 summary가
실패했다. 파생 예시는 `PG사 정산 대조 결과 상세 → ... 상세 코드 → ... 상세 메시지`였다.
새 프롬프트를 적용한 직전 run의 `upsert_settlement`은 출력 폭주 없이 아래처럼 끝났다.

- logical name: `정산 건 신규 적재 및 금액 재계산`
- summary: 확정 상태 skip, 보류 상태 재계산, 미존재 신규 적재, 주문 정산 반영 표시 갱신
- domain terms: `정산` 1개
- 단, 원문 line 449의 `보류`가 표제어에서 빠져 품질/누락 검증은 아직 미완료다.

## 8. 현재 용어 품질의 사실 기반 예비 판정

직전 1회는 **폭주 방지 성공**, **용어 품질 최종 합격 아님**이다.

도움이 되는 실제 예:

- `PG사`, `결제수단`, `수수료율`
- `대사`, `재정산`, `정산대상`, `포인트결제분`
- `도서산간`, `무료배송`, `본인인증`, `휴면`, `탈퇴회원`
- `라운드로빈`, `안전재고`, `가용재고`
- `SMS 게이트웨이`

남아 있는 노이즈/의심 예:

- 코드값/명령값: `W01`, `D04`, `ENC`, `GRADE`, `CHANNEL`, `STK`, `RESERVE`,
  `CANCEL`, `PRICE`, `DISPLAY`, `CPN*`, `EVT*`
- 너무 일반적인 말: `오류`, `취소`, `회원`, `재고`, `창고`, `단가`, `상한`, `누적`
- 뜻 품질 문제: `db_encrypt`의 `ENC` 뜻에 `내부 사관례` 오타가 있었다.
- 일관성 문제: 같은 쿠폰 코드가 노드마다 정액/정률 등 다르게 설명된 사례가 있다.

의심 누락 예:

- `upsert_settlement`: 원문에 `보류`, `대사 재판정 대기`, `정산반영`이 있으나 `정산`만 남음.
- 0개인 47개 노드가 모두 누락은 아니다. 기술 helper는 `[]`가 정상이다. 업무 함수만 원문과
  대조해야 한다.
- Shopmall 원문에는 `SK-VAN`이 없으므로 프롬프트 예시의 `SK-VAN` 미출력은 누락이 아니다.

## 9. 5회 실행 절차

직전 기준선 run은 5회에 포함하지 않는다. 새 세션에서 5회를 새로 실행한다.

각 회차마다:

1. `ROBO_CACHE_ENABLED=true` 확인.
2. 실행 중인 동일 분석 프로세스가 없는지 확인.
3. SGLang `/flush_cache` 호출이 HTTP 200인지 확인하고 응답을 회차 기록에 남김.
4. 아래 Framework CLI 실행.
5. 성공/실패와 관계없이 `logs/analysis.run/latest`를 `_runs/domain-terms-5run/run-0N`에 복사.
6. 다음 회차 전에 로그·산출물 판정 완료. 폭주나 계약 위반이면 원인을 확인하고 summary
   프롬프트만 최소 수정한 뒤 단위/계약 테스트, prompt hash 갱신 후 해당 회차를 다시 센다.

```powershell
.venv\Scripts\python.exe cli_scripts\analyze_data_dir.py shop_mall --strategy framework
```

동시에 두 run을 실행하지 않는다. 각 run은 새 `latest`를 덮어쓰므로 반드시 회차별로 보존한다.

## 10. 회차별 필수 판정

### 폭주/종료

- `search_field_overflow == 0`
- `summary_failed == 0`
- `LengthFinishReason == 0`
- summary output max가 1,500에 닿지 않음. 직전 안정 기준 774와 큰 차이가 나면 원문 출력 확인.
- 동일·유사 표제어/문장이 길이 제한까지 반복되지 않음.
- `code_summary_core`가 호출되었다면 원인과 출력 원문을 별도 기록. 단순 성공으로 덮지 않음.

### 개수/근거

- 모든 노드가 0~10개. 11개는 즉시 실패.
- 10개 노드는 원문에 실제 독립 후보 10개가 있는지 수동 대조.
- 각 `term`이 해당 노드의 body·설명 주석·업무 문자열에 그대로 존재하는지 확인.
- `상세/코드/메시지/결과` 파생 체인, 중복, 한 개념의 유사 변형 0.
- 0~9개 노드가 개수를 채우기 위한 일반어/코드값을 포함하지 않음.

### 도움 여부

각 용어를 아래 셋 중 하나로 분류하고 근거를 남긴다.

- 유지: 현업 검색어로 코드/함수를 찾는 데 실제 다리 역할을 함.
- 제거 후보: 코드값·구현 식별자·SQL/기술어·너무 일반적인 말.
- 보류: 도메인 고유성 또는 뜻 근거가 불명확함.

유지율만 높이려고 용어를 적게 뽑으면 안 된다. 다음 핵심 표제어가 적절한 업무 노드에서
최소 한 번은 보존되는지 원문과 대조한다: `PG사`, `대사`, `재정산`, `정산대상`, `도서산간`,
`무료배송`, `본인인증`, `휴면`, `라운드로빈`, `안전재고`, `가용재고`, `SMS 게이트웨이`.

### 누락

“누락 0”을 개수만으로 주장하지 않는다. 아래 표본을 5회 모두 원문 대조한다.

- `payment.c:load_pg_company`
- `settlement.c:upsert_settlement`
- `settlement.c:match_pg_result`
- `settlement.c:settlement_reprocess`
- `shipping.c:shipping_calc_fee`
- `shipping.c:send_ship_sms`
- `catalog.c:pick_warehouse`
- `inventory.c:inventory_outbound`
- `member.c:member_authenticate`

표본 외에도 각 회차에서 `domain_terms=[]`인 업무 함수와 정확히 10개인 함수를 전수 확인한다.
기술 helper의 빈 배열은 정상으로 분류한다.

### 5회 비교

- 회차별 총 용어 수, 0개/10개 노드 수, drop 사유·개수, summary 최대 출력 토큰을 표로 작성.
- 노드별 용어 집합을 비교하여 계속 빠지는 핵심어와 한 회차만 튀는 노이즈를 표시.
- 결정적 설정 때문에 5회가 같아도 정상이다. 같다는 이유만으로 캐시 미삭제라 단정하지 않는다.
- 한 번이라도 폭주/11개/파생 체인이 나오면 5회 성공이 아니다. 수정 후 독립 5회를 다시 센다.

## 11. 테스트와 마감

프롬프트 변경 전후 최소 검증:

```powershell
.venv\Scripts\python.exe -m unittest shared.test_domain_terms shared.test_domain_term_prompt_contract -v
.venv\Scripts\python.exe analysis_flow/step4_linking/stage1_regex_grep/test_shopmall_skill_patterns.py
.venv\Scripts\python.exe analysis_flow/step3_scan_project/test_framework_overlap_guard.py
.venv\Scripts\python.exe analysis_flow/step6_analyze_code/test_rw_table_filter.py
.venv\Scripts\python.exe cli_scripts/check_byte_identical.py --update
.venv\Scripts\python.exe cli_scripts/check_byte_identical.py
```

현재 환경에는 `pytest`가 설치되어 있지 않으므로 위 독립 실행형 테스트를 사용한다.

최종 보고에는 다음을 분리한다.

- 5회 모두 확인된 사실
- 한정된 표본에서만 확인한 품질
- 남은 노이즈와 누락 의심
- 프롬프트 변경 diff
- 실행/테스트 명령과 결과

검증하지 않은 것을 “누락 없음”, “범용 100%”, “완전 해결”이라고 표현하지 않는다.

## 12. 보존해야 할 작업 상태

- 저장소는 이미 많은 관련 변경과 사용자 소유의 검색 WIP가 섞인 dirty 상태다.
- 특히 `search/config.py`, `search/retriever.py`, `search/eval/anchor_ab_eval.py`,
  `search/test_global_anchor.py`, spec 053은 이번 summary 검증과 무관하므로 건드리지 않는다.
- unrelated 변경을 되돌리거나 stage/commit/push하지 않는다.
- `.env` 비밀값을 출력·문서화·커밋하지 않는다.
- 최신 run 끝에 `ReferencedSymbolContext`가 msgpack 허용 목록에 없다는 checkpoint warning이
  1건 있었지만 run 산출물 저장은 완료됐다. 이번 summary 프롬프트 5회 범위에서 로직 수정하지 않는다.

## 13. 새 세션 복붙용 요청문

```text
D:\work\robo\project\robo-data-analyzer 작업을 이어서 진행하세요.

먼저 워크스페이스 AGENTS.md 지침대로 아래 메모리 4개를 원문 전체로 순서대로 다시 읽고,
저장소 CLAUDE.md, 현재 Git dirty 상태, 현재 코드와 실제 로그를 재확인하세요.
1) C:\Users\roede\.claude\projects\d--work-robo\memory\manual.md
2) C:\Users\roede\.claude\projects\d--work-robo\memory\principles.md
3) C:\Users\roede\.claude\projects\d--work-robo\memory\speckit.md
4) C:\Users\roede\.claude\projects\d--work-robo\memory\todo.md

그다음 아래 핸드오프 문서를 처음부터 끝까지 읽고 그대로 이어서 수행하세요.
D:\work\robo\project\robo-data-analyzer\specs\050-bounded-domain-terms\HANDOFF_5RUN.md

핵심 요구:
- 새 세션에서 Shopmall Framework 분석을 새로 5회 수행하세요. 직전 기준선은 5회에 포함하지 마세요.
- ROBO_CACHE_ENABLED=true를 유지하세요.
- cached_tokens=0을 GPU 캐시 미사용 근거로 쓰지 마세요. 서버가 값을 안 주는 것입니다.
- 매 회차 직전에 SGLang /flush_cache가 실제 HTTP 200인지 확인하고, 실패를 무시하지 마세요.
- 5회 사이 결과를 각각 보존하고, 동시에 실행하지 마세요.
- 폭주 여부뿐 아니라 실제 뽑힌 용어가 검색에 도움되는지, 코드값/일반어 노이즈인지,
  중요한 원문 용어가 누락됐는지를 함수 원문과 대조하세요.
- domain_terms는 중요도순 0~10개이며 10개는 목표가 아니라 절대 상한입니다.
- 실패가 나오면 summary 프롬프트만 수정하세요. 분석 로직/스키마/필터/Scan/RW/DDL은 수정 금지입니다.
- Framework에서 확정한 공통 summary 규칙은 DBMS summary 프롬프트에도 충돌 없이 반영하되,
  CLI 실검증은 Framework만 하세요.
- happy path만 보지 말고 0개, 10개, 11개 거부, 파생 반복, 코드값/일반어, 누락 경로를 확인하세요.
- 5회 중 한 번이라도 폭주/11개/파생 체인이 나오면 수정 후 독립 5회를 처음부터 다시 세세요.
- 사용자 WIP를 보존하고 commit/push하지 마세요.

중간에 멈추지 말고 5회 실행·원문 대조·필요한 summary 프롬프트 수정·재검증·최종 비교표까지
혼자 진행하세요. 검증되지 않은 것을 100% 또는 누락 없음이라고 말하지 마세요.
```
