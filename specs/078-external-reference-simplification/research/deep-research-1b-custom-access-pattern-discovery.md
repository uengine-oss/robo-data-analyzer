# 정답지 없이 레거시 코드의 커스텀 DB 접근·호출 패턴을 자동 발견하는 방법

## TL;DR
1. **결정론적 sink-reachability(표준 DB API에서 역방향 호출그래프 전파)를 1차 필터로 삼고, 약한 오픈모델은 "이 함수가 SQL을 다른 함수에 위임하는가? 예/아니오+근거라인" 수준의 좁은 이진 검증만 맡기며, 불확실 경계 사례만 사람에게 올리는 3층 파이프라인**이 우리 제약(오프라인·약한 모델·정밀도 우선)에 가장 잘 맞는다. "스킬 파일"은 이 파이프라인이 자동 생성·축적하는 CodeQL "models-as-data" 형태의 패턴 사전으로 대체 가능하다.
2. 이 접근의 근본 한계는 **간접 호출(함수 포인터·문자열 동적 디스패치·C 매크로)**이다. 실증적으로 리플렉션/동적 호출의 이름 문자열은 평균 약 절반만 상수로 해석되고(Li/Tan/Xue TOSEM 2019: 전체 리플렉션 콜사이트의 49%가 문자열 상수), 타입 기반 간접호출 해석은 재현율은 높지만(약 96~97%) 정밀도가 약 절반(F1 59~64%)에 그친다. 따라서 "해석 불가"를 **UNVERIFIED로 남기는 selective prediction**이 성능 저하가 아니라 품질 지표가 되도록 평가를 설계해야 한다.
3. 벤더 자체 주장(CAST Imaging의 "150+ 기술 커버")과 독립 검증(간접호출·리플렉션 누락률 실증치)은 명확히 구분해야 하며, 클라우드 LLM 전제 솔루션(예: LLM 요약 기반 SEA)은 우리 환경에서 Qwen 30B급 로컬 모델로 재해석해 "후보 필터"로만 쓰는 것이 안전하다.

## Key Findings

### 1. 결정론적 자동 발견은 성숙했으나 간접성에서 무너진다
- **Sink-reachability(역방향 도달성)**는 표준 기법이다. 알려진 최하위 DB 진입점(JDBC, ODBC/OCI, embedded SQL `EXEC SQL`, cursor API)을 sink로 두고 호출그래프를 역전파하면 "DB 도달 가능 함수 집합"을 얻는다. Joern(코드 프로퍼티 그래프, CPG)의 `reachableByFlows`, CodeQL의 dataflow가 이를 구현한다. 우리는 이미 Neo4j에 CALLS/READS/WRITES 그래프가 있으므로 Cypher 역방향 순회로 직접 구현 가능하다.
- **핵심 한계는 간접 호출**이다. C 함수 포인터 기반 간접호출은 타입/시그니처 매칭만으로는 정밀도가 낮다. MLTA/TypeDive(Lu & Hu, "Where Does It Go? Refining Indirect-Call Targets with Multi-Layer Type Analysis", CCS 2019 Best Paper)는 단일계층 타입분석(FLTA) 대비 간접호출 타깃을 "86% to 98% more ... without introducing new false negatives"로 더 제거했으나, 여전히 Linux 커널에서 icall당 최대 1,914개 타깃이 남는다(FLTA에서는 최대 7,983개). SEA(arXiv 2408.04344) 벤치마크에서 순수 타입 매칭의 정밀도는 약 49~55%, 재현율은 약 96~97%(FLTA F1 59.0, MLTA 61.3, KELP 63.7)다. 즉 타입 매칭이 잡는 후보의 절반가량이 오탐이다.
- KELP(USENIX Security 2023)는 def-use 체인 기반 지역 포인터 분석으로 "단순" 함수 포인터를 먼저 정밀 해석(평균 32.0%가 고유하게 확정 가능)한 뒤 나머지에 타입 분석을 적용해 MLTA 타깃을 23.2% 추가 축소했다 — 우리의 def-use 상수 전파 설계에 직접적인 참고다.

