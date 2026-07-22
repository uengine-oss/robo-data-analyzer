# 079 tasks

기준선(실측 2026-07-22, 변경 전): unittest 전수 288 passed / 5 skipped (`-t .`).
변경 후: 298 passed / 5 skipped — 기존 전부 유지 + 신규 10개.

## 슬라이스 A — db_reach 지표

- [x] A1. `health_metrics_step._compute_metrics` 에 db_reach 계산 추가
      (시드=reads_writes_edges 보유 함수, internal_edges 역방향 BFS). 머리말 갱신.
- [x] A2. 저장 item 에 `db_reach: True`(참일 때만) — in_call_cycle 패턴 준수.
- [x] A3. health_report.json 에 `db_reach {direct, indirect, total}` 추가.
- [x] A4. 속성 문서화 — 조사 결과 risk_level/in_call_cycle 도 graph/ 에 별도 등록처가
      없음(관례 = health_metrics_step 머리말이 문서). 동일 관례로 머리말에 기재 완료.
      별도 레지스트리 신설은 억지 구조라 하지 않음.
- [x] A5. 신규 테스트 `tests/pipeline/step8_post_processing/test_health_metrics_db_reach.py`
      — 4 케이스(직접/간접·순환·무시드·결정론) 통과.
- [x] A6. 회귀 전수 통과 (298 OK).

## 슬라이스 B — SKILL YAML 결정론 주입

- [x] B1. `agents/skills/catalog.py` `load_skill_contract()` — table_access_patterns·
      ddl_transform·call_wrappers fence 파싱, 깨진 YAML 은 SkillCatalogError.
      (ddl_transform 은 같은 fence 계약이라 스펙 FR-4 에 포함시킴)
- [x] B2. `scan_codebase_step._apply_skill_contract()` — matched_skill fence 값으로
      덮어쓰기 + pydantic 재검증 + 로그 표면화. 기존 가드들 앞에 배치.
- [x] B3. 신규 테스트 `tests/agents/test_skill_contract_loader.py` — 6 케이스
      (실물 proframe 파싱 고정·미존재·fence 없음·깨진 YAML·덮어쓰기·미매칭 유지) 통과.
- [x] B4. 회귀 전수 통과 (298 OK).

## 선행 검증 (파서)

- [x] P1. `antlr-code-parser/src/test/.../c/CProFrameMacroCallTest.java` 3/3 통과 —
      PFM_TRY 매크로 인자 안 호출이 FUNCTION_CALL(이름+라인)로 방출됨을 회귀로 고정.
      (C FUNCTION_CALL 은 인자 텍스트를 담지 않는 것이 기존 계약 — SQL_KEY 는
      analyzer 원문 정규식 소관이므로 요구 계약 아님을 테스트 주석에 명시)

## 완료 후

- [ ] C1. **실물 검증 (미수행)**: ProFrame 코퍼스 실분석 → Neo4j db_reach 함수 수를
      실험 result.json(252)과 대조. LLM·Neo4j 실행 환경 기동 시 수행.
- [x] C2. 작업보고 문서 갱신.

## 실물 검증 중 관찰과 조치 (2026-07-22)

- ProFrame 코퍼스 1차 실행: Scan Agent 가 `max_calls=60` 소진으로 실패.
  호출 분포 실측 — **60콜 중 55콜이 동일 정규식 재검증 루프** (파일읽기 4·검색 3·
  SKILL 읽기 1 등 정상 스캔은 소수). 한도 200 재시도(2차)도 같은 궤적이라 중단.
- 원인 판정: 한도(조절 로직)가 아니라 **약한 모델에게 준 열린 임무(계약 전사·검증)**가
  근본 원인. 2차 백로그(프롬프트에서 전사 임무 제거)의 실측 근거.
- 즉시 조치 (스코프 추가, 프롬프트 바이트 무변경): `agents/tools/validation.py`
  `verify_table_regex` 에 **멱등 가드** — 동일 call_regex 재호출 시 재계산 없이
  캐시 결과 + "재검증 불필요, 다음 단계로" 안내 반환. 루프의 연료 차단.
