# 078 Research — 외부 파일 의미 청크·직접 참조(REFER_TO) 아키텍처 선정

작성일: 2026-07-22. 이 문서는 `research/deep-research-raw.md`(외부 Deep Research 복원본)의
claim을 이 저장소·이 환경에서 직접 재검증한 결과와, 그에 따른 최종 기술 선정을 기록한다.
075 계약을 supersede하는 078의 유일한 기술 근거 문서다.

## 1. 검증 방법

- 실행 환경: Windows 11, Python 3.12.10 (analyzer `.venv`), spike 전용 격리 venv
  (`scratchpad\spikevenv`, analyzer venv 비오염).
- 재현 스크립트: `scratchpad\repro_defects.py`(현재 코드 결함 재현),
  `scratchpad\spike_bytespan.py`(후보 스택 span 정확성 실측). 실행 로그는 본문에 인용.
- 실물 코퍼스: `D:\work\robo\분석대상모음\` 9개 프로젝트 (2026-07-22 실측).

## 2. 현재 구현 결함 — 실행으로 재확인 (2026-07-22)

`repro_defects.py`를 현재 코드에 실행한 결과. 전부 딥리서치 진단과 일치한다.

| # | 결함 | 실측 출력 | 원인 코드 |
|---|---|---|---|
| 1 | XML outer element 손실 | `<select id="getOrderDetail">`의 text가 inner content만 반환, `outer tag preserved? False` | `xml_universal.py::_element_text()` — 요소 span 대신 내부 텍스트 조립 |
| 2 | YAML 반복 leaf 위치 오류 | `first.enabled`/`second.enabled` 모두 `line=2` | `config_parser.py::_line_for_key()` — leaf 이름 첫 occurrence 검색 |
| 2b | 재직렬화 값 저장 | 원문 `true`가 `'True'`(Python bool str)로 저장 | `config_parser.py::_flatten()` — `str(value)` |
| 3 | Markdown fence 절단 | 빈 줄 포함 fence가 2개 DOC_CHUNK로 분리 (` ```sql\nSELECT * ` / ` FROM orders\n``` `) | `markdown_doc.py` — 빈 줄에서 무조건 `flush()` |
| 4 | fixed 1200자 분할 | 1,800자 입력 → `[1200, 600]` 2 parts, 문장 중간 절단 | `chunking.py::MAX_CHUNK_CHARS=1200`, `split_text()` |

기존 테스트 baseline 재실행(회귀 기준선): Step3 external 23 PASS·1 SKIP, Step4 9 PASS,
Step5 external 18 PASS, Step2 AST 10 PASS. **이 PASS는 옛 계약의 회귀 없음일 뿐 새 목표의
증거가 아니다.**

## 3. 딥리서치 rubric 판정 (핸드오프 12절 8항목)

| 항목 | 판정 | 근거 |
|---|---|---|
| 1. 공식 출처 기반인가 | 대체로 PASS | 핵심 주장(expat/PyYAML/json-source-map 능력, CommonMark 펜스 규칙, defusedxml)은 본 spike로 직접 재현됨. 단 arXiv:2606.07528(BEACON)은 **번호 형식이 비정상(2606=2026년 6월)이며 미검증** — 인용하지 않는다. ChainPoll(2310.18344)은 실존. |
| 2. 버전/유지보수 확인 | PASS(핵심), 부분 미검증(주변) | 설치 실측: ruamel.yaml 0.19.1, json-source-map 1.0.5, tomlkit 0.15.1, defusedxml 0.7.1, markdown-it-py 4.2.0 — 전부 Windows/Py3.12 휠 설치 성공. rhwp-python은 이번 범위 밖이라 미검증으로 남긴다. |
| 3. source span 실제 제공 | **PASS — 직접 실측** | 아래 §4. 문서의 tree-sitter 중심 권고와 달리 순수 Python으로 충분함을 실측. |
| 4. format family 구분 | PASS | 텍스트(byte/char-span) vs 바이너리(page/bbox derived) 이원화는 그대로 채택. |
| 5. held-out/adversarial 평가 | PASS(설계 채택) | verifier·held-out 게이트 설계를 Phase 1 gold corpus 계획으로 채택. |
| 6. Agent common-mode error | PASS | 다수결 승인 금지 + 결정론 verifier 승인 원칙 채택. AUROC 수치 자체는 재검증하지 않음(설계 결론에 영향 없음). |
| 7. Windows/Python 운영 적합 | PASS — 직접 실측 | 후보 전부 순수 Python/휠. GPU·네이티브 의존 없음(이번 범위). |
| 8. robo corpus 재현 가능 | PASS — 직접 실측 | loan-ejb-app 실물 XML 3파일 515개 요소 span 일관성 실측 통과. |

## 4. Spike 실측 결과 — 순수 Python 스택 20/20 PASS

`spike_bytespan.py` (spikevenv, 2026-07-22):

```
[XML] stdlib expat byte spans
  PASS select element slice-exact / outer tag preserved / closing tag preserved
  PASS attr '>' quote-aware        (<x a="1&gt;2" b='>'>)
  PASS self-closing element        (시작 태그가 '/>'로 끝나는지로 판별)
  PASS CDATA preserved verbatim    (<![CDATA[a > b\n\nc]]>)
  PASS ejb-jar.xml: 398 elements span-consistent bad=0
  PASS web.xml: 52 elements / pom.xml: 65 elements — bad=0