### 2. 문자열 이름 기반 동적 호출은 절반만 잡힌다
- Li, Tan, Xue "Understanding and Analyzing Java Reflection"(ACM TOSEM 28(2), Art.7, DOI 10.1145/3295739)는 16개 프로그램의 1,423개 리플렉션 콜사이트를 조사해, 진입 메서드(forName/loadClass)의 문자열 인자 중 34.7%만 상수, 멤버 조회 메서드(getMethod/getField)는 63.1%가 상수이며 **전체 평균 49%만 문자열 상수로 해석 가능**("On average, only 49% reflective call sites ... use string constants")하다고 보고했다. 나머지는 Unknown(설정파일·명령행·네트워크에서 유입) — 진입 메서드 55.3%, 멤버조회 25.1%. 또한 `newInstance()` 콜사이트의 28%는 캐스트 기반 타입 추론 트릭조차 실패한다.
- Landman, Serebrenik, Vinju(ICSE 2017, DOI 10.1109/ICSE.2017.53, 461개 프로젝트, Distinguished Paper)는 **"reflection can not be ignored for 78% of the projects"**라고 보고했다.
- 실무 결론: def-use 상수 전파로 **단순 상수/단순 연결(`"get" + name`)까지만** 결정론적으로 해석하고, 계산된 이름(외부 입력·암호화·복잡한 문자열 조작)은 포기하고 UNVERIFIED로 남긴 뒤 사람/LLM 검증으로 넘기는 것이 정밀도 우선 원칙에 부합한다.

### 3. C 매크로는 island grammar/부분 파싱으로 우회
- 전처리 없이 매크로를 다루는 검증된 계열은 **island grammar / fuzzy parsing**(Moonen "Generating Robust Parsers using Island Grammars" WCRE 2001; Koppler의 fuzzy parsing)과 Padioleau의 "Parsing C/C++ Code without Pre-processing"(yacfe, Coccinelle 기반)이다. 후자는 매크로를 확장하지 않고 AST와 문법을 확장해 매크로 호출을 관용구로 인식·보존한다.
- 실용안: (a) 컴파일 가능하면 `gcc -E`(혹은 `-fdirectives-only`로 부분 전처리)한 산출물을 병행 분석, (b) 불가하면 `#define`을 스캔해 "함수형 매크로가 DB 진입 함수를 감싸는가"를 텍스트 수준 후보로 올린 뒤 LLM 이진 검증. Joern은 fuzzy parsing으로 불완전 코드도 파싱한다. 주의: 매크로 자동 탐지의 정밀도 수치는 문헌상 정성적 서술뿐이라 근거가 약하다.

### 4. 상용 도구의 "패턴 사전"은 데이터 확장(models-as-data) 포맷
- **CodeQL "models-as-data"**: 프레임워크의 DB 접근 함수를 YAML 튜플로 기술한다. 예: `["Mysql2::Client", "Method[query].Argument[0]", "sql-injection"]`(타입/경로/종류). C/C++·Java·Python·Go·Ruby·JS 모두 `sourceModel`/`sinkModel`/`summaryModel` 확장 술어를 노출하고, `addsTo`로 팩에 주입되며 union semantics로 병합·중복제거된다. 이것이 우리가 만들 "고객별 패턴 사전"의 가장 좋은 참고 포맷이다 — 사람이 읽고, 병합되며, 버전 관리·컨테이너 레지스트리 배포 가능.
- **Joern semantics**: 외부 메서드의 taint 전파 규칙을 `.semantics` 파일로 지정(예: `org.springframework.jdbc.core.JdbcTemplate.*`를 sink로). 정의 없는 외부 메서드는 기본적으로 모든 인자→모든 인자(및 반환값) 전파(soundness 우선, 오탐 증가)하므로 커스텀 semantics로 정밀도를 회복한다.
- **CAST**: Universal Analyzer 프레임워크 + AIP Extension SDK로 커스텀 어댑터를 만들고, SQL Analyzer/Data Column Access 확장이 클라이언트 코드(COBOL 등)→Column 간 accessRead/accessWrite 링크를 생성한다(예: `Update table1 set col1 = :data1`에서 data1이 col1을 accessWrite). 다만 CAST의 "150+ 기술 지원"은 벤더 자체 주장이며 커스텀 래퍼 자동 발견 정밀도 수치는 공개돼 있지 않다(근거 약함).

