# Research: 041-summary-search-split (Phase 0)

전수조사(2026-07-09, 이 세션 코드 직접 정독)로 확정한 사실과 결정. NEEDS CLARIFICATION 0.

## 확정 사실 (코드 정독)

| # | 사실 | 근거 위치 |
|---|---|---|
| 1 | 분할1 호출: framework=`run_summary`/`analyze_code_three_split`(SummaryOutput), dbms=`_batch_call`(BatchSummaryOutput) — 결과는 `fn.summary/stereotype/logical_name` + `ctx.summary`(상향 전달)로 fan-in | three_split_llm_fanout.py:57-230 · batch_analyze.py:288-418 |
| 2 | 저장: `update_summaries` → `to_summary_item`(id·summary·stereotype·logical_name·code_text, 빈값 미저장) → `SET node += item` | shared/neo4j/nodes/code_node.py:56-83 |
| 3 | 프롬프트 합성: `extends` → common.system + common.user_template_inputs + rule.user_template_specific. **분할 고유 지침은 suffix라 KV캐시 prefix 무관** | shared/llm/yaml_llm_runner.py:89-109 |
| 4 | 해시 가드: `prompts/` 전 yaml SHA256 = `cli_scripts/prompt_hashes.json`, CI `byte_identical.yml`. 프롬프트 수정 시 `--update` 필수 | cli_scripts/check_byte_identical.py |
| 5 | `[핵심]` 마커 강제 validator: `SummaryOutput._validate_summary_format` (BatchSummaryItem은 validator 없음 — 프롬프트만) | data_models/code_analysis_output.py:49-55 |
| 6 | 프론트: NodeDetailBase가 summary를 첫 문단 분리(마커 파싱 X). **FallbackCard가 노드 속성 일반 나열**(HIDDEN_KEYS={labels,summary} 제외 전부) — DBMS 구문노드(IF/LOOP/COMMIT 등 = 분석 대상)가 여기로 렌더 | FallbackCard.vue:17-27 · NodeDetailContent.vue:36-44 |
| 7 | 놀이터 임베딩 재료: `fetch_corpus` Cypher(summary·code_text·stereotype·…) + `_compose_embed_text`(EMBED_TEXT_MODE=template\|summary\|core, template 모드가 `코드:...[:180]` 포함) — 캐시 파일명 모드별 자동 분리 | module_test/임베딩서칭/engine/_common.py:31-142 |
| 8 | 실측 전제: summary-only 임베딩=정답 유실, core(코드 제외)=SKT 결제망 2개 유실 → 코드180자가 현행 변별 목발(재설계 동기) | eval/measure_log.md Session 14 |

## 결정 (Decision / Rationale / Alternatives)

### D1. 필드명 = `search_text`
- **Decision**: Neo4j 속성·dataclass·Pydantic 필드 모두 `search_text`.
- **Rationale**: 용도(검색 전용 텍스트)가 이름에 직진. summary/logical_name과 혼동 없음.
- **Alternatives**: `business_desc`(검색 용도 불명확), `embed_text`(임베딩만이 아니라 CE·용어집도 소비) — 기각.