[YAML] PyYAML compose marks (char offsets)
  PASS first/second distinct spans (9,22) vs (33,47)
  PASS 슬라이스 정확: "enabled: true" / "enabled: false"
  PASS 값 재직렬화 없음(true 그대로) / 서브트리 슬라이스 / CRLF 정확
[JSON] json-source-map — key/value/서브트리 position 슬라이스 정확
[MD] markdown-it-py token.map + line→offset — 빈 줄 포함 fence 무절단, CRLF 정확
TOTAL 20 checks, 0 FAIL
```

핵심 기술 사실:

1. **XML — stdlib `xml.parsers.expat`가 byte offset을 제공한다** (`CurrentByteIndex`).
   요소 종료는 닫는 태그 시점 index에서 따옴표 인식 스캔으로 `>`를 찾아 계산. 자체닫힘
   요소는 EndElement 시점 index가 태그 뒤를 가리키므로 **시작 태그가 `/>`로 끝나는지로
   판별**해야 한다(spike에서 실측·해결). lxml은 sourceline만 있고 byte offset이 없어 부적합
   (딥리서치 주장과 일치). 보안은 파싱 전 defusedxml 계열 사전검사(DTD/entity 거부)를
   유지한다 — expat 자체는 entity 확장 제한을 위해 기존 안전 봉투(DTD 거부)와 결합.
2. **YAML — PyYAML `compose()`의 Mark.index가 문자 offset을 제공한다.** ruamel.yaml
   없이도 노드별 정확 span 확보. anchor/alias/multi-doc은 gold corpus에서 별도 검증 필요
   (Phase 1). ruamel은 주석 보존이 필요해질 때의 예비 후보로만 남긴다.
3. **JSON — json-source-map(MIT/Apache 이중 표기, 순수 Python)이 JSON pointer별
   key/value position을 제공한다.** duplicate key는 최후 항목만 남는 한계 → duplicate 발생
   시 fail-closed 정책으로 처리(Phase 2 계약).
4. **Markdown — markdown-it-py(이미 analyzer 의존성, MIT) token.map + 라인→offset
   매핑으로 CommonMark 규칙대로 fence를 무절단 추출.** CRLF 실측 통과.
5. **Properties/INI/CSV — 위치 제공 라이브러리 부재(javaproperties는 값만).** 규격 기반
   logical-record 스캐너 자체 구현(결정론, 딥리서치 권고와 동일).

## 5. 최종 선정 — 딥리서치 권고에 대한 채택/수정/기각

### 채택 (딥리서치와 동일)

- parser-first, byte/char-span 청크, `chunk.text == original_source[start:end]` 불변식
- provenance 이원화: `source_span`(텍스트) / `derived_document`(바이너리 문서)
- derived 청크는 직접 `REFER_TO` 대상에서 기본 제외
- Agent는 span/규칙 제안만, 결정론 verifier(round-trip + no overlap/gap + held-out) 승인
- REFER_TO는 whole-token·case-sensitive exact match + 1단계 불변 상수 전파만.
  occurrence 이상의 의미(호출/소비) 주장 금지. duplicate/ambiguous/dynamic 무연결
- 고정 1200자 분할·flatten 문자열화·부분 문자열/유사도/embedding 매칭 기각(동일)
- MarkItDown 등 문서 변환 후보 판정(이번 범위 밖이지만 기록 유지)

### 수정 (실측 근거로 딥리서치 권고를 변경)

- **tree-sitter 기각(이번 범위).** 딥리서치는 tree-sitter를 범용 1위로 권고했으나,
  대상 형식(XML/YAML/JSON/MD/properties/INI/CSV)은 순수 Python 스택이 spike 20/20으로
  충분함을 실측했다. tree-sitter는 C 확장이라 문서화된 OOM/무한 recovery 사례 때문에
  subprocess 격리가 필요해지는데, 순수 Python 채택으로 **이번 작업의 신규 subprocess는
  0개**가 된다(사용자 확정 방향). 향후 순수 Python이 gold를 통과하지 못하는 형식이
  실측되면 그 형식에 한해 재검토한다.
- **좌표계: 디코딩된 텍스트의 문자 offset을 정본으로 저장한다.** 원문 파일은
  `encoding_reader`가 디코딩하므로(CP949 등), 불변식은 `chunk.text ==
  decoded_source[char_start:char_end]`로 정의하고 디코딩 결과의 hash를 함께 저장한다.
  expat의 byte offset은 UTF-8 재인코딩 기준이므로 어댑터 내부에서 문자 offset으로 변환한다.
  (원본 바이트 좌표가 필요해지는 요구가 생기면 encoding 메타로 재계산 가능.)

### 기각 (딥리서치 제안 중 이 저장소 원칙과 충돌)

- **Stage 0의 feature flag 병행 롤백** — 원칙(임시 플래그·이중 경로 금지)과 충돌.
  롤백은 git 커밋 단위로 한다. 생산자·소비자를 같은 슬라이스에서 전환하고 옛 경로 grep 0.
- **BEACON(arXiv:2606.07528) 인용** — 실존 미확인(비정상 번호). 결론(다수결 승인 금지)은
  ChainPoll 수치와 verifier 설계만으로 충분히 지지되므로 유지하되 이 출처는 쓰지 않는다.

### 이번 범위에서 보류 (미검증으로 명시)

- PDF/Office/HWP 어댑터(Docling/PyMuPDF4LLM/MinerU/rhwp-python): 실물 코퍼스에 해당
  파일이 0건이라 gold를 만들 수 없다. 기존 `document_adapters.py` 경로는 유지하되 산출
  청크를 `derived_document` provenance로 정직하게 재분류만 한다. 후보 평가는 향후
  실물 문서 확보 시 수행.
- tree-sitter·ruamel.yaml·tomlkit: 예비 후보로 기록만. 설치 가능성은 실측 완료.

## 6. 형식별 최종 어댑터 선정

| 형식 | 어댑터 | 라이선스(실측) | span 방식 |
|---|---|---|---|
| `.xml` | stdlib expat + 따옴표 인식 태그 종단 스캔 (자체) | PSF (stdlib) | byte→char 변환 |
| `.yaml` `.yml` | PyYAML `compose()` Mark.index (이미 의존성) | MIT | char |
| `.json` `.jsonl` | json-source-map 1.0.5 (신규 의존성) | MIT(LICENSE)/Apache(classifier) — 허용적 | char |
| `.md` `.markdown` | markdown-it-py 4.2.0 (이미 의존성) + 라인→offset 매핑 | MIT | char |
| `.properties` `.env` `.ini` `.cfg` `.conf` | 규격 기반 logical-record 스캐너 (자체 구현) | — | char |
| `.csv` `.tsv` | 논리 레코드 스캐너 (quoted newline 인식, 자체) | — | char |
| `.toml` | stdlib tomllib(값) + 자체 라인 스캐너 또는 보류 — Phase 1 gold에서 결정 | PSF | char |
| PDF/Office/HWP/이미지 | 기존 subprocess 경로 유지, `derived_document`로 재분류 | (기존) | page/anchor (비-span) |
| unknown text | Agent 제안 + 결정론 verifier (기존 skill 구조 재사용, 승인 게이트 강화) | — | char |
| binary(기타) | 청크 없음 — 파일 노드만, `unsupported` 분류 | — | — |

의미 단위 정의(요약): XML=키(id 등) 보유 요소 전체(여는~닫는 태그), YAML/JSON=키 경로가
있는 서브트리 전체, MD=heading 구간·fence 블록 전체, properties/INI=논리 레코드(연속행
포함), CSV=헤더+데이터 행. 크기 제한은 의미 계층에서 제거하고, LLM budget은 소비 계층
(context 조립)에서 온전한 청크 단위 선택으로 처리한다.

## 7. 실물 코퍼스 분포 (2026-07-22 실측)

| 프로젝트 | 파일 | 외부 파일 관점 | 역할 |
|---|---|---|---|
| loan-ejb-app | Java 58, XML 8, MD 1 | ejb-jar.xml(22KB)·web.xml·pom.xml 등 | train + E2E 1순위 |
| ofbiz-order-standalone | Java 29, XML 4, MD 1 | | held-out 후보 |
| ukesa | Java 7, XML 2 | | held-out |
| library_system | Py 17, conf 1, MD 1, log 1, db 1 | config/문서/binary 분류 검증 | train |
| swing_system | C 11, h 7, conf 1, dat 4, exe 1, log 1 | unknown/binary 정직 분류 검증 | train |
| AMS / rwis | SQL 2 / SQL 12 | "SQL 가정 금지" 검증 | train |
| shopmall / apascrt | C 중심 | C literal 매칭 | 보조 |

공백: PDF/HWP/Office/YAML/JSON/TOML 실물 0건 → YAML/JSON/TOML은 fixture로 gold 구성,
PDF/HWP/Office는 §5대로 보류. 이 사실을 spec의 지원 범위 선언에 반영한다.

## 8. 결론 — 1위 아키텍처

```
inventory (기존 보존)
  → Format Router (확장자 기반, 기존 inventory 분류 재사용)
  → 형식별 span 어댑터 (§6, 전부 in-process 순수 Python)
  → SemanticChunk(char span, hash, keys, provenance=source_span)
  → 결정론 verifier: chunk.text == decoded_source[start:end] + no overlap + hash
  → PROJECT_FILE ─HAS_CHUNK→ chunk
코드측: METHOD/FUNCTION code_text의 주석 밖 리터럴 + 1단계 불변 상수
  → whole-token exact == chunk key (유일 매칭만)
  → METHOD/FUNCTION ─REFER_TO→ chunk
미연결 청크 = 정상 데이터. CALLS_SQL/USES_CONFIG/MENTIONS/UNRESOLVED_REF/
CANDIDATE_LINK/역FQN/외부 SQL→TABLE 파생 = 전부 제거.
```

이 선정은 스파이크 실측(20/20)·라이선스 실측·코퍼스 실측에 근거하며, 딥리서치 결과에
의존하되 종속되지 않는다.