### 5. 약한 모델일수록 "좁은 이진 판정 + 다수결 + 선택적 사람 검토"가 유리
- 소형 모델은 열린 생성에서 약하지만 반복적 증거조건부 예/아니오 판정에는 적합하다는 관측이 있다(arXiv 2607.01773: "the decisions required ... are ... repeated, evidence conditioned YES/NO judgments"). 단, 9B 미만 일부 모델은 이진 분류에서 constant-prediction baseline조차 못 넘는 사례가 보고돼(예: Qwen-3-4B-Instruct가 일부 데이터셋에서 baseline 이하) 태스크별 실측이 필수다.
- 자기일관성(Wang et al., "Self-Consistency Improves Chain of Thought Reasoning in Language Models", arXiv:2203.11171, ICLR 2023)은 다수결로 CoT 정확도를 크게 올린다: GSM8K +17.9%(56.5%→74.4%, 40샘플), SVAMP +11.0%, AQuA +12.2%, StrategyQA +6.4%, ARC-challenge +3.9%.
- SURE 파이프라인(MDPI, "Towards Reliable LLM Grading Through Self-Consistency and Selective Human Review"): 반복 프롬프트(각 답변 20회 채점) + 불확실도 플래깅 + 저신뢰 사례만 사람 재검토로 "높은 정확도, 적은 작업량"을 달성 — 우리 active learning/uncertainty sampling 설계의 직접 사례다. 오픈소스 gpt-oss-20b 포함 검증.
- SEA(Cheng et al., arXiv:2408.04344, ASE 2024)는 결정론 분석(타입/포인터)이 후보를 만들고 LLM은 caller-callee 이진 매칭만 수행해 "improve the F1 score of ... FLTA by up to 24%"; "in cases where only FLTA can perform the analysis, SEA improves its F1 score from 38% to 67%". **정확히 우리가 원하는 "결정론 후보 → LLM 좁은 검증" 분해의 실증**이다. 특히 로컬 오픈모델 재해석에 결정적인 수치로, **qwen 모델 사용 시** F1이 "from 34.8% to between 58.5% and 59.4%, with a maximum recall loss of only 1.5%"로 오르며 최적 세팅은 "qwen model at a temperature setting of 0.5"였다. 즉 우리 Qwen 30B급으로도 후보 필터 역할은 재현 가능하되, 대형/클라우드 대비 절대 성능은 하향됨을 전제해야 한다.

### 6. 부트스트랩·전이
- **부트스트랩**: PR-Miner(빈발 아이템셋), MUDetect(빈발 서브그래프, arXiv 2204.09945) 등 명세 마이닝은 "코드베이스에서 빈발 호출 패턴 = 관행"으로 후보를 만든다. 위임비율·fan-in·시그니처 전달 같은 구조 신호로 래퍼 후보를 랭킹하고, 확정된 소수를 나머지에 전파한다.
- **전이**: 고객 A의 확정 사전을 B에 이식할 때 프레임워크 공통 부분(JDBC 래퍼 관행)은 잘 전이되나 사내 명명·매크로 관행은 실패하기 쉽다. Cpam(cross-project frequency)처럼 in-project/cross-project 빈도를 분리 관리해 "이식 가능(일반)" vs "고객 고유"를 태깅해야 한다.

## Details

### 기법별 비교표