### D2. 생성 위치 = 분할1 출력 필드 추가 (호출 +0)
- **Decision**: SummaryOutput·BatchSummaryItem에 `search_text: str = ""` 추가, 분할1 yaml `user_template_specific`에 작성 지침 `[4]` 신설.
- **Rationale**: 변별 재료(분기·코드값·테이블)는 LLM이 요약 쓰며 이미 파악한 정보 — 같은 호출에서 뽑는 게 비용 0·품질 정합. 공통 system 불변 = KV캐시 안전(사실 #3).
- **Alternatives**: 별도 4번째 분할 호출(비용·캐시 부담) / 후처리 단계 신설(입력 컨텍스트 중복 조립) — 기각.

### D3. 프론트 비노출 = FallbackCard HIDDEN_KEYS 1줄
- **Decision**: `HIDDEN_KEYS`에 `'search_text'` 추가. 다른 카드(FunctionCard 등)는 고정 필드 렌더라 무영향(전수 grep: 노드 속성 일반 나열은 FallbackCard 유일, NodeDetailPanel의 Object.entries는 관계 속성이라 무관).
- **Rationale**: FR-003(비노출)을 지키는 최소 결정론 변경. "프론트 수정 미룸"은 요약 표시 재설계를 미룬 것 — 노출 차단은 이 스펙의 필수 요구.
- **Alternatives**: 백엔드 조회 API에서 속성 제외(그래프 조회 경로 다수·과잉) / 저장 안 하고 별도 저장소(구조 복잡) — 기각.

### D4. 놀이터 ablation 모드 = `EMBED_TEXT_MODE="searchfield"`
- **Decision**: `fetch_corpus` Cypher에 `coalesce(n.search_text,'')` 추가 + `_compose_embed_text`에 searchfield 모드: 종류·역할·이름·테이블·요약(template와 동일) + `검색:{search_text}` — **코드 조각 완전 제외**. search_text 빈 노드는 해당 파트만 생략(코드 폴백 없음 — 순수 비교).
- **Rationale**: 비교 대상이 정확히 [코드180 목발] vs [검색 서술]. 폴백 섞으면 ablation 오염. 기본값 template 유지 = 편애0(채택 전 기본 off), 캐시 파일명 자동 분리(사실 #7).
- **Alternatives**: template에 검색서술 추가(코드+검색 중복 — 무엇이 효과인지 판별 불가) — 기각.

### D5. 위험질의 세트 = 측정 장부 실측 근거 선정 (전량 측정 금지 — 사용자 지시)
- **Decision**: ⓐ 코드 제외 시 정답 유실이 실측된 질의(SKT 결제망 계열 — release_stock·d700_check_skvrq_bank 유실 사례) ⓑ 과거 실패→구제 이력 질의(결제망·문자) ⓒ 민감 도메인(정산·주문취소) 골드 질의. Phase B에서 cluster_gold.yaml·rewrite_gold.yaml·measure_log 대조로 id 목록 확정(5~8개), `cluster_eval.py --ids`로 측정.
- **Rationale**: 가장 무너지기 쉬운 질의에 검증력 집중(시간 대비 최대) — 사용자 지시 2026-07-09.

### D6. 파일럿 = SKT(test DB, 38노드) 선행 전량 재분석
- **Decision**: "소수 대표노드"의 구현 = 작은 그래프(SKT 38노드)를 먼저 실제 파이프라인으로 재분석 → 산출 원문 대조 + SKT 위험질의 재검색 → 통과 후 쇼핑몰(124노드) 재분석.
- **Rationale**: 별도 probe로 입력 컨텍스트(모듈 intent·callee 요약·테이블 설명)를 반쪽 재현하면 검증이 거짓이 됨 — 실제 실행으로 검증(절대원칙 §6). 38노드는 비용·시간 미미, 실패해도 재실행으로 복원.
- **Alternatives**: 단독 LLM probe 스크립트(컨텍스트 반쪽 재현 = 검증 왜곡) / 쇼핑몰부터(더 큼) — 기각.

### D7. `[핵심]` 마커 = Phase C에서 제거, validator = 결정론 정규화 (개정 2026-07-09)
- **Decision**: 다이어트 시 마커 제거. validator 는 **거부가 아니라 정규화** `_normalize_diet_summary`: 마커 등장 시 벗겨내고, 비어있지 않은 줄 3줄 초과분은 절단 — 항상 성공. SummaryOutput·BatchSummaryItem 동일 적용.
- **Rationale(개정 사유 — 실측)**: 최초 설계(거부→LLM 재시도 유도)는 허구였음 — `run_yaml_rule` 은 `ainvoke` 1회에 재시도가 없어 거부=노드 분석 통째 소실(다이어트 첫 런에서 쇼핑몰 5·SKT 2건 요약 유실, release_stock 골드 포함). LLM 확률 출력에 하드 게이트는 데이터 손실 폭탄 → 편차를 결정론으로 흡수하고 마지노선(3줄)은 기계가 100% 보장. 절단으로 잃는 세부는 search_text 가 보존(역할 분리 설계).
- **Alternatives**: 재시도 로직 추가(비용↑·잔존 실패 확률·복잡도) / 4줄 완화(사용자 지시 위반) / 마커 유지(데드 규약) — 기각.

### D8. 상향 전달 재료 = 다이어트 summary 그대로 (Phase C 검증 항목)
- **Decision**: `ctx.summary`(callee_summaries·compact_body 재료)는 계속 summary 사용. search_text 병용은 하지 않음.
- **Rationale**: 다이어트 요약(1~3줄)은 현행 "[핵심] 한 줄"보다 오히려 정보량 동등 이상 — 기존에도 부모는 자식 [핵심] 위주로 소비(프롬프트 지침 "자식 끌어올림 X"). search_text 병용은 부모 입력을 부풀려 프롬프트가 금지한 "자식 세부 끌어올림"을 유발. 단 Phase C에서 부모 산출 표본 대조로 실측 검증(FR-010) — 열화 관측 시 이 결정 재고.

### D9. 재분석 실행 전제
- **Decision**: 놀이터 두 그래프의 원 데이터셋·기동 레시피는 구현 단계에서 메모리(manual.md·shopmall-demo-testdata.md) 정독 후 실행. LLM=기존 analyzer 설정(공유 .env), 예산 회로차단기 기본값 유지.
