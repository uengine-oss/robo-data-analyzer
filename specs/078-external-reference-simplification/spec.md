# Spec 078 — 외부 파일 의미 청크·직접 참조 단순화 (supersedes 075)

작성일: 2026-07-22. 이 spec은 `075-external-file-linking`의 외부 그래프 계약을 **대체**한다.
075 문서는 역사 기록으로 보존하며 수정하지 않는다. 기술 근거는 `research.md`.

## 1. 목적

외부 파일(주 언어 AST가 코드로 해석하지 않는 프로젝트 내 모든 비코드 파일)을
**완전한 의미 단위**로 나누고, **정확한 직접 증거가 있을 때만** 코드와 연결한다.

최종 그래프 계약(전부):

```text
PROJECT_FILE ─HAS_CHUNK→ EXTERNAL_CHUNK     (형식 문법상 온전한 의미 단위)
METHOD/FUNCTION ─REFER_TO→ EXTERNAL_CHUNK   (whole-token exact 직접 증거가 유일할 때만)
```

## 2. 범위

### 포함
- Step3 청킹을 span 기반으로 교체 (XML/YAML/JSON/JSONL/MD/properties/env/INI/CSV/TSV)
- Step4 unknown-text Agent를 span 제안 + 결정론 verifier 승인으로 강화
- Step5 링킹을 `REFER_TO` 단일 관계·정확 증거로 축소
- 옛 관계·노드(`CALLS_SQL`, `USES_CONFIG`, `MENTIONS`, `HAS_PART`, `HAS_SECTION`,
  `UNRESOLVED_REF`, `CANDIDATE_LINK`, 외부 SQL→TABLE `READS/WRITES` 파생, chunk→Module
  역방향 FQN `REFER_TO`) 생산·소비 전부 제거
- Step6 context/Step7 prompt가 outgoing `REFER_TO`만 소비, 청크 무절단 전달
- Frontend가 새 계약(라벨·`text`·provenance)을 표시
- 실물 E2E (Neo4j `skill` DB 격리 owner)

### 제외 (명시적 미지원/보류)
- PDF/Office/HWP/이미지의 새 어댑터 도입 — 기존 변환 경로 유지, 산출물을
  `derived_document` provenance로 재분류만 한다 (실물 코퍼스 0건, research.md §5)
- SQL 내용 해석, TABLE 파생 관계, METHOD→TABLE — 이 목표에서 제외
- 외부 `PROJECT_FILE`의 AST Package 편입 — 별도 기능
- AST/ANTLR 계약 변경 — call argument span은 현재 계약에 없으므로 REFER_TO는
  occurrence 의미까지만 주장
- tree-sitter 등 네이티브 파서 도입 — research.md §5 수정 결정

## 3. 데이터 모델

### 3.1 EXTERNAL_CHUNK (단일 라벨)

기존 `SQL_STATEMENT`/`CONFIG_SECTION`/`ARTIFACT_CHUNK`/`CHUNK`/`DOCUMENT`/
`DOC_SECTION`/`DOC_CHUNK` 라벨과 anchor/part 분할 모델을 폐기하고 청크 1개 = 노드 1개.

| 필드 | 의미 |
|---|---|
| `id` | `external_chunk_id(file_id, char_start, char_end)` — 파일 내 span으로 유일 |
| `name` | 표시명 (첫 key 또는 형식별 제목) |
| `keys` | 이 청크가 노출하는 exact reference key 목록 (0개 이상, 정렬·중복 제거) |
| `semantic_kind` | 문법 종류: `xml_element`·`yaml_entry`·`json_entry`·`md_section`·`record`·`row`·`agent_segment`·`derived_document` — 의미 해석이 아니라 형식 사실 |
| `text` | 원문 슬라이스. **불변식: `text == decoded_source[char_start:char_end]`** |
| `file_path` | 프로젝트 상대 경로 |
| `char_start`/`char_end` | 디코딩된 원문의 문자 offset (derived는 둘 다 0) |
| `start_line`/`end_line` | 1-based 라인 (derived는 앵커 정보 없으면 0) |
| `provenance` | `source_span` \| `derived_document` |
| `source_hash` | 디코딩된 원문 전체의 SHA-256 (span 검증 좌표계 고정) |
| `content_hash` | `text`의 SHA-256 |

크기 상한 없음(1200자 규칙 폐기). 크기 관리는 소비 계층(§6)의 온전한-청크 단위 선택으로만.

### 3.2 PROJECT_FILE (유지 + status 정직성)

기존 필드 유지. `analyzed`/`unanalyzed`/`failed`/`registered` + `reason`이 파일 수준
실패·미지원의 유일한 표현이다 (UNRESOLVED_REF 노드 폐기).

### 3.3 REFER_TO (유일 연결 관계)

| 필드 | 의미 |
|---|---|
| `source_id` | METHOD/FUNCTION (routine) id — **방향은 코드→청크만** |
| `target_id` | EXTERNAL_CHUNK id |
| `matched_key` | 일치한 key 원문 |
| `evidence_file`/`evidence_line` | 코드 측 발견 위치 |
| `via_symbol` | 1단계 불변 상수 전파 시 심볼명 (직접 리터럴이면 빈값) |

## 4. 의미 단위 정의 (형식별)