| 기법 | 출처 | 실증 수치 | 우리 제약 적합성 | 도입 난이도 |
|---|---|---|---|---|
| Sink-reachability 역방향 전파 | Joern CPG(`reachableByFlows`); CodeQL dataflow | Joern은 정의 없는 외부 메서드를 전인자 전파로 처리(soundness 우선, 오탐↑) | 높음. Neo4j 그래프에 Cypher로 직접 구현 가능. 결정론이라 정밀도 우선에 부합 | 낮음(그래프 이미 보유) |
| 타입 기반 간접호출 해석(FLTA/MLTA/KELP) | Lu & Hu CCS 2019; KELP USENIX Sec 2023; SEA arXiv 2408.04344 | MLTA는 FLTA 대비 86~98% 타깃 제거, 그래도 icall당 최대 1,914 타깃; 타입매칭 정밀도 49~55%/재현율 96~97%(F1 59~64%); KELP 단순포인터 32% 고유 해석 | 중간. 재현율↑ 정밀도↓이므로 후보생성용, 확정은 검증 필요 | 중간(C 포인터 분석 필요) |
| 문자열 상수 전파(def-use) 동적호출 해석 | Li/Tan/Xue TOSEM 2019(10.1145/3295739); Landman ICSE 2017(10.1109/ICSE.2017.53) | 전체 리플렉션 콜사이트 49%만 문자열 상수(진입 34.7%/멤버 63.1%); 78% 프로젝트에서 리플렉션 무시 불가 | 중간. 단순 상수/연결까지만, 계산된 이름은 UNVERIFIED | 중간 |
| C 매크로 무전처리/부분전처리 | Padioleau yacfe(Coccinelle); Moonen island grammar WCRE 2001 | 정성적(정밀도 수치 공개 적음, 근거 약함) | 중간. 부분전처리 병행이 현실적 | 중간~높음 |
| 명세/패턴 마이닝(빈발 패턴) | PR-Miner; MUDetect(arXiv 2204.09945) | 빈발=정상 가정은 오탐 유발(문헌 다수 지적) | 중간. 후보 랭킹용, 확정 아님 | 중간 |
| LLM 좁은 이진 검증 + 자기일관성 | Wang et al. arXiv:2203.11171; SEA arXiv 2408.04344 | 자기일관성 GSM8K +17.9%(56.5→74.4); SEA는 FLTA F1 +24%p, qwen 34.8→58.5~59.4% | 높음(단 로컬 Qwen 재현 시 하향). 좁은 질문에 국한 필수 | 중간 |
| Selective prediction(미확인 유지) | Geifman & El-Yaniv(SelectiveNet); Chow rule(1970) | risk-coverage tradeoff 정형화 | 매우 높음. "정밀도 우선·오탐0" 철학과 정확히 일치 | 낮음(임계값 튜닝) |
| Active learning/uncertainty sampling | Settles; SURE(MDPI) | SURE: 저신뢰만 사람 재검토로 정확도↑ 작업량↓ | 높음. 사람 확정 최소화 | 중간 |
| 패턴 사전 포맷(models-as-data) | CodeQL 데이터 확장; Joern semantics | YAML 튜플, union semantics, 팩 배포 | 매우 높음. 스킬 파일 대체 자산 | 낮음 |

### 최종 권고 아키텍처 (4단계)

**1단계 — 결정론 후보 생성 (정답지 불필요)**
- 표준 최하위 DB 진입점 목록(언어별 고정: JDBC `execute*`/`prepareStatement`, OCI/ODBC, `EXEC SQL`, PL/SQL cursor, psycopg 등)을 seed sink로 등록. 이 목록은 표준 API라 고객 무관하게 재사용 가능 — 스킬의 "정답지" 부분을 여기로 흡수한다.
- Neo4j CALLS 그래프에서 **역방향 도달성**을 Cypher로 계산 → "DB 도달 가능 함수 집합" 확보.
- 래퍼 후보를 **구조 신호**로 랭킹: (a) 위임비율(본문이 거의 다른 호출로만 구성), (b) 시그니처 전달(인자·반환 그대로 전달), (c) fan-in(많은 곳에서 호출), (d) DB 도달집합과의 인접성.
- 간접성 처리: C 함수 포인터는 타입 매칭(FLTA/MLTA류)으로 후보 과생성; 문자열 동적 호출은 def-use 상수 전파로 상수/단순연결만 해석; 매크로는 부분전처리(`gcc -E`) 병행 또는 island-grammar 후보. **해석 불가는 전부 UNVERIFIED 태그.**

