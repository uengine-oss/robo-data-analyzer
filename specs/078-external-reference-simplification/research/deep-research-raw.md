# 외부 파일 의미 청크 및 코드 직접 참조(REFER_TO) 통합 아키텍처 심층 연구 보고서

## TL;DR
- **단일 라이브러리로는 이 문제 전체를 해결할 수 없다.** 권고하는 최종 1위 아키텍처는 **"parser-first · 형식별 라우터 → 원문 byte-span 기반 의미 청크 → 결정론적 verifier가 승인한 exact-match만 REFER_TO"** 이며, LLM Agent는 알려지지 않은 포맷에 한해 **청크 텍스트를 생성하지 않는 span 제안자**로서 최후에만 투입한다.
- **청크는 반드시 원문 `original_source[start:end]`의 byte/char span으로 정의**하고, 파서가 재직렬화한 문자열을 원문이라 부르지 않는다. 구조화 텍스트는 CST/source-map 파서로 정확한 span을, PDF·Office·HWP 같은 바이너리 문서는 원문 byte-span이 존재하지 않으므로 **page/bounding-box라는 별도의 provenance 계층**으로 분리해 모델링한다. 두 계층은 같은 정확성을 의미하지 않는다.
- **직접 참조를 증명하는 최소 증거는 "코드 측 리터럴/식별자 site의 정확한 span 값"과 "외부 청크가 노출하는 정확한 key의 span 값"의 whole-token exact 일치**다. 현재 AST가 call arguments/literal sites를 제공하지 않으면 REFER_TO를 사실로 주장할 수 없고, 해당 청크는 오류가 아니라 **정상적인 미연결(UNVERIFIED) 데이터**로 남긴다. precision-first 원칙상 이러한 recall 손실은 의도적으로 수용한다.

---

## 1. 한 페이지 Executive Decision

이 시스템의 두 핵심 문제 — (1) 이질적 외부 파일을 의미가 깨지지 않는 완전 단위로 분할, (2) 코드 METHOD/FUNCTION이 특정 외부 청크를 직접 참조함을 추론 없이 고정밀 증명 — 는 성격이 전혀 다르며, 단일 도구·단일 모델로 동시에 풀 수 없다.

**결론적 판단:**

1. **기본 전략은 parser-first다.** document-model-first(Docling 등)는 바이너리 문서에만 정당하고, Agent-first는 검증 불가능성 때문에 기본이 될 수 없다. 구조화 텍스트(XML/JSON/YAML/TOML/Properties/INI/CSV/Markdown)는 형식별 CST 또는 source-map 파서로 처리해 각 의미 단위의 정확한 원문 byte span을 얻는다.

2. **구조화 텍스트와 바이너리 문서는 같은 chunk 모델로 처리하면 안 된다.** 텍스트 파일은 `chunk_text == source[start:end]` 및 hash 일치라는 강한 불변식을 만족할 수 있다. 반면 PDF/DOCX/XLSX/HWP는 원문 문자 스트림이 존재하지 않으므로 이 불변식이 애초에 정의되지 않는다. 이 계열은 **"어떤 페이지의 어떤 bounding box에서 유래했는가"** 라는 약한 provenance만 제공된다. 두 계층을 억지로 통합하면 반드시 "재직렬화 문자열을 원문이라 주장"하는 금지된 오류로 회귀한다.

3. **Agent가 최종 청크 텍스트를 생성해서는 안 된다.** Agent는 오직 원문 상의 (start,end) span 후보 또는 분할 skill(regex/state-machine)만 제안하고, 최종 청크 텍스트는 항상 원문에서 slicing한다. 그래야 source fidelity가 구조적으로 보장된다.

4. **Agent 다수결만으로 parser skill을 승인할 수 없다.** self-consistency/다수결은 "합의한 오답"과 "미관측 형식으로의 잘못된 일반화"를 걸러내지 못한다. 승인은 반드시 결정론적 verifier(round-trip 복원 + no overlap/gap + held-out 파일 통과)가 담당한다.

5. **직접 참조의 최소 증거는 exact, whole-token 일치**다. 부분 문자열·접두/접미사·대소문자 보정·embedding 유사도·LLM 판단은 사실 관계로 승격 금지. 현재 AST가 call argument 문맥과 literal site span을 제공하지 못하면, 증명 가능한 최대치는 "이 method 본문의 소스 범위 안에 이 key와 정확히 일치하는 리터럴이 존재한다"까지다. 그것을 실제 "소비(consumption)" 의미로 해석해서는 안 된다.

**최종 1위 아키텍처 (요약도):**

```
[File Inventory: root-confined, symlink 방지, binary/encoding 판정]
        │
        ▼
[Format Router] ──► 구조화 텍스트 ──► CST/source-map 파서 ──► byte-span 청크 ──┐
        │                                                                      │
        ├──► 바이너리 문서 ──► Docling(page/bbox provenance 계층) ──────────────┤
        │                                                                      │
        └──► unknown text ──► Agent(span/skill 제안) ──► 결정론 verifier ───────┤
                                            │(verify 실패)                      │
                                            ▼                                   ▼
                                     [UNVERIFIED로 보존]              [PROJECT_FILE-HAS_CHUNK→의미청크]
                                                                                │
[Code AST: literal/identifier site의 정확한 span] ──► exact-match verifier ──► [METHOD/FUNCTION-REFER_TO→청크]
```

