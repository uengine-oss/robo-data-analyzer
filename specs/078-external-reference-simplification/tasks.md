# Tasks 078 — 실행 체크리스트

체크는 실행 증거(명령+결과)가 있을 때만. 완료 증거는 `verification-evidence.md`에 기록.

## S1 — Gold/adversarial corpus

- [x] T101 fixture 디렉터리 신설(`tests/pipeline/step3_load_external/gold/`):
      XML(중첩 key·CDATA·namespace·자체닫힘·attr `>`)·YAML(반복 leaf·anchor/alias·
      multi-doc·CRLF)·JSON(escape·duplicate key)·JSONL·MD(fence 내 빈 줄·heading 계층)·
      properties(연속 행)·INI·CSV(quoted newline)·CP949 인코딩·malformed 각 형식·decoy
      — 기대 span/keys를 gold JSON으로 병기
- [x] T102 반례 4종(재현 스크립트와 동일 입력)을 gold에 포함 — mybatis_like(XML outer)·
      repeated_leaf(YAML)·doc.md(fence)·big_sql(1200 초과 단일 청크, 생성 스크립트)
- [x] T103 링킹 gold: `link_external/gold/linking_cases.json` 8케이스
      (occurrence 계약·duplicate·dynamic·case·주석·부분 문자열·1단계 상수·다중 key)
- [x] T104 채점 하네스 `gold/gold_loader.py` + `test_gold_integrity.py` 2/2 PASS.
      현재 구현 red 증거는 `research.md` §2 (repro_defects.py 실행 결과)
- [x] T105 held-out 선언: gold/README.md (train=gold+loan-ejb-app,
      held-out=ukesa·ofbiz — S7 전 관찰 금지)

## S2 — Span 어댑터·verifier (추가만)

- [x] T201 span 어댑터 신설: `parsers/span_common.py`(SemanticSpan·key 규칙·EOL 확장),
      `span_xml.py`(expat, 최외곽 key 요소+자식 name 흡수, benign DOCTYPE 허용/ENTITY
      선언·외부 entity 거부), `span_config.py`(YAML mark·JSON/JSONL source-map·
      properties 연속행·INI section·CSV quote 스캐너), `span_markdown.py`(heading 분할)
- [x] T202 `span_verifier.py`: bounds·overlap·whitespace-only·slice·hash, fail-closed
- [x] T203 requirements.txt에 json-source-map>=1.0.5 추가(신규 의존성 유일) + venv 설치
- [x] T204 `test_span_adapters_gold.py` — gold 18 fixture 전수 정확 일치 + verifier
      거부 경로. 추가 실물 검증: loan-ejb-app XML 7파일 → 23청크 79키 slice-exact
      (ejb-jar.xml 21청크 — DOCTYPE 정책 정밀화로 확보)
- [x] T205 전체 suite 276 tests OK(5 skip) — 기존 경로 무접촉 증명

## S3 — 계약 컷오버 (한 슬라이스)

- [x] T301 schema: ExternalChunk 재정의(EXTERNAL_CHUNK 단일 라벨·keys·char span·
      provenance·source_hash), ReferTo(matched_key/via_symbol), 구 dataclass 5종·관계
      상수 5종·제약·unresolved/candidate id 삭제, `derived_chunk_id` 신설
- [x] T302 step3: 어댑터 라우팅 표+verifier 게이트+`chunk_builder.py`, UNRESOLVED 생성
      제거, document_adapters 산출 `derived_document` 재분류, chunking.py 삭제,
      `.toml`→`no_span_adapter` unanalyzed
- [x] T303 writers: external.py(PROJECT_FILE/EXTERNAL_CHUNK만, tuple→list 직렬화),
      external_links.py(REFER_TO/HAS_CHUNK만)
- [x] T304 stream/state/artifact: 구 타입 제거, ReferTo wire props 갱신
- [x] T305 step5: linker REFER_TO 전용+유일성 규칙+derived 제외,
      document_mentions.py·sql_tables.py·answer_key_parity 삭제
- [x] T306 step6: outgoing만+통째-or-skip(절단 필드 폐기)+hash 재검증+skip 관측 로그
- [x] T307 step4: span 계약(ExternalRuleMatch.start/end)+held-out 게이트(단일 파일
      승격 금지)+builtin negative decoys, 합의는 후보 선별로 강등
- [x] T308 step8: `_build_document_candidates`·CandidateLink 제거
- [x] T309 테스트 전면 전환(6개 폐기 테스트 삭제+5개 재작성) + 링킹 gold 8케이스 green
      (FP 0: duplicate/dynamic/case/comment/substring 전부 무연결 검증)