- [x] 가드 테스트 `tests/agents/test_verify_table_regex_idempotent.py` 3 케이스 통과.
  전수 301 OK (기준선 288 + 신규 13).

## 2026-07-22 후반 추가 작업 (사용자 지시 — 전부 완료, 전수 314 OK)

- [x] **링커 가드 회귀 수정**: `_entity_group_complete` 후방 검사를 entity 그룹 끝이
      아니라 **매치 전체 끝** 기준으로 (7/21 병합의 신설 가드가 SQL_KEY 꼬리 소비형
      패턴 314/314 오탈락 — 실측). 회귀 케이스 3종을 기존
      `test_shopmall_skill_patterns.py` 에 추가 (신규 테스트 파일 생성 금지 지시 준수).
- [x] **logs 재구성(레거시 복귀)**: run 산출 루트 `run-output` → `logs`, 계층 제거 —
      `logs/` 바로 아래 analyzer.log·error.log·jsonl 3종·usage_summary·health_report·
      llm-prompts/ 만. `analysis/latest` 계층·`artifacts/` 덤프 로직 제거
      (`artifact_writer.py` 삭제, run_executor 성공/실패 경로 정리 — 실패 시 state
      보존 기능도 함께 제거됨을 명시). 계약 테스트 2종 갱신.
- [x] **병렬 기본 30→50**: SGLang 실측(동시 64·KV 561k·평균 10.3k 토큰) 근거.
- [x] **테스트 정리**: 함수식(스위트 미수집=일회성) 테스트 파일 12개에 스위트 편입
      브리지 추가 → 잠들어 있던 실패 발굴·수리: 문법 3(브리지 삽입 위치),
      낡은 계약 대상 2(batch_prompt_values 이사 미반영), 옛 스킬 경로 1,
      죽은 import 2(batch_analyze). 고객 스키마명(zpay_/zord_) 픽스처 중립화.
- [x] **메모리 규칙 2건**: no-new-databases(사용자 등록 확인) +
      test-via-product-scripts-only(신규 등록).
- [x] spec079 DB: 현 활성 스토어에는 존재하지 않음(no-op). ※ 19:49 에 7687 의
      Neo4j 인스턴스가 교체되어(옛 13:39 프로세스 종료) 옛 스토어(spec079·
      shopmallgoald·skill 보유)는 정지 상태 — 그 스토어 기동 시 spec079 만 DROP.

## 2차 백로그 (별도 착수)

- db_sinks 절(+스킬 6종·템플릿) + call_classifier/call_linker sink 대조
  (동적 SQL_KEY 시드 보강) + 밑바닥 표(외부 이름 fan-in) 리포트.
- framework_runtime_functions 의 YAML fence 승격 + 로더 주입.
- `_warn_if_call_wrappers_missing` 가드 및 스캔 프롬프트 받아쓰기 지시 정리
  (프롬프트 바이트 diff 명시 필요 — 별도 스펙).

## 2026-07-22 심야 — 성능·폭주 근본수리 (사용자 지시: "깔끔하게 싹")

계약(변경 전 고정):
- [x] **:ANALYZER_OWNED 마커 라벨 + id 유니크 제약**: 코드 노드 라벨은 열린 집합이라
      종류별 인덱스 불가 → 전 analyzer 노드 공통 마커에 제약. 무라벨 `MATCH {id}` 는
      AllNodesScan(엣지 1건당 전 노드 스캔) — run6 실측 AST 적재 161초의 159초가 이것.
      쓰기 경로 24곳 전부 `:ANALYZER_OWNED` 시크로 전환. 이름은 ANALYZED 검토 후
      의미 정확성(스텁은 분석 안 됨)으로 개명. **run7 실측: AST 161초 → 6초.**
- [x] **한줄요약 폭주 근본수리**: run7 실측 — 스칼라 1개 출력에 JSON 문법 강제 시
      `{` + 공백 3만 토큰 폭주(1건, 266초, step6 임계경로). 평문 텍스트 모드 신설
      (run_yaml_rule response_model=None) + 결정론 검증(한 줄 정규화·120자) +
      rule max_tokens 300(정상 실측 최대 65의 4.6배) + 본문 폴백 유지.
