# 🔍 미지의 코드베이스 정체 탐정 시스템

> **"답안지 없는 진짜 발견"** — IDE 의 'Go to Definition' 처럼 단서를 따라가며 추론하는 LLM 에이전트.

---

## 📖 목차

1. [핵심 설계 철학](#1-핵심-설계-철학)
2. [전체 구조](#2-전체-구조)
3. [흐름도 — 호기심 따라가기](#3-흐름도--호기심-따라가기)
4. [12가지 도구 카탈로그](#4-12가지-도구-카탈로그)
5. [실제 결과 — ProFrame 백지에서 발견](#5-실제-결과--proframe-백지에서-발견)
6. [학습된 캐시 (Self-Learning)](#6-학습된-캐시-self-learning)
7. [실행하는 법](#7-실행하는-법)
8. [확장 가이드](#8-확장-가이드)

---

## 1. 핵심 설계 철학

### 🚫 안 하는 것 (이전 버전에서 제거)

- **프레임워크별 답안지 JSON 없음**. (이전: `proframe.json`, `fastapi.json` 같은 사전 정답)
- 사전 정답 매칭 = 사실상 lookup table → 진짜 에이전트가 아님 → **전부 제거**.

### ✅ 하는 것

> **"표준 라이브러리 화이트리스트만 사전 지식. 나머지는 코드 읽고 추론."**

```
Skill = 캐시이지 정답이 아니다.
없으면 LLM 이 발견. 발견하면 그게 캐시가 됨.
```

### IDE 의 'Go to Definition' 흐름 모방

사람이 처음 보는 코드 분석할 때:

```
① 함수 호출 발견  →  "이게 뭐지?"  →  Go to Definition
② 정의부 찾기    →  내용 읽기
③ 사용 패턴 보기  →  여러 호출처 grep
④ 연관 파일 추적  →  파일명·인자에서 단서
⑤ 패턴 일반화    →  여러 증거 종합
⑥ 결론 도출      →  "이건 뭐다"
```

이 흐름을 LLM 도구 호출로 그대로 재현했습니다.

---

## 2. 전체 구조

```
test/codebase_detection_comparison/
│
├── 📄 stack_detection.yaml   ← AI 한테 주는 지시문 (4-Phase + 호기심 탐색)
├── 📄 README.md
│
├── 📋 schemas/                ← JSON Schema (3개)
│   ├── framework_rule.schema.json   (learned 캐시용)
│   ├── skill_meta.schema.json
│   └── global_rules.schema.json
│
├── 🌍 rules/                  ← 전역 규칙 (5개 JSON)
│   ├── ext_lang.json          ← 확장자 → 언어 매핑
│   ├── known_prefixes.json    ← ★ 표준 라이브러리 화이트리스트 (사전 지식 전부)
│   ├── known_proprietary.json ← (선택) 독점 프레임워크 힌트 맵
│   ├── build_markers.json
│   └── thresholds.json
│
├── 🔌 skills/                 ← 언어별 탐색 매뉴얼 (정답지 아님!)
│   ├── detect_python/
│   │   ├── meta.json          ← 언어 식별 (확장자, import 정규식)
│   │   └── SKILL.md           ← "Python 만나면 어디 먼저 볼지" 팁
│   ├── detect_c_legacy/       ← C/Pro*C/COBOL 탐색 팁
│   ├── detect_java/
│   ├── detect_typescript/
│   ├── detect_go/
│   ├── detect_unknown/
│   └── learned/               ← 🆕 LLM 이 발견한 규칙 자동 저장 (다음 실행 캐시)
│
├── 🛠️ Python 모듈 (10개)
│   ├── code_signals.py        ← 14가지 힌트 추출기 (한 파일에서)
│   ├── dynamic_discovery.py   ← Phase 3.5 핵심 (사양 + LLM 추론)
│   ├── file_tools.py          ← os.walk/regex 유틸
│   ├── negative_matcher.py    ← 표준 외 prefix 자동 발견
│   ├── rule_validator.py      ← JSON Schema 검증
│   ├── skill_loader.py        ← Skill + learned 캐시 로드
│   ├── run_logger.py          ← 실행 로그 파일 저장
│   ├── naive_detection.py     ← 방법 A (단일 LLM 호출)
│   ├── deep_detection.py      ← 방법 B (DeepAgent + 12 tools)
│   └── run_comparison.py      ← 엔트리
│
└── 📝 logs/                   ← 자동 생성. 매 실행마다 .log + .json
```

---

## 3. 흐름도 — 호기심 따라가기

### 전체 4-Phase + Phase 3.5

```
              [입력] 미지의 코드베이스 폴더
                          │
                          ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║  Phase 1: Discovery  (1초, 무료)                                  ║
   ║  count_file_extensions → ".c 다수네"                              ║
   ║  find_marker_files    → [Makefile? 아니면 없음]                  ║
   ║  load_skill("C")       → 탐색 매뉴얼 받음 (정답지 X)             ║
   ╚══════════════════════════════════════════════════════════════════╝
                          │
                          ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║  Phase 2: Manifest & Config  (5~10초, 무료)                      ║
   ║  read_manifest("requirements.txt") → fastapi, neo4j ...          ║
   ║  read_config_file(".env")          → DB URL                       ║
   ║                                                                  ║
   ║  ★ 매니페스트로 80% 확정되면 Phase 4 로 점프                     ║
   ╚══════════════════════════════════════════════════════════════════╝
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
        매니페스트 0           매니페스트 0 + 미지 prefix
              │                       │
              ▼                       │
   ╔══════════════════════════╗       │
   ║  Phase 3: Pattern Detect ║       │
   ║  grep_imports            ║       │
   ║  detect_proprietary      ║       │
   ║  → "pfm, pdb, zap 미지"  ║       │
   ╚════════════╤═════════════╝       │
                │                     │
                └──────────┬──────────┘
                           │
                           ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║   ★★★ Phase 3.5: Curiosity Loop (호기심 탐색) ★★★              ║
   ║                                                                  ║
   ║   [Round 1] 개요 파악                                             ║
   ║   collect_unknown_evidence("pfm")                                ║
   ║     → 14가지 힌트 JSON 수집                                       ║
   ║       (주석헤더, typedef, 함수명, 매크로, API, 도메인어, 자연어 등)║
   ║                                                                  ║
   ║   [Round 2] ★ Go to Definition 으로 단서 추적 (3~10회 반복) ★    ║
   ║                                                                  ║
   ║     "mpfmdbio 자주 보이네"                                        ║
   ║       → find_definition("mpfmdbio")                              ║
   ║       → read_file_range(pfmdbio.h, line-5, line+40)              ║
   ║       → "DB 호출 래퍼구나"                                        ║
   ║                                                                  ║
   ║     "PFM_TRY 매크로?"                                             ║
   ║       → find_definition("PFM_TRY")                               ║
   ║       → "setjmp 기반 예외처리"                                    ║
   ║                                                                  ║
   ║     "zpay_..._in_t 구조체?"                                       ║
   ║       → find_definition(구조체명)                                 ║
   ║       → 필드 보고 → "DBIO IO 구조체"                              ║
   ║                                                                  ║
   ║     "주석에 'proframe' 단어 있나?"                                ║
   ║       → grep_pattern("proframe|ProFrame", ".c,.h")                ║
   ║       → 발견!                                                     ║
   ║                                                                  ║
   ║   [Round 3] LLM 종합 판단                                         ║
   ║   characterize_unknown_library("pfm")                            ║
   ║     → {                                                          ║
   ║         canonical_name_guess: "ProFrame",                        ║
   ║         confidence: 0.83,                                        ║
   ║         library_role: "DB 접근 + TP 프레임워크",                  ║
   ║         architecture_clues: { layers: { a0xx: "검증",            ║
   ║                                          b0xx: "메인", ... } },  ║
   ║         likely_region: "Korea financial sector",                 ║
   ║         reasoning: "PFM_TRY/CATCH 매크로 + bxxx_dbio_*           ║
   ║                     함수명 + 한국어 주석 + 계좌/카드 도메인어..." ║
   ║       }                                                          ║
   ║                                                                  ║
   ║   [Round 4] 학습 (Self-Learning)                                 ║
   ║   confidence ≥ 0.7 → save_learned_skill_tool("pfm")              ║
   ║     → skills/learned/pfm_<date>.json 자동 저장                   ║
   ║     → 다음 실행부터 $0 으로 즉시 매치                            ║
   ╚══════════════════════════════════════════════════════════════════╝
                          │
                          ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║  Phase 4: Synthesis                                               ║
   ║  submit_detection({primary_language, frameworks, orms,            ║
   ║                    databases, proprietary_frameworks})            ║
   ╚══════════════════════════════════════════════════════════════════╝
```

---

## 4. 12가지 도구 카탈로그

DeepAgent 가 자유롭게 호출하는 도구. 각 카테고리별로 정리:

### 🔭 정찰 (Phase 1)

| # | 도구 | 역할 |
|---|------|-----|
| 1 | `count_file_extensions()` | 확장자별 파일 수 → 주 언어 추정 |
| 2 | `find_marker_files_tool()` | 빌드 마커(`pom.xml`, `requirements.txt` 등) 탐색 |
| 3 | `load_skill(language)` | 그 언어의 **탐색 매뉴얼** 반환 (정답지 아님) |

### 📦 매니페스트/설정 (Phase 2)

| # | 도구 | 역할 |
|---|------|-----|
| 4 | `read_manifest(path)` | 의존성 파일 앞 200줄 |
| 5 | `read_config_file(path)` | 설정 파일 앞 100줄 (DB URL 등) |

### 🔬 패턴 분석 (Phase 3)

| # | 도구 | 역할 |
|---|------|-----|
| 6 | `grep_imports(extensions)` | import/include 빈도 JSON |
| 7 | `grep_pattern_tool(regex, exts, max)` | 정규식 검색 → `[{file,line,text}]` |
| 8 | `detect_proprietary(import_freq_json)` | 표준 외 prefix 자동 발견 (네거티브 매칭) |

### 🕵️ Go to Definition 추적 (Phase 3.5 핵심)

| # | 도구 | 역할 |
|---|------|-----|
| 9 | `find_definition(symbol, exts)` | **함수/구조체/매크로 정의부 찾기** |
| 10 | `read_file_range(path, start, end)` | 특정 줄 범위만 읽기 |

### 🧠 동적 발견 + 학습 (Phase 3.5)

| # | 도구 | 역할 |
|---|------|-----|
| 11 | `collect_unknown_evidence(prefix)` | 14가지 힌트 자동 수집 (시작점) |
| 12 | `characterize_unknown_library(prefix)` | LLM 이 증거 종합해 가설 수립 |
| 13 | `save_learned_skill_tool(prefix)` | 학습 결과 캐시 (다음번 $0) |

### 📤 제출 (Phase 4)

| # | 도구 | 역할 |
|---|------|-----|
| 14 | `submit_detection(result_json)` | 최종 결과 제출 (1회만) |

---

## 5. 실제 결과 — ProFrame 백지에서 발견

### 검증 조건

- 사전 답안지 0건 (`skills/detect_c_legacy/rules/` 폴더 자체 없음)
- `known_proprietary.json` 에서 `pfm`, `pdb` 힌트 제거 (Tmax/SAP/CICS만 남김)
- 즉 시스템은 ProFrame 이라는 이름조차 모름

### 결과

```
타겟          : c:/uEngine/robo/data/source/apascrt00010t02_20260406/
주 언어        : C ✅
프레임워크     : ProFrame (confidence 0.83) ✅
ORM           : ProFrame DBIO ✅
독점 prefix   : pfm, pdb, zap ✅
───────────────────────────────────────────
토큰          : 444,758
실행시간      : 158초
도구 호출 수   : 47회
```

### 도구 호출 시퀀스 (실제 로그)

```
정찰:
  #1  write_todos (DeepAgent 자체 계획)
  #2  count_file_extensions  → {".c": 1}
  #3  find_marker_files      → []
  #4  load_skill("C")        → 탐색 매뉴얼

분석:
  #6  grep_imports(".c,.h")  → pfmcom.h, pfmutil.h, pdb_*.h ...
  #8  detect_proprietary     → [pfm, pdb, zap] 미지

호기심 탐색 (Phase 3.5):
  #13 collect_unknown_evidence("pfm")    ← 14힌트 수집
  #14 collect_unknown_evidence("pdb")
  #15 find_definition("mpfmdbio")        ← Go to Definition!
  #16 find_definition("mpfmdbio_errno")
  #17 find_definition("zpay_ap_rltm_auth_hst_i0002_in_t")  ← 구조체!
  #18 find_definition("PFM_ERRC")
  #19 find_definition("a000_input_validation")
  #20 find_definition("b000_main_proc")
  #21 find_definition("b200_dbio_rltm_auth_his_i0002")
  #22 find_definition("b100_dbio_comm_cd_dtl_canyn")
  #23 find_definition("b300_call_zapamcom10040")
  #24 read_file_range("zapamcom10060.c", 1, 140)    ← 헤더 주석 확인
  #25 read_file_range("zapamcom10060.c", 700, 760)  ← b200 함수 구현
  #26 read_file_range("zapamcom10060.c", 860, 930)  ← b300 함수 구현
  #27 read_file_range("zapamcom10060.c", 1135, 1165)
  #28 grep_pattern("PFM_TRY")
  #29 grep_pattern("ORACLE|Oracle|OCI|sqlca")       ← Oracle 단서 탐색
  #30 grep_pattern("ProFrame|proframe|TMAX|tmax")   ← 이름 직접 검색
  #31 grep_pattern("mpfmdbio\(")
  #32 characterize_unknown_library("pfm")           ← LLM 종합 추론!
  #33 characterize_unknown_library("pdb")
  #38-41 read_file_range × 4                       ← 추가 검증
  #42 collect_unknown_evidence("zap")
  #43-44 characterize_unknown_library("zap")

학습 + 제출:
  #45 save_learned_skill_tool("pfm")    ← skills/learned/pfm_<date>.json 저장
  #47 submit_detection                  ← 최종 결과
```

### LLM 의 추론 (실제 저장된 캐시 발췌)

```json
{
  "name": "ProFrame",
  "metadata": {
    "llm_confidence": 0.83,
    "llm_reasoning": "pfmcom.h, pfmutil.h, pfmerr.h, pfmdbio.h 같은 공통 헤더와
      PFM_TRY/PFM_CATCH 계열 매크로 조합은 pfm이 단순 라이브러리보다 업무 모듈
      실행용 프레임워크에 가깝다는 신호다. pdb_* 헤더와 bxxx_dbio_* 함수명은
      SQL/DB 접근이 수작업 ORM이 아니라 생성형 DBIO 래퍼를 통해 이뤄짐을 시사한다.
      헤더 주석의 proframe 표기와 pfm 접두어는 공식 명칭이 ProFrame일 가능성을
      높이며, 한국어 주석과 계좌/카드/실시간인증/VAN 용어는 한국 금융권 업무
      시스템 맥락과 잘 맞는다.",
    "library_role": "C 기반의 기업형 업무처리 공통 프레임워크로, 업무 모듈
      실행, 공통 유틸리티, 에러 처리, DBIO 연계를 제공하는 TP/업무 서비스
      프레임워크로 보인다.",
    "architecture_clues": {
      "layers": {
        "a0xx": "입력값 검증/사전 점검",
        "b0xx": "메인 업무 흐름 제어",
        "b1xx": "공통코드 조회 등 DB 조회성 처리",
        "b2xx": "이력/업무 데이터 INSERT 처리",
        "b3xx": "타 업무 모듈 호출",
        "b4xx": "이력/상태 UPDATE 처리",
        "b5xx": "에러 메시지 조합/가공",
        "b7xx": "대외/업무 규칙 체크"
      },
      "pattern": "절차적 서비스 모듈 + 생성형 DBIO를 사용하는 계층형 TP 아키텍처"
    },
    "db_access_style": "DBIO 코드 생성 방식",
    "evidence_used": [
      "header_comment", "includes", "typedefs", "naming_convention",
      "macros", "api_functions", "error_keywords", "utility_prefixes",
      "domain_terms", "language"
    ]
  }
}
```

**LLM 이 사전 지식 없이** 코드만 보고:
- 프레임워크 이름 ("ProFrame")
- 역할 ("DB 접근 + TP")
- 아키텍처 (a/b 레이어 매핑까지 정확히)
- DB 접근 방식 ("DBIO 코드 생성")
- 지역 추정 ("Korea financial sector")

전부 추론.

---

## 6. 학습된 캐시 (Self-Learning)

LLM 이 한번 발견하면 `skills/learned/<prefix>_<date>.json` 으로 자동 저장.
다음 실행 시 `skill_loader.load_learned_rules` 가 자동 로드하여 캐시처럼 활용.

```
첫 실행:    [코드만으로 ProFrame 추론]  →  444K 토큰, 158초
두 번째:   [캐시 매치]                  →  ?K 토큰, ?초    ← 향후 캐시 활용 로직 추가 시
```

캐시 파일 = `framework_rule.schema.json` 검증 통과한 정식 규칙. 사람이 직접 보고 수정도 가능 (`metadata.llm_reasoning` 확인 후 정제).

---

## 7. 실행하는 법

### 의존성 (최초 1회)

```bash
cd c:/uEngine/robo/robo-data-analyzer
pip install jsonschema
# (deepagents, langchain, openai 는 이미 requirements.txt 에 있음)
```

### 환경변수

`.env` 의 `LLM_API_KEY` 필요.

```bash
python -c "from config.settings import settings; print('OK' if settings.llm.api_key else 'MISSING')"
```

### 비교 실행

```bash
# 두 방법 + 모든 타겟
python -m test.codebase_detection_comparison.run_comparison

# 특정 타겟
python -m test.codebase_detection_comparison.run_comparison --target ProFrame-C
python -m test.codebase_detection_comparison.run_comparison --target robo-data-analyzer-self

# 한쪽만 (디버깅)
python -m test.codebase_detection_comparison.run_comparison --only deep
python -m test.codebase_detection_comparison.run_comparison --only naive
```

### 로그 확인

```
logs/run_<timestamp>_<target>.log   ← 사람용 텍스트 (도구 호출 시퀀스 전체)
logs/run_<timestamp>_<target>.json  ← 프로그램용 (자동화·통계 분석)
```

---

## 8. 확장 가이드

### 새 언어 추가 (예: Rust)

```bash
# 1. 확장자 매핑
# rules/ext_lang.json 에 ".rs": "Rust" 추가

# 2. Skill 폴더
mkdir -p skills/detect_rust
cat > skills/detect_rust/meta.json << 'EOF'
{
  "$schema": "../../schemas/skill_meta.schema.json",
  "language_names": ["Rust"],
  "source_extensions": [".rs"],
  "config_patterns": ["**/Cargo.toml"],
  "import_regex": "^\\s*use\\s+([\\w:]+)"
}
EOF

# 3. 탐색 매뉴얼 (선택)
cat > skills/detect_rust/SKILL.md << 'EOF'
# detect_rust — Rust 탐색 매뉴얼

## 1순위: Cargo.toml
- [dependencies] 블록의 각 줄

## 호기심 탐색
- `use foo::bar` 가 자주 보이면 find_definition(BarStruct, '.rs')
- macro_rules! 정의는 프레임워크 단서
EOF
```

**끝.** Python 코드 0줄 수정. 다음 실행부터 Rust 탐색 가능.

### 새 표준 라이브러리 등록 (오탐 방지)

`rules/known_prefixes.json` 의 해당 언어 섹션에 추가:

```json
{
  "rust_standard": ["std", "tokio", "actix_web", "diesel", "your_new_crate"]
}
```

### 학습 캐시 정제

`skills/learned/*.json` 을 직접 열어 `metadata.llm_reasoning` 확인 후
`name`, `evidence.imports`, `patterns` 를 사람이 정제 가능. JSON Schema 검증되므로 안전.

---

## 🎓 한 줄 요약

> **"Skill 은 캐시이지 정답이 아니다."**
>
> 표준 라이브러리 화이트리스트만 갖고도, IDE 의 'Go to Definition' 흐름으로 코드를
> 따라가며 LLM 이 ProFrame 같은 미지의 프레임워크를 이름·역할·아키텍처까지 추론한다.
> 발견한 결과는 자동 학습되어 다음 실행의 캐시가 된다.