**2단계 — LLM 좁은 검증 (Qwen 30B 로컬)**
- 각 후보에 대해 **닫힌 이진 질문**만 던진다: "다음 함수 본문이 SQL 실행을 다른 함수에 위임하는가? 예/아니오. 근거가 된 호출 라인 번호를 인용하라." 코드 스니펫은 함수 본문+피호출 시그니처로 국한(SEA의 컨텍스트 DB 방식).
- **자기일관성**: 동일 질문을 온도>0(SEA 실증상 qwen은 0.5가 최적)로 N회(예: 5회) 샘플 + 교차 프롬프트(질문 표현 변형) → 다수결. 만장일치=고신뢰, 분열=저신뢰.
- 근거 라인이 실제 해당 함수 범위 안에 있는지 결정론적으로 재검증(환각 필터). EUC-KR/한글 식별자·주석은 UTF-8 정규화(CP949 슈퍼셋 주의) 후 프롬프트에 투입(모지바케 방지).

**3단계 — 사람 확정 (경계 사례만)**
- uncertainty sampling: LLM 다수결이 분열됐거나 근거 라인 재검증에 실패한 사례만 사람 큐에 올린다(SURE 패턴). 나머지 고신뢰 판정은 자동 승인.
- 사람 판정은 곧바로 4단계 사전에 기록되어 재질의 불필요.

**4단계 — 패턴 사전 축적 (models-as-data)**
- 확정된 래퍼/진입점/동적 디스패처를 **CodeQL 데이터 확장 유사 YAML 튜플**로 저장: `[함수풀네임, 경로(Argument[n]), 종류(db-read/db-write/wrapper), 근거파일:라인, 신뢰도, in-project/cross-project 태그]`.
- 다음 실행 시 이 사전을 seed sink에 병합(union) → 같은 코드베이스 나머지에 자동 전파(부트스트랩). cross-project 태그가 붙은 항목만 신규 고객 B에 후보로 이식하고 반드시 2단계 재검증.

### 평가 방법론
- **3축 지표**: 정밀도(승인 판정 중 옳은 비율, 오탐0 목표), 재현율(실제 DB 접근 함수 중 발견 비율), **미확인율(UNVERIFIED coverage)**.
- selective prediction의 **risk-coverage 곡선**을 채택(El-Yaniv & Wiener의 risk-coverage 정형화, Geifman & El-Yaniv SelectiveNet): 커버리지(자동 판정 비율)를 낮추면 위험(오탐)이 낮아진다. "모르면 미확인"을 **coverage를 의도적으로 낮춰 risk를 0으로 유지하는 정상 동작**으로 평가 — 미확인율 자체는 벌점이 아니라 사람 검토 예산 지표.
- 회귀 테스트용 소규모 골드셋(사람이 확정한 함수 수백 개)으로 임계값(자기일관성 다수결 비율, 신뢰도 컷)을 튜닝.

## Recommendations
1. **즉시(1~2주)**: 표준 DB 진입점 seed 목록을 언어별로 고정하고 Neo4j 역방향 도달성 Cypher 쿼리를 구현. 이것만으로 "표준 API 직접 호출" 케이스는 결정론적으로 100% 커버되고 스킬 파일의 상당 부분이 불필요해진다. 벤치마크: 기존 스킬로 잡던 진입점의 재발견율.
2. **단기(1~2개월)**: 위임비율·fan-in 구조 신호로 래퍼 후보 생성기를 붙이고, Qwen 30B에 좁은 이진 검증 + 5회 자기일관성(온도 0.5)을 붙인다. 벤치마크: 골드셋 대비 정밀도 ≥ 0.98(오탐 거의 0), 미확인율은 허용. 정밀도가 0.98 미만이면 커버리지를 더 낮춰(컷 상향) 미확인으로 돌린다.
3. **중기(3~6개월)**: models-as-data YAML 사전을 자산화하고 uncertainty sampling 사람 큐를 운영. 부트스트랩(같은 코드베이스 전파)을 먼저 안정화한 뒤에 cross-project 전이를 시도. 벤치마크: 신규 고객에서 사람이 새로 써야 하는 스킬 라인 수의 감소율.
4. **간접성 R&D(병행)**: C 매크로 부분전처리 파이프라인과 함수포인터 타입매칭을 후보생성에만 한정. 문자열 계산 이름은 처음부터 UNVERIFIED로 두고 재현율 목표에서 제외(정밀도 우선). KELP식 def-use 우선 해석으로 단순 포인터(~32%)만 결정론 확정.
- **임계값 변경 트리거**: 골드셋 정밀도 < 0.98 → 신뢰도 컷 상향(미확인↑). 사람 큐가 과부하 → uncertainty 임계 완화(자동 승인↑, 단 정밀도 모니터). 신규 고객 전이 오탐 발생 → cross-project 태그 항목 강등. 특정 태스크에서 Qwen이 이진판정 baseline을 못 넘음 → 그 태스크 전부 사람 큐로 우회.