- [x] **반복 페널티 0.15**: 두 폭주(공백 루프·동일 의미 77회 반복)의 공통 뿌리 =
      top_k=1 탐욕 디코딩의 반복 어트랙터. presence_penalty 는 같은 입력→같은 출력
      결정론 규칙(랜덤성 아님). run8 gold 대조로 품질 확인 후 확정.
- [ ] **run8 검증**: 제품 스크립트·neo4j DB(시작 초기화)·R/W 103·db_reach·416/416·
      폭주 0·rules 품질 이상 소멸 여부·단계별 시간 실측.
- [ ] **병렬·인덱스·쿼리 재감사**: EXPLAIN 인덱스 시크 확인, UNWIND 배치 검토,
      남은 병목 실측 지도.
- 재감사 관찰 (2026-07-22 심야): ① EXPLAIN 확증 — 엣지 MATCH AllNodesScan →
  NodeUniqueIndexSeek. ② UNWIND 는 호출당 단일 배치(분할 없음) — 현 규모(4천 노드
  4.8초) 근거 부족으로 분할 미도입, 10만+ 노드 프로젝트에서 tx 크기 재평가.
  ③ step6↔step7 겹침 평가·기각 — 양쪽 모두 동시성 64 GPU 포화라 겹쳐도 총 GPU
  작업량 불변(이득 ≈ 세마포어 드레인 꼬리뿐). ④ run6 db_reach direct=75 의 1차
  기록 부재(health_report 덮임) — run7=78, run8 재현성으로 판정하고 함수 목록을
  실험 정답과 대조.
- [x] **run9 판정** (2026-07-23 00시경): 폭주 지형 확정 — 한줄요약 공백 폭주 재발 0
      (step6 288→64초, 평문 전환 유효 실증). 새 폭주 1건 = glossary_synthesis
      **내용 반복 루프**("일련번호"류 별칭 5개 조합 267회 반복, 3만 토큰·~4분) —
      presence_penalty 0.15 로도 미차단 실증(1회성 감점은 루프 내 순위 불변) →
      **페널티 0.0 원복** (산출물 미세 변동 examples 1780→1757 도 근거).
- [x] **룰별 max_tokens 상한 전수 명시** (framework 8 + dbms 7 + quality 1): 실측
      정상 최대(usage.jsonl)의 2~10배 — 정상 호출 영향 0, 루프 피해 4분 → 초 단위.
      프롬프트 해시 기준선 --update 로 갱신.
- [ ] run10 최종 실측 (페널티 0 + 상한 + 평문 전환 + 인덱스): 시간·품질·폭주 0 확인.
- [x] **원칙원문 전수 감사** (2026-07-23 /goal, diff 3,498줄 전량 재독): 색출·수리 7건 —
      ① one_liner yaml 의 삭제된 Pydantic 지시 절(§2 끊어진 참조) ② 고객 식별자
      주석 2곳 중립화(§8) ③ resynthesize_glossary 옛 계약 동기화+무라벨 조회 2건(§2 DRY)
      ④ 에이전트 경로 출력 상한 8000(run10 실측: 무상한 콜 1건 3만 토큰·207초)
      ⑤ embed docstring 낡은 "무라벨" 서술 ⑥ glossary UPSERT SET 절 정리
      ⑦ 용어사전 닫힌 선택형(코드+프롬프트+운영 스크립트+테스트 3건). 옛 심볼 grep 0
      (잔존은 핀 서브모듈 사본뿐), pyflakes 0, 전수 323 OK, 해시 기준선 갱신.
- [x] **run11 최종 검증 완료** (00:17~00:32, 총 15분38초 — 최고 기록): 416/416 ·
      R/W 103 · 폭주 1건이 상한 20k 에서 차단·부분 마크·무중단(방어 실물 작동) ·
      용어사전 닫힌 선택 33.9초·상호 병합 4→59건·입력 11.9M→10.8M 토큰.
      산출 문서: docs/핸드오프/2026-07-23-robo-data-analyzer-완전-핸드오프.md +
      동일 폴더 딥리서치-프롬프트.md.
