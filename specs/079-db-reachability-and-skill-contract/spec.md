# 079 — DB 도달성 지표 + SKILL 계약 결정론 주입

> 배경·실험 근거: `D:\work\robo\docs\작업보고내용\2026-07-22-도장번지기-실험결과와-제품반영-설계.html`
> (SKT ProFrame 코퍼스 실측: 사람 정답지 대비 테이블 R/W 95.9% 정확 일치, 도달성 전파로
> 스킬에 없던 DB 접근 함수 57개 추가 판정). 파서 선행 검증 완료:
> `antlr-code-parser/src/test/java/.../c/CProFrameMacroCallTest.java` 3/3 통과
> (PFM_TRY 매크로 인자 안 호출이 FUNCTION_CALL 로 방출됨).

## 목적

1. **DB 도달성(db_reach)**: "이 함수를 실행하면 호출 사슬 어딘가에서 DB 에 도달하는가"를
   결정론으로 판정해 FUNCTION 노드 속성으로 적재한다. (스킬·LLM 무관, 추측 0)
2. **SKILL 계약 결정론 주입**: SKILL.md 의 YAML fence 계약(table_access_patterns,
   call_wrappers)을 LLM 이 ScanResult 로 "받아 적는" 경유지를 제거하고, 결정론 로더가
   직접 파싱해 주입한다. (단일 진실 = SKILL.md 유지, 받아쓰기 누락 사고 원천 차단)

## 범위 — 1차 (이 스펙)

독립 슬라이스 2개. 서로 의존 없음.

- **슬라이스 A — db_reach 지표**
  - 시드 = R/W 엣지를 가진 함수 집합 (state `reads_writes_edges`, 이미 존재하는 데이터).
  - 전파 = internal 호출 엣지의 역방향(callee→caller) BFS.
  - 위치 = `pipeline/steps/step8_post_processing/health_metrics_step.py` (선언된 책임:
    "결정론·LLM 0, state 그래프만으로 코드노드별 지표 계산 → FUNCTION 속성 적재"와 동류).
  - 저장 정책 = spec 020 준수: 최종 판정만 저장 — `db_reach: True` 일 때만 SET
    (기존 `in_call_cycle` 과 동일 패턴). False 는 저장하지 않는다(속성 부재 = 미도달/미판정).
  - 리포트 = health_report.json 에 `db_reach {direct, indirect, total}` 추가.
- **슬라이스 B — SKILL YAML 결정론 주입**
  - `agents/skills/catalog.py` 에 YAML fence 파서 추가 (책임 동일 축: "SKILL 문서의
    결정론 독해·검증").
  - `scan_codebase_step.py` 의 기존 후처리 자리(가드들 옆)에서, matched_skill 이 있으면
    로더 산출로 `table_access_patterns`·`call_wrappers` 를 **덮어쓴다** (LLM 산출 무시).
  - 스캔 프롬프트·SKILL.md 는 1차에서 바이트 무변경 (프롬프트 계약 보존).

## 범위 밖 — 2차 (별도 태스크로 이어감, 이 스펙에 계획만 기록)

- `db_sinks:` 절 신설(+스킬 6종·템플릿) 및 call_classifier/call_linker 의 sink 대조
  (동적 SQL_KEY 등 R/W 패턴이 못 잡는 케이스의 시드 보강).
- 밑바닥 표(외부 이름 fan-in 순위) 리포트.
- `framework_runtime_functions` 의 YAML fence 승격 + 로더 주입.
- `_warn_if_call_wrappers_missing` 가드 제거(로더가 무의미하게 만듦), 스캔 프롬프트에서
  받아쓰기 지시 제거 — 프롬프트 바이트 diff 를 별도 스펙으로.

## 기능 요구

- FR-1: `reads_writes_edges` 에 등장하는 함수와, 그 함수를 internal 호출로 (몇 단계든)
  호출하는 모든 함수는 `db_reach=True` 속성을 갖는다.
- FR-2: 어느 시드에도 도달하지 않는 함수 노드에는 `db_reach` 속성이 존재하지 않는다.
- FR-3: db_reach 계산은 LLM·스킬·설정에 의존하지 않고 state 만으로 결정론이다.
  같은 state → 같은 결과.
- FR-4: matched_skill 의 SKILL.md 에 `table_access_patterns`/`ddl_transform`/
  `call_wrappers` YAML fence 절이 있으면, ScanResult 의 해당 필드는 LLM 산출과 무관하게
  fence 파싱 결과와 일치한다(pydantic 재검증 통과). — ddl_transform 은 같은 fence 에
  있는 계약이라 함께 주입한다(반쪽 받아쓰기 방지, 구현 중 확정).
- FR-5: fence 가 없거나 스킬 미매칭이면 기존 동작(LLM 산출 유지) 그대로다.
- FR-6: fence YAML 이 깨져 있으면 조용히 넘기지 않고 오류를 표면화한다(스킬 문서 결함
  = 데이터 결함).

## 검증 기준

- 단위: (A) 시드/전파/미도달/순환 포함 케이스, (B) fence 파싱·덮어쓰기·부재 시 통과·
  깨진 YAML 오류 — 신규 테스트로 고정.
- 회귀: 저장소 unittest 전수 (`.venv .. -m unittest discover -s tests -p "test_*.py" -t .`)
  변경 전 기준선(2026-07-22 실측 194 passed)과 동일 + 신규 통과.
- 실물: ProFrame 코퍼스(분석대상모음/apascrt00010t02_20260406) 분석 실행 후 Neo4j 에서
  db_reach 함수 수를 실험 산출물(docs/작업보고내용/2026-07-22-도장번지기-실험/result.json
  의 stamped 252개)과 대조. — LLM/Neo4j 실행 환경 필요, 가능 시점에 수행.

## 영향도

- 생산자: health_metrics_step(_compute_metrics·_SET_QUERY item·_write_report),
  catalog.py(신규 함수), scan_codebase_step(후처리 1개 추가).
- 소비자: Neo4j FUNCTION 속성(신규 db_reach — 부재 허용이라 기존 소비자 비파괴),
  health_report.json(키 추가 — 추가만), ScanResult 필드(형식 동일·값 원천만 변경).
- 무변경: strategies.py(스텝 목록 그대로), 프롬프트 바이트, SKILL.md 6종, 스트림/프런트,
  step1~7, link_tables/link_calls/link_external.
- `.specify/feature.json` 은 사용자 진행 중인 078 을 가리키므로 변경하지 않는다.