## 함정 목록 (전형적 실패)
1. **간접 호출 과신**: 타입 매칭은 재현율만 높고 정밀도 절반(F1 59~64%). 이를 확정으로 쓰면 오탐 폭증. 반드시 후보로만.
2. **계산된 문자열 이름 무리한 해석**: 리플렉션 이름의 절반은 상수 아님(진입 메서드는 55.3%가 Unknown). 억지 해석은 오탐·환각. 포기(UNVERIFIED)가 정답.
3. **약한 모델에 열린 과제 부여**: "코드베이스 훑어 패턴 정교화"는 소형 모델이 취약. 반드시 닫힌 이진 질문으로 분해.
4. **LLM 근거 라인 환각**: 인용 라인이 함수 범위 밖이거나 존재하지 않을 수 있음 → 결정론적 재검증 필수.
5. **EUC-KR 모지바케**: 한글 식별자·주석이 깨지면 LLM 판정 붕괴. 파이프라인 입구에서 인코딩 정규화(CP949 슈퍼셋 유의).
6. **빈발=정상 가정의 오탐**: 명세 마이닝은 소수 정상 패턴을 이상으로 오판. 랭킹 신호로만.
7. **cross-project 과이식**: 고객 A의 사내 명명·매크로 관행을 B에 그대로 적용하면 실패. 공통(프레임워크) 부분만 이식하고 재검증.
8. **Joern 기본 semantics의 과전파**: 정의 없는 외부 메서드 전인자 전파로 오탐. 커스텀 semantics 필수.
9. **미확인율을 성능 저하로 오독**: 미확인을 줄이려 억지 판정하면 오탐0 원칙 붕괴. risk-coverage로 재정의.
10. **매크로 전처리 산출물만 분석**: 전처리하면 원본 파일:라인 근거가 소실 → 근거 추적성 상실. 원본↔전처리 매핑 유지 필요.

## Caveats
- **벤더 주장 vs 독립 검증**: CAST Imaging "150+ 기술/언어 지원"과 "reverse-engineers inner workings"는 벤더 자체 주장(근거 약함)이며 커스텀 래퍼 자동 발견 정밀도 수치는 미공개. 반면 간접호출·리플렉션 누락률(Lu&Hu CCS 2019, Li/Tan/Xue TOSEM 2019, Landman ICSE 2017)은 동료심사 실증치다.
- **클라우드 전제 솔루션 재해석**: SEA와 대부분의 self-consistency 실증은 클라우드/대형 모델 기반이다. 단 SEA는 qwen 로컬 모델로도 F1 34.8→58.5~59.4%(재현율 손실 ≤1.5%)를 보고했으므로 후보 필터 역할은 재현 가능하되 절대 성능 하향을 전제하고, 좁은 이진 검증 + 자기일관성 + selective prediction 조합으로 보수적으로 운용하며 골드셋으로 실측할 것. 소형 모델이 이진 판정에서 constant-prediction baseline을 못 넘기면(일부 <9B 모델 사례) 해당 태스크는 사람 큐로 우회.
- Landman ICSE 2017의 세부 문자열 조작(비상수·문자열 연결) 비율은 원문 PDF 접근 제약으로 확정 인용 못 함(78% 프로젝트 수치만 확정).
- C 매크로 무전처리 파싱의 정밀도/재현율은 문헌상 정량 수치가 희소해 근거가 약하다.
- 여기 제시한 정밀도 목표(≥0.98)와 자기일관성 N=5·온도 0.5는 출발점 권고이며 골드셋 튜닝으로 확정해야 한다.