---

## 2. 문제 정의와 용어

- **외부 파일**: 프로젝트 내부에 존재하지만 주 언어 AST가 코드 구조로 표현하지 않는 모든 비코드·보조 파일. (프로젝트 바깥 파일이라는 뜻이 아니다.)
- **의미 청크**: 형식의 문법상 "의미가 깨지지 않는 완전한 최소 단위". XML은 여는~닫는 태그를 포함한 요소 전체, YAML/JSON은 특정 key-value 서브트리 전체, Markdown fenced code block은 여는 펜스~닫는 펜스 전체 등. 고정 길이 1,200자 분할은 정의상 의미 청크가 아니다.
- **Source fidelity**: `chunk_text == original_source[start:end]` 이며 저장된 hash가 원문 slice의 hash와 일치. 파서가 재직렬화한 문자열은 이 정의를 만족하지 못하므로 원문으로 간주하지 않는다.
- **직접 참조(REFER_TO)**: 추론이 아니라, 코드의 리터럴/식별자 토큰이 외부 청크가 노출하는 key와 정확히(whole-token, case-sensitive) 일치함이 결정론적으로 검증된 관계. 관계 종류는 `REFER_TO` 하나로 통일하며 `CALLS_SQL`/`USES_CONFIG`/`MENTIONS` 같은 의미 추론을 하지 않는다.
- **미연결 청크(UNVERIFIED)**: 어떤 코드와도 exact 증거가 없는 외부 청크. 오류나 미해결 후보가 아니라 **정상 데이터**.
- **provenance 계층 이원화**: (a) 텍스트 계열의 정확한 byte-span provenance, (b) 바이너리 계열의 page/bbox provenance. 정확도 수준이 다르므로 데이터 모델에서 명시적으로 구분한다.

---

## 3. 현재 구현에 대한 냉정한 수준 판정

**판정: 안전장치(security envelope)는 우수하나, 청크 분할 코어와 참조 증명 로직은 근본적으로 결함이 있어 "충분하다"는 전제는 반증된다.**

기존 안전장치(root-confined inventory, symlink 탈출 방지, XML의 DTD/entity 확장 차단, 문서 파서 subprocess 격리·크기/페이지/시간 제한·취소 시 kill, 정규식 안전성 검증, 청크 hash/길이/part 순서 검증)는 프로덕션 수준으로 잘 설계되어 있으며 **유지·계승**해야 한다.

그러나 확인된 8개 반례는 튜닝 문제가 아니라 두 개의 구조적 오류로 환원된다:

**(A) "재직렬화·flatten 결과를 원문으로 착각"** — 다음 5개가 여기 속한다.
- XML `<select id="getOrderDetail">...</select>`에서 내부 text만 반환되어 원문 요소와 key attribute 소실 → 요소 전체 span을 청크로 잡지 않았기 때문.
- YAML `first.enabled`/`second.enabled`가 동일한 첫 번째 `enabled` 행으로 기록 → 위치(span)가 아니라 leaf key 이름으로 매핑했기 때문.
- Markdown fenced code block이 내부 빈 줄에서 절단 → 펜스 종료 규칙(빈 줄은 펜스를 닫지 않음)을 무시했기 때문.
- 1,200자 고정 분할이 의미 단위를 절단 → 애초에 의미 청크가 아님.
- JSON/YAML/TOML/config를 dict/leaf로 flatten 후 문자열화 → 원문 표현·주석·계층 문맥 소실.

**(B) "occurrence를 reference로 착각"** —
- `logger.info("ORDER.API")`와 `client.get("ORDER.API")`가 동일한 외부 사용 관계로 연결. 현재 방식이 실제로 증명하는 것은 "method 본문에 exact key occurrence가 존재함"이지 "실제 소비 API 호출"이 아니다. 이 둘의 의미 차이를 시스템이 구분하지 못한다.

**(C) 평가·게이트의 부재** —
- unknown-format Agent가 negative sample을 빈 배열로 전달하고, 같은 sample을 본 후보들의 결과 합의만으로 승인 → 합의한 오답과 미관측 일반화를 차단하지 못함.
- 기존 테스트가 예제 추출·길이 제한·다수결 실행만 확인하고 boundary exactness, held-out generalization, false-positive rate를 측정하지 않음.

따라서 **아키텍처 교체(청크 정의를 byte-span으로, 참조를 verifier 기반 exact-match로)와 평가 체계 신설**이 필요하다.

---

## 4. 조사 방법·검색 범위·source selection 기준