| 형식 | 청크 = | keys |
|---|---|---|
| XML | key attribute(id/name 등)를 가진 **최외곽** 요소 전체(여는~닫는 태그). key 요소가 중첩되면 최외곽이 이기고 내부 key는 그 청크의 keys에 흡수 | `namespace.id`(있으면)와 bare `id` 모두 |
| YAML/JSON | 최상위 entry의 서브트리 전체(멀티 doc은 doc별) | 내부 모든 dotted 경로 (`first`, `first.enabled` …) |
| JSONL | 라인별 최상위 객체 | 라인 인덱스 접두 dotted 경로 |
| Markdown | heading 경계 사이 구간(파일을 heading으로 분할, fence 내부에서 절단 금지 — CommonMark 준수) | 없음 |
| properties/env | 논리 레코드(연속 행 포함) | property key |
| INI/cfg/conf | section 전체 | `section` 및 `section.key` |
| CSV/TSV | 데이터 행(quoted newline 인식) | 첫 컬럼이 식별자 형태일 때 그 값 |
| unknown text | Agent 제안 span (verifier 승인 시) | skill이 제안하고 verifier가 재확인한 key |
| 문서 변환(PDF 등) | 기존 변환 결과 세그먼트 (`derived_document`) | 없음 (직접 연결 대상 제외) |

**공통 불변식** (source_span 청크 전체):
1. `chunk.text == decoded_source[char_start:char_end]` — 위반은 즉시 실패(fail-closed)
2. 같은 파일의 청크 span은 서로 겹치지 않는다
3. `content_hash`/`source_hash` 일치
4. 파싱 실패 파일은 청크 0개 + `failed`/사유 — 억지 성공 금지

## 5. 직접 참조 규칙 (precision-first)

1. 증거 = routine `code_text`의 **주석 밖 문자열 리터럴**이 어떤 청크 key와
   whole-token·case-sensitive **완전 일치**.
2. 허용 확장 = 1단계 불변 상수/변수 전파(기존 `const_propagation` 규칙 유지).
3. **유일성**: 일치한 key를 노출하는 청크가 프로젝트 전체에서 정확히 1개일 때만 연결.
   중복 key(청크 2개 이상) → 연결 0 (정상).
4. dynamic concat 의심·부분 문자열·대소문자 변형 → 연결 0 (정상, 노드도 안 만듦).
5. `derived_document` 청크는 연결 대상에서 제외.
6. 연결 없는 청크는 PROJECT_FILE 아래 정상 데이터다. unresolved/error/candidate 노드로
   표현하지 않는다.
7. `REFER_TO`는 "이 routine 본문에 이 key 리터럴이 존재한다"(occurrence)까지만 의미한다.

## 6. Context·Prompt 계약

- Step6는 **outgoing `REFER_TO`만** 소비해 routine별 evidence를 조립한다.
- 청크는 **통째로 넣거나 아예 넣지 않는다** — 중간 절단(`truncated`) 폐기. budget에
  안 들어가는 청크는 skip하고 관측 로그에 남긴다.
- evidence에는 원문 `text`·`file_path`·라인·`matched_key`·provenance를 포함한다.
- 타 routine의 외부 원문 유출 금지.
- 공통 system prefix는 병렬 요청 간 byte-identical 유지. 외부 evidence는 user suffix에만.
- 검증은 unit test + **실제 LLM HumanMessage/prompt record**로 한다.

## 7. Agent(unknown text) 게이트

- Agent는 span 제안 또는 regex/state-machine skill 제안만. 청크 텍스트 생성 금지.
- 승인 = 결정론 verifier 전부 통과: (a) 각 span slice-exact, (b) no overlap,
  (c) **held-out 파일**(제안에 쓰지 않은 같은 형식 파일)에서도 불변식 유지,
  (d) negative sample(다른 형식 decoy)에서 매치 폭주 없음.
- LLM 다수결 합의는 승인 근거가 아니다(보조 신호로만).
- 실패 시 파일은 `unanalyzed`+사유로 정직하게 남긴다.

## 8. Acceptance Gates (전부 필수)

1. 지원 선언 형식의 boundary gold precision 100% (Phase 1 gold 기준)
2. source_span 청크 slice/hash equality 100%
3. `REFER_TO` false positive 0 (gold 채점, `logger.info` 반례 포함)
4. duplicate/ambiguous/dynamic 입력의 fabricated relation 0
5. 연결 청크의 실제 prompt 포함 100% + 중간 절단 0 + 타 routine leak 0
6. 옛 관계/노드의 active producer·consumer grep 0
   (`CALLS_SQL|USES_CONFIG|MENTIONS|HAS_PART|HAS_SECTION|UNRESOLVED_REF|CANDIDATE_LINK`)
7. AST Step2 10/10 회귀 0
8. Catalog TABLE-`REFER_TO`·Fabric 회귀 0
9. Frontend 기본 숨김/showHidden/카드 `text` 표시 실화면 검증
10. held-out 검증 없는 Agent skill 승격 0
11. silent drop/truncation/silent success 0
12. recall은 함께 보고하되 precision 우선 원칙 유지

## 9. 사용자 확정 제약 (재확인)

해피패스 금지 · 샘플 통과=완료 금지 · fixed-length=의미청크 금지 · Agent/LLM 의미 추론
금지 · 부분 문자열/유사도/embedding/이름 추정/고객 allowlist 금지 · SQL 가정 금지 ·
재직렬화=원문 금지 · WIP(Frontend 6건·Architect·ANTLR) 비접촉 · Neo4j 전체 wipe 금지 ·
새 subprocess 0 (기존 문서 변환 경로 제외) · feature flag 병행 금지(롤백=git).