- [x] T310 게이트 통과: 전체 suite **263 tests OK(5 skip)** (AST 10/10 포함),
      옛 심볼 grep 0(constraints.py legacy 데이터 목록만 문서화된 예외),
      `.specify/feature.json` → 078, README Step3 갱신
- [x] T311 사용자 WIP 보존 확인: Frontend 6건 동일, ANTLR 55건 비접촉

## S4 — Prompt 계약

- [x] T401 `_common.yaml` external_evidence 서술을 078 스키마로 갱신(S3에서 선행) —
      matched_key·원문 슬라이스·호출 단정 금지 문구
- [x] T402 `test_external_prompt_contract.py` 4 tests: evidence는 user template에만,
      system 정적·byte-identical·placeholder 0, 075 어휘 부재, evidence가 user만 변화.
      (실 LLM prompt record 검증은 T704에서 수행)
- [x] T403 전체 suite green에 포함 확인 (263+4)

## S5 — Frontend

- [ ] T501 WIP 6건 해시 기록 후 비접촉 확인
- [ ] T502 graphVisibility: EXTERNAL_CHUNK/외부 endpoint REFER_TO 기본 숨김
- [ ] T503 카드가 `text`·keys·provenance 표시(구 라벨 라우팅 정리)
- [ ] T504 external-graph-contract.mjs 갱신 + build + 1920×1080 실화면/콘솔/네트워크 캡처

## S6 — 전수 회귀

- [x] T601 Analyzer 전체 unittest 267 tests OK(5 skip) — S3 263 + S4 4
- [x] T602 Catalog 전체 41 OK. `REFER_TO`는 `schema_queries.py`의 TABLE-target 쿼리만
      (코드 무변경)
- [x] T603 Fabric 전체 32 OK + 외부 계약 심볼 grep 0
- [x] T604 Frontend 계약 mjs PASS·구 라벨 grep은 부재검증 항목만. WIP 보존 확인
      (빌드 오류 7건은 전부 비접촉 파일 — package.json WIP 의존성 기인, 수정 금지 기록)

## S7 — 실기동 E2E

- [x] T701 Neo4j(Desktop2 DBMS, zulu21) 기동 + `skill` DB. ANTLR 파서는 workspace
      러너로 기동해 업로드 모드 재파싱(AST 70 exact). 분석은
      `ROBO_NEO4J_DATABASE=skill` + **로컬 GPU LLM(qwen36_sglang_local)** 로 실행
      (residual 한도 40 상향으로 자력 통과 — cloud 미사용, 사용자 지시 준수)
- [x] T702 loan-ejb-app 전체 파이프라인 완주(1,823 LLM calls, 13.78M in tokens).
      EXTERNAL_CHUNK 33개 전수 `text==source[start:end]`+hash 일치 **0 불일치**
- [x] T703 REFER_TO 3건(LoanServlet 3메서드→ejb-jar.xml entity 청크, matched_key
      approverEmployeeId/loanType/interestAmount) — 전부 실제 리터럴 occurrence(FP 0).
      미연결 청크 31개 정상 유지, 폐기 라벨/관계 grep 0
- [x] T704 prompt record(code_summary.json 784건): 연결 원문 포함 실패 0·절단 0·
      타 routine leak 0·system prompt 완전 동일(1종)
- [x] T705 held-out(ukesa+ofbiz, 미관측): XML/MD 7파일 325청크(iBatis sqlmap 120 포함)
      slice 위반 0·fail-closed 0
- [x] T706 Frontend 실화면 1920×1080: 기본 숨김(외부 노드 부재, 1023노드) →
      "내부 요소까지 펼치기" 후 2461노드, Overview에 EXTERNAL_CHUNK(33)/HAS_CHUNK(33)/
      REFER_TO(3) 표시·구 라벨 부재, 메서드 카드에 분석요약+원문 리터럴+연결 표시,
      콘솔 오류 0. (청크 자체 NonCodeCard의 캔버스 클릭 오픈은 좌표 한계로 **미검증** —
      계약 테스트로만 검증)
- [x] T707 전체 wipe 미사용 — run 시작 시 owner-scoped wipe(설계 lifecycle)만 사용,
      사전 스냅샷 기록(owner=analyzer 284노드→신규 그래프로 교체)

## S8 — 보고·Git

- [ ] T801 `docs/작업보고내용/2026-07-22-외부-파일-의미청크-직접참조-단순화-작업보고.html`
      (spec 17절 필수 항목 전부, 미검증 구분)
- [ ] T802 Analyzer scoped commit/push (WIP 제외)
- [ ] T803 Frontend scoped commit/push (WIP 6건 제외)
- [ ] T804 root 보고서 commit (사용자 문서 2건 제외)
- [ ] T805 핸드오프 문서 최종 갱신