공식 문서·원 논문·원 저장소를 1차 소스로 삼았고, 라이브러리 소개 페이지나 vendor benchmark는 단독 근거로 채택하지 않았다. 조사 축은 (1) 구조화 텍스트 CST/source-map, (2) 바이너리 문서 AI, (3) grammar inference·LLM 검증, (4) 코드 참조 해석(AST/source span), (5) 보안(XXE·fuzzing)이다. 2026년 7월 기준 유지보수 상태와 버전을 확인했다. 확인·주장·추론을 아래에서 명시적으로 구분한다.

- **확인된 사실(공식 문서/원 저장소)**: Tree-sitter node의 byte offset API, ruamel.yaml lc 속성, json-source-map의 위치 산출, tomlkit style-preserving, markdown-it-py token.map, Python ast의 col_offset, Docling provenance, defusedxml 방어 항목, CommonMark 펜스 종료 규칙, tree-sitter 오류복구/OOM 사례.
- **주장(논문/커뮤니티)**: MinerU/Docling/Marker의 정확도·속도 수치, self-consistency AUROC 범위.
- **연구자 추론**: 아키텍처 순위, verifier 게이트 설계, provenance 이원화 필요성.

---

## 5. 파일 계열별 후보 기술 비교표

원시 지표(source fidelity 가능성, provenance 정밀도, 유지보수, 라이선스, 운영 적합성)를 함께 제시한다.

### 5.1 구조화 텍스트

| 형식 | 1위 도구 | 원문 byte-span | 근거·비고 |
|---|---|---|---|
| 범용 CST | **Tree-sitter (py-tree-sitter)** | ✅ 노드마다 `start_byte`/`end_byte` + row/column point | py-tree-sitter 0.25.2 공식 문서: Node는 `start_byte`("The byte offset where this node starts"), `end_byte`, "The byte range of source code that this node represents, in terms of bytes"를 제공하고 `source[node.start_byte:node.end_byte]`로 원문 부분을 복원하는 예시를 명시. CST는 모든 토큰(쉼표·괄호 포함)을 노드로 보존. 현재 core 0.26.x대 활발히 유지보수. |
| YAML | ruamel.yaml (round-trip) + tree-sitter-yaml 보조 | 부분(라운드트립 lc) / ✅(tree-sitter) | ruamel.yaml는 comment·key order·flow style을 round-trip 보존하고 RoundTripParser로 읽은 collection은 `lc.line`/`lc.col`을 노출. 단 dotted-path의 정확 span 구분(`first.enabled` vs `second.enabled`)에는 CST가 더 안전. anchor/alias/multi-doc/block scalar 처리 강점. |
| JSON | json-source-map (open-alchemy) + tree-sitter-json | ✅ | 각 JSON pointer마다 `value_start`/`value_end`/`key_start`/`key_end`를 line·column·position(문자 위치)로 제공. duplicate key·escape·large array 대응 시 tree-sitter-json 병용. |
| TOML | tomlkit + tree-sitter-toml 보조 | 부분 / ✅ | tomlkit은 TOML v1.1.0 준수·주석/공백/순서까지 style-preserving하며 `dumps(parse(x))==x` 불변식 제공(단 순서 어긋난 sub-table은 정규화 예외). 위치 span API가 약하므로 정확 offset이 필요하면 tree-sitter-toml. Python ≥3.9, MIT. |
| Markdown | markdown-it-py (CommonMark/GFM) | 라인 단위(`token.map=[line_begin,line_end]`) | token.map은 라인 범위만 주므로 문자 단위 정밀도가 필요하면 라인→offset 매핑을 별도 구현. tree-sitter-markdown은 저장소 스스로 "not recommended where correctness is important"라 경고하므로 boundary exactness 용도로 부적합. |
| XML | tree-sitter-xml 또는 SAX+span 재구성 | ✅(tree-sitter) / ❌(lxml sourceline) | lxml `sourceline`은 65,535 라인 한계(Launchpad Bug #1341590)와 byte offset 부재로 정확 span에 부적합. libxml2의 `XML_MAX_TEXT_LENGTH`(약 10MB) 제약도 존재. select/insert 같은 요소는 여는 태그~닫는 태그 전체를 청크로 잡아야 CDATA/entity/namespace/dynamic tag를 보존. |
| Properties/INI/CSV | 규격 기반 logical-record scanner | ✅(자체 구현) | continuation line·escaped separator·quoted newline·duplicate section을 규격대로 처리하는 결정론적 스캐너. Python `configparser`/`csv`는 재직렬화 성향이라 원문 span 보존용으로는 얇은 래핑 필요. |

### 5.2 비정형·바이너리 문서

| 도구 | provenance | 라이선스/운영 | 판정 |
|---|---|---|---|
| **Docling (DoclingDocument)** | page number + bounding box + content_layer(main/header/footer) | MIT, IBM Research Zürich 개발·2024-07 오픈소스화, 현재 Linux Foundation AI & Data 호스팅. Python, CPU 가능 | **바이너리 1위.** 문서(Ylang Labs/Docling) verbatim: DoclingDocument는 "every heading, paragraph, table, caption, and picture along with provenance such as page numbers, bounding boxes, and the content_layer flag that tells you whether an item belongs to the main body or the header and footer bands"를 저장. 2026년 활발(v2.106.0, 2026-06-23). |
| PyMuPDF4LLM | page_chunks + page_boxes(bbox class) | AGPL/상용 이중, CPU 빠름, native PDF 강점 | 보조: native·텍스트 PDF 고속 전처리. Office/HWP는 PyMuPDF Pro 필요. |
| MinerU (2.5) | layout·table·formula 구조 | AGPL-3.0, GPU 의존, CJK 강점 | 보조: 복잡 레이아웃·CJK 전용. 원논문(arXiv:2409.18839, Shanghai AI Laboratory) 기준 레이아웃 탐지는 학술 검증셋 **77.6% mAP**(DocXchain 52.8% 대비), 수식 탐지 87.7% AP50(Pix2Text-MFD 60.1% 대비). (일부 커뮤니티가 인용하는 "97.5 mAP"는 원 출처가 확인되지 않아 채택하지 않음.) |
| Unstructured | element별 coordinates(PixelSpace bbox) | 오픈소스+상용 | 보조: 다포맷 partition, 단 표 구조·원문 충실도는 재직렬화 성향. |
| Microsoft MarkItDown | ❌ (bbox/page 없음) | MIT, 0.x | **탈락 후보.** 다수 소스가 확인하듯 PDF는 서식 없이 평문화, provenance 부재, 서드파티 래퍼 성격. |
| Apache Tika | 제한적 | Java 의존 | 보조: 포맷 폭 넓으나 JVM 운영 부담·정밀 provenance 약함. |
| HWP/HWPX | pyhwp(HWP5), rhwp-python(HWP+HWPX) | pyhwp AGPL·2018 이후 정체 / rhwp Rust+PyO3 | 한국어 문서 전용. pyhwp는 스트림/XML 변환, rhwp-python은 두 포맷 통합 API. |

### 5.3 unknown proprietary text

| 접근 | 검증 가능성 | 판정 |
|---|---|---|
| LLM이 직접 청크 text 생성 | 낮음(할루시네이션) | 금지 |
| **Agent가 span/skill 제안 + 결정론 verifier** | 높음 | **1위.** Agent는 원문 상 (start,end) 또는 regex/state-machine skill만 제안; 최종 텍스트는 원문 slicing. |
| grammar inference (V-Star, L*) | 이론적 | 참고. 흑박스 CFG 학습은 일반적으로 계산 불가능(Angluin), 실전은 regex/state-machine으로 제한. |
| single-model 다수결만으로 승인 | 낮음 | 금지 |

---

## 6. 청크 분리 — 전체 1위 아키텍처와 선정 이유

**1위: "Format Router → 형식별 CST/source-map 파서 → 원문 byte-span 청크" (parser-first).**

선정 이유:
1. **Source fidelity를 구조적으로 보장.** 모든 청크가 `original_source[start:end]`로 정의되므로 hash 일치·round-trip 불변식을 기계적으로 검증 가능하다. Tree-sitter의 byte offset API가 이를 언어 무관하게 제공한다.
2. **의미 단위 보존.** CST는 여는~닫는 토큰을 모두 노드로 가지므로 XML 요소 전체·YAML 서브트리·Markdown 펜스 블록을 온전한 단위로 자를 수 있다. 이는 현재 구현의 XML/YAML/Markdown 반례를 직접 해소한다.
3. **결정론성·재현성.** LLM 개입 없이 동일 입력→동일 청크. observability(어느 노드에서 유래했는지)를 그대로 얻는다.

보조 규칙:
- 파서가 결과를 재직렬화해도 그것을 저장하지 않고 항상 원문 offset으로 slicing한다.
- 라인 단위 span만 주는 도구(markdown-it-py, ruamel lc)는 라인→byte offset 매핑 레이어를 덧붙여 문자 단위 정확도를 확보한다.
- 파싱 실패(malformed/ambiguous)는 억지 성공 처리하지 않고 fail-closed로 UNVERIFIED 보존한다.

---

## 7. 파일 계열별 1위 도구/방법과 보조 도구

- **범용 구조화 텍스트**: 1위 Tree-sitter(py-tree-sitter). 보조: 형식별 전용 파서(아래).
- **YAML**: 1위 tree-sitter-yaml(정확 span) + ruamel.yaml(주석·anchor 문맥).
- **JSON/JSONL**: 1위 json-source-map + tree-sitter-json(중복 키·대용량).
- **TOML**: 1위 tomlkit + tree-sitter-toml(정확 offset 필요 시).
- **Markdown/AsciiDoc/HTML/template**: 1위 markdown-it-py(라인→offset 매핑) + 임베디드 언어(SQL/코드)는 해당 언어 tree-sitter로 2차 파싱.
- **Properties/INI/CSV/TSV**: 1위 규격 기반 logical-record scanner(자체, 원문 span 보존).
- **SQL/query asset**: 1위 tree-sitter-sql 또는 문장 경계 스캐너.
- **PDF/Office**: 1위 Docling(page/bbox provenance). 보조: native PDF는 PyMuPDF4LLM, 복잡 CJK 레이아웃은 MinerU.
- **HWP/HWPX**: 1위 rhwp-python(HWP+HWPX 통합), 보조 pyhwp(HWP5).
- **unknown text**: 1위 Agent 제안 + 결정론 verifier.

---

## 8. 직접 참조(REFER_TO) — 1위 아키텍처와 증거 수준

**1위: "코드 AST의 literal/identifier site 정확 span → 외부 청크 key span과 whole-token exact-match → 결정론 verifier 승인".**

증거 계층(강→약)과 승격 규칙:
1. **가장 강함 — call argument evidence**: AST가 call 노드와 인자 리터럴의 span을 제공하면 "이 method가 이 key를 인자로 사용"까지 주장 가능. (현재 AST 계약에는 없음.)
2. **중간 — literal site occurrence**: method 본문 소스 범위 안에 외부 청크 key와 exact 일치하는 문자열 리터럴 span이 존재. **현재 안전하게 주장 가능한 최대치.** Python이라면 `ast.Constant`의 `col_offset`/`end_col_offset`로 정확한 리터럴 span을 얻는다(공식 문서: `source_line[col_offset:end_col_offset]`).
3. **약함(금지) — 부분 문자열/대소문자 보정/embedding/LLM 판단**: 사실로 승격 금지.

**최소 증거 정의**: (코드 리터럴 토큰 값, whole-token, case-sensitive) == (외부 청크가 노출하는 key 값). 상수 전파는 순수 불변 상수의 한 단계까지만 허용하고, dynamic concat은 증거로 인정하지 않는다.

**의미 차이 명시**: `logger.info("ORDER.API")`와 `client.get("ORDER.API")`는 리터럴 occurrence 수준에서 동일하다. 현재 AST가 call 문맥을 주지 않는 한 시스템은 이 둘을 구분할 수 없고, 따라서 **"직접 참조(occurrence)"까지만 사실로 저장**하고 "실제 소비/호출"로 확대 해석하지 않는다. duplicate key·ambiguous 매칭은 다중 후보로 표기하고 단일 사실로 확정하지 않는다.

**현재 AST가 call arguments/literal sites를 제공하지 않을 때 주장 가능한 것**: 오직 "method 소스 범위에 key와 exact 일치하는 리터럴이 있다"뿐이며, 그 이상(어떤 API를 호출한다, 어떤 설정을 읽는다)은 주장 불가. 이 경우 recall을 포기하고 precision을 지킨다.

---

## 9. Agent Fallback — 1위 흐름과 승인/거부 게이트

**1위 흐름 (candidate generation + deterministic verifier 분리):**

1. **입력 준비**: unknown 파일을 train/observe용과 held-out용으로 엄격히 분리(단순 복제·key 치환은 held-out으로 세지 않음).
2. **제안(candidate generation)**: Agent가 (a) 청크 경계 span 후보 또는 (b) regex/delimiter/state-machine skill을 다수 제안. **청크 텍스트 자체는 생성 금지.**
3. **결정론 검증(verifier)** — 아래를 모두 통과해야 승인:
   - **Round-trip 복원**: 제안 span들을 순서대로 이으면 원문과 정확히 일치(overlap/gap/duplicate 없음).
   - **Boundary exactness**: 각 청크가 `source[start:end]`이고 hash 일치.
   - **Held-out generalization**: 제안이 본 적 없는 파일/포맷 변형에서도 위 불변식을 유지.
   - **Negative sample 처리**: 빈 배열이 아니라 실제 decoy/comment/구분자 충돌 케이스를 포함해 실패해야 할 입력에서 실패하는지 확인.
4. **게이트**:
   - **승인**: 위 전부 통과 시에만 청크로 확정.
   - **거부/보류(fail-closed)**: 하나라도 실패하면 청크를 만들지 않고 파일을 UNVERIFIED로 남긴다. LLM 다수결 합의는 승인 근거로 사용하지 않는다.

**근거**: self-consistency 기반 할루시네이션 탐지는 상한이 낮다. ChainPoll 논문(arXiv:2310.18344) Table 9(TriviaQA)에서 SelfCheck-BERTScore AUROC 0.784·SelfCheck-NGram 0.757 수준이고, BEACON 벤치마크(arXiv:2606.07528)는 "SelfCheckGPT proxies ... yields 0.5792 AUROC for the best combination variant"로 훨씬 낮게 보고한다. 즉 다수결/자기일치는 "합의한 오답"을 원리적으로 걸러내지 못하므로 정답 보증이 될 수 없고, 반드시 원문 복원 가능성 같은 결정론적 성질로 승인해야 한다.

---

## 10. 탈락 후보와 구체적 탈락 이유

- **Microsoft MarkItDown** — provenance(page/bbox) 부재, PDF 평문화로 heading/본문 미구분, 서드파티 래퍼. source fidelity·직접 참조 증명에 부적합.
- **고정 길이(1,200자) 분할** — 정의상 의미 청크가 아님(절대 제약 위반).
- **flatten 후 문자열화(dict/leaf)** — 원문 표현·주석·계층·중복 키 문맥 소실.
- **lxml sourceline 기반 XML 청크** — 65,535 라인 한계 및 byte offset 부재로 boundary exactness 불가.
- **tree-sitter-markdown을 정확도 목적에 사용** — 저장소 스스로 correctness 부적합 경고.
- **LLM이 청크 텍스트 직접 생성** — round-trip 검증 불가, 재직렬화=원문 주장 금지 위반.
- **Agent 다수결 승인** — 합의 오답·미관측 일반화 차단 불가.
- **embedding 유사도/부분 문자열 매칭으로 REFER_TO 생성** — 절대 제약 위반(사실 승격 금지).
- **의미 관계 추론(CALLS_SQL 등)** — 절대 제약 위반.

---

## 11. Benchmark Corpus, Gold Annotation Schema, Metric 계산법

**Corpus 축 (train/observe와 held-out을 엄격 분리):**
- 상태: 정상 / 손상 / 부분 손상.
- 인코딩·개행: LF/CRLF, UTF-8 BOM, CP949/EUC-KR, 혼합 Unicode.
- 크기: 빈 파일 / 초소형 / 초대형.
- 반복·중복: 같은 key 반복·중복·shadowing.
- 중첩: nested object/element, namespace, anchor/alias, multiline scalar.
- 형식별 함정: XML CDATA/entity/namespace/dynamic tag; JSON escape/large array/duplicate key; YAML anchor/alias/multi-doc/block scalar/custom tag; TOML array-of-table/multiline/dotted key; Properties continuation/escaped separator/unicode escape; INI default/interpolation/duplicate section; CSV quoted newline/escaped quote/variable columns/huge field; Markdown 펜스 내 빈 줄/heading/list/table/HTML/front matter; template 내 embedded SQL/code/directive.
- 바이너리: scanned/multi-column/table-heavy/encrypted PDF; DOCX/XLSX/HWP의 표·병합 셀·수식·숨김 시트·텍스트 상자.
- unknown: line-oriented/nested/delimiter collision/misleading decoy·comment.
- 코드: literal/comment/log message/annotation/constant/dynamic concat/duplicate key.

**Gold annotation schema (청크당):** `{file, byte_start, byte_end, char_start, char_end, semantic_type, parent_path, expected_hash}`. 참조당: `{method_id, code_literal_span, external_chunk_id, key_value, evidence_level}`.

**Metric 계산법 (원시 지표 우선):**
1. **Boundary exactness** = 기대 (start,end)와 정확 일치한 청크 수 / 전체 gold 청크 수.
2. **Source fidelity** = `chunk_text==source[start:end]` 및 hash 일치 비율(1.0 목표, 미달 시 자동 실패).
3. **Reference-key precision/recall** = key 추출 기준.
4. **REFER_TO precision/recall** 및 **false-positive 절대 개수**(precision-first이므로 FP를 최우선 최소화).
5. **Nested-structure correctness** = 중첩·중복 키에서 올바른 span 선택 비율.
6. **Malformed-input behavior** = fail-closed 비율(억지 성공 0 목표).
7. **Held-out generalization** = held-out corpus에서의 위 지표(복제·치환 제외).
8. **Round-trip invariants** = overlap/gap/duplicate 0.
9~14. Performance(크기·노드 수별 시간·메모리), Security, Operational fit, Maintenance, License, Observability는 §13에서.

종합 점수만 제시하지 않고 위 원시 지표를 모두 병기한다.

---

## 12. Adversarial / Fuzz / Property / Metamorphic 테스트 계획

- **Property-based**: 모든 청크에 대해 `source[start:end]==chunk_text`, 청크 union==원문(overlap/gap 없음)을 불변식으로 무작위 입력에 대해 검증(Hypothesis 등).
- **Metamorphic**: 의미 보존 변형(공백 추가, key 순서 변경, 개행 LF↔CRLF)을 가했을 때 청크 경계와 REFER_TO 결과가 규칙대로 변하는지 확인. 예: 무관한 위치에 주석 추가 시 참조 개수 불변.
- **Fuzz**: malformed XML/JSON/YAML/TOML/CSV, 깊은 중첩, 대용량 필드, 구분자 충돌, decoy 문자열을 투입해 크래시·행·오탐 발생 여부 확인.
- **Adversarial 참조**: 동일 key를 comment·log·실제 인자에 배치해 occurrence와 (가능 시) argument evidence를 구분하는지, case 변형·부분 문자열이 잘못 연결되지 않는지 확인.
- **Agent held-out**: Agent가 보지 못한 포맷 변형에서 verifier가 실제로 거부하는지(미관측 일반화 차단) 확인.

---

## 13. 성능·보안·비용·라이선스·Windows 운영 분석

**성능**: 구조화 텍스트 CST 파싱은 파일 크기에 대략 선형이며 GPU 불필요. Docling은 layout AI 모델로 페이지당 비용이 크고, MinerU는 GPU 의존이라 배치·전용 워커로 격리 권장. 성능 지표는 파일 크기·노드 수별로 시간/메모리를 측정해 원시값으로 관리.

**보안**:
- **XML**: `defusedxml` 사용. 공식 문서 기준 "limited entity expansion level to antagonize billion laugh attacks; limited total length of expansions to prevent quadratic blowups; monkey patch to prevent retrieval of external entities and DTDs"로, `xml.etree.ElementTree`를 `defusedxml.ElementTree`로 교체하면 "automatically protected from XXE, Billion Laughs, and Quadratic Blowup attacks". Python 공식 문서도 신뢰 불가 XML 파싱 시 defusedxml 사용을 권고. (defusedxml 최신 안정 릴리스는 v0.7.1(2021-03)로 다소 오래되었음을 유의.)
- **Tree-sitter**: malformed 입력에 대해 크래시 대신 ERROR/MISSING 노드를 내도록 설계되었으나, 이는 **절대적 보장이 아니다.** GitHub Discussion #2073은 fuzzing에서 "the parser seemingly enters an infinite error recovery loop before eventually running out of memory" 사례를, 연관 이슈 #2071은 특정 버전(0.20.8/0.20.9) stack overflow를 보고한다. 따라서 subprocess 격리 + 시간·메모리·입력 크기 제한을 필수로 둔다.
- **문서 파서**: 기존 subprocess 격리·크기/페이지/시간 제한·취소 시 kill 유지. archive bomb·regex DoS·prompt injection(Agent 경로)에 대한 입력 검증 유지.

**비용**: 구조화 텍스트·native PDF(PyMuPDF4LLM)·CPU Docling은 저비용. GPU 문서 AI(MinerU)와 LLM Agent 경로는 호출 단위 비용이 크므로 unknown·복잡 문서로 한정.

**라이선스**: Docling(MIT), tree-sitter(MIT), tomlkit(MIT), markdown-it-py(MIT), json-source-map(파이썬 포팅 확인 필요), ruamel.yaml(MIT). **AGPL 주의**: pyhwp(AGPL), MinerU(AGPL-3.0), PyMuPDF/PyMuPDF4LLM(AGPL/상용 이중). 상용 배포 시 AGPL 도구는 격리·대체·상용 라이선스 검토 필요.

**Windows 운영(Python 3.12)**: 순수 파이썬/휠 제공 도구(tree-sitter 바인딩, tomlkit, markdown-it-py, ruamel.yaml, json-source-map, Docling)는 Windows에서 설치 용이. GPU 모델(MinerU)은 Windows+CUDA 설정 부담이 크므로 별도 워커·컨테이너 권장. Rust 기반 rhwp-python은 휠 여부 확인 필요.

---

## 14. 단계별 도입·Rollback·관측성 계획

**Stage 0 (기반)**: 청크 정의를 byte-span으로 전환, source fidelity 자동 검증(hash·round-trip) CI 게이트 신설. 기존 안전장치 유지. Rollback: 기존 flatten 경로를 feature flag 뒤에 두고 병행.

**Stage 1 (구조화 텍스트)**: Format Router + Tree-sitter/전용 파서로 XML·JSON·YAML·TOML·Markdown·Properties/INI/CSV 처리. benchmark corpus로 boundary exactness·fidelity 측정. **임계값**: fidelity=1.0, boundary exactness ≥ 목표치 미달 시 해당 형식 롤백.

**Stage 2 (바이너리)**: Docling으로 page/bbox provenance 계층 추가(텍스트 계층과 분리 저장). native PDF는 PyMuPDF4LLM 보조.

**Stage 3 (참조)**: 코드 AST의 literal site span 기반 exact-match verifier로 REFER_TO 생성. **임계값**: false-positive=0을 목표, FP>0이면 매칭 규칙을 더 엄격화하거나 롤백. call argument evidence는 AST 계약이 확장될 때만 활성화.

**Stage 4 (Agent fallback)**: unknown 포맷에 한해 Agent 제안 + verifier + held-out 게이트. **임계값**: held-out round-trip 실패 시 자동 fail-closed.

**관측성**: 모든 청크에 (파서·노드 경로·span·hash·provenance 계층), 모든 REFER_TO에 (evidence_level·code span·key·verifier 통과 기록), 모든 실패에 (실패 사유·UNVERIFIED 태그)를 기록해 재현 가능하게 한다.

**임계값 요약(결정 변경 트리거)**: source fidelity가 1.0 미만이면 해당 파서 비활성화; REFER_TO false-positive가 1건이라도 발생하면 매칭 규칙 강화; held-out generalization이 목표 이하이면 Agent skill 거부; malformed 입력에서 억지 성공이 관측되면 fail-closed 강제.

---

## 15. 아직 증명하지 못한 영역과 위험

- **정확도 수치의 재현성**: MinerU/Docling/Marker의 mAP·속도, self-consistency AUROC는 원논문·벤치마크 조건에 종속된 **주장**이며, 대상 corpus에서 직접 재측정이 필요하다. 특히 커뮤니티가 인용하는 MinerU "97.5 mAP"는 원 출처 미확인으로 채택하지 않았다.
- **line→byte offset 매핑의 완전성**: markdown-it-py·ruamel의 라인 단위 정보를 문자 단위로 승격하는 레이어의 엣지 케이스(멀티바이트·BOM·CRLF)는 property test로 추가 검증 필요.
- **바이너리 provenance의 근본적 한계**: PDF/Office/HWP는 원문 byte-span이 없으므로 "완전 단위 청크"와 "source fidelity=1.0"이 정의상 불가능. 이 계열은 page/bbox 근사 provenance만 제공되며, 이를 텍스트와 동일 신뢰도로 취급하면 안 된다.
- **tree-sitter 견고성**: 문서화된 무한 recovery loop·OOM·stack overflow 사례가 있어 자원 제한 없이는 신뢰 불가. 튜닝 가능한 공식 recursion 한계는 문서화되어 있지 않음.
- **직접 참조의 의미 상한**: 현재 AST 계약(call argument·literal site 부재)에서는 "occurrence"까지만 사실이며, "실제 소비"는 증명 불가. AST 확장 없이는 이 격차를 메울 수 없다.
- **unknown 포맷의 일반화**: Agent가 제안한 skill이 held-out에서 통과하더라도 완전히 새로운 변형에 대한 일반화는 보증되지 않으며, 이 경우 fail-closed가 유일한 안전 정책이다.

---

## 16. 참고문헌 (직접 링크)

- Tree-sitter 공식 문서(노드 byte offset): https://tree-sitter.github.io/py-tree-sitter/classes/tree_sitter.Tree.html · https://tree-sitter.github.io/tree-sitter/using-parsers/2-basic-parsing.html · https://pypi.org/project/tree-sitter/ · https://github.com/tree-sitter/tree-sitter
- Tree-sitter grammars(YAML/JSON/XML/TOML/Markdown): https://github.com/tree-sitter-grammars/ · https://github.com/tree-sitter-grammars/tree-sitter-markdown · https://github.com/ikatyang/tree-sitter-yaml
- ruamel.yaml: https://pypi.org/project/ruamel.yaml/ · https://yaml.dev/doc/ruamel.yaml/detail/
- json-source-map(Python): https://pypi.org/project/json-source-map/ · https://github.com/open-alchemy/json-source-map · yaml-source-map: https://pypi.org/project/yaml-source-map/
- tomlkit: https://github.com/python-poetry/tomlkit · https://tomlkit.readthedocs.io/en/latest/quickstart/
- markdown-it-py(token.map): https://markdown-it-py.readthedocs.io/en/latest/api/markdown_it.token.html · https://github.com/executablebooks/markdown-it-py
- CommonMark Spec 0.31.2(펜스 종료 규칙): https://spec.commonmark.org/0.31.2/#fenced-code-blocks
- lxml(sourceline 한계): https://bugs.launchpad.net/lxml/+bug/1341590 · https://lxml.de/parsing.html
- Python ast(Constant col_offset): https://docs.python.org/3/library/ast.html
- Docling: https://www.docling.ai/ · https://arxiv.org/abs/2408.09869 · https://arxiv.org/abs/2501.17887 · https://docling-project.github.io/docling/reference/docling_document/ · https://github.com/docling-project/docling/blob/main/CHANGELOG.md
- PyMuPDF4LLM: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/ · https://github.com/pymupdf/pymupdf4llm
- MinerU 원논문: https://arxiv.org/abs/2409.18839 · https://github.com/opendatalab/MinerU
- Unstructured: https://docs.unstructured.io/open-source/concepts/document-elements · https://github.com/Unstructured-IO/unstructured
- MarkItDown 한계: https://www.infoworld.com/article/3963991/markitdown-microsofts-open-source-tool-for-markdown-conversion.html · https://realpython.com/python-markitdown/
- HWP/HWPX: https://github.com/mete0r/pyhwp · https://github.com/DanMeon/rhwp-python
- grammar inference / active learning: https://arxiv.org/pdf/2404.04201 (V-Star) · https://arxiv.org/pdf/2503.08486 · https://arxiv.org/pdf/2505.16978 (HyGenar)
- self-consistency / 할루시네이션: https://arxiv.org/abs/2310.18344 (ChainPoll) · https://arxiv.org/pdf/2405.13131 · https://arxiv.org/html/2510.06265v2 (SelfCheckGPT 서베이)
- defusedxml: https://pypi.org/project/defusedxml/ · https://github.com/tiran/defusedxml · https://docs.python.org/3/library/xml.html
- tree-sitter 견고성/보안: https://github.com/tree-sitter/tree-sitter/discussions/2073 · https://github.com/tree-sitter/tree-sitter/issues/1870
- MyBatis 이름 해석(참조 맥락): https://mybatis.org/mybatis-3/getting-started.html · https://mybatis.org/mybatis-3/sqlmap-xml.html
