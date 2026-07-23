# Deep Research 5 — "키로 외부 내용을 가져다 쓰는 참조"만 판별하는 방법 (2026-07-23)

> **보존 한계**: 사용자가 전달한 원본 첨부(`compass_artifact_wf-996ea4da-…`)가 인코딩 손상 상태로
> 도착해 byte-exact 보존이 불가능했다. 아래는 **의미 보존 복원본**이며, 인용된 영문 원문과
> 고유명사·수치는 손상되지 않은 부분에서 그대로 옮겼다. 원본 확보 시 교체한다.
> (deep-research-1~4도 같은 사유로 복원본 — `../../078-external-reference-simplification/research/README.md` 참조)
>
> **요청 프롬프트**: `D:\work\robo\docs\핸드오프\2026-07-23-키기반-외부내용-적재-참조판별-딥리서치-프롬프트.md`

## TL;DR

- **범용 신호는 없다.** 산업 표준은 예외 없이 **API 화이트리스트(호출 규약 카탈로그)** 다.
  CAST·CodeQL·Semgrep·OpenRewrite·IntelliJ/Spring·SCIP·Glean 전부가 "이 메서드/이 패턴이 리소스를
  조회한다"를 사람이 등록한 카탈로그로 판정하며, **인자 위치**를 핵심 근거로 삼는다.
- **"정의(declaration) vs 단순 언급(mention)"을 한 파일 안에서 가르는 결정론 장치는 이미 존재하고
  이식 가능하다.** SCIP의 `SymbolRole` 비트마스크(`Definition = 0x1`)가 정확히 그 문제를 푼다.
  조각(chunk)에 "이 조각이 키를 **정의**하는가"를 형식 종류별 결정론 규칙으로 부여하면
  `<bean id>`(조회 가능 키 정의)와 `<field name>`(스키마 필드 정의 — 다른 역할)이 분리된다.
- **그래프 스키마는 관계 타입을 나누는 것이 정답이다.** "내용 적재 참조"와 "이름 언급"을
  신뢰도 속성으로 섞지 말고 별도 관계 타입으로 두고, 기존 이름일치 엣지는
  GraphQL/Glean식 **병기 → 소비자 이관 → 제거** 3단계로 축소한다.
- 사내 롱테일 프레임워크는 **통계적 후보 발견 + 결정론 승인**(neuro-symbolic)으로만 다루되,
  FP 0 제약 때문에 정밀도 게이트를 **100%** 로 걸어야 한다.

## Key Findings

### 1. 산업 표준은 전부 "API 화이트리스트" — 다른 범용 신호는 존재하지 않는다 (Q1·Q2)

조사한 모든 도구가 리소스 조회 호출부를 **명시적 호출 규약 카탈로그**로 인식했다.
범용 신호(미해결 심볼 인자, 반환값 캐스팅 등)를 근거로 쓰는 도구는 **하나도 없었다.**

- **CodeQL "Models as Data"** — 소스/싱크/서머리를 QL 코드가 아닌 **데이터 확장 파일**(`.model.yml`)로
  외부화한다. 각 모델은 `[패키지, "Member[메서드].Argument[n]", 종류]` 형태의 튜플이며
  **인자 위치**가 판별의 핵심이다. 공식 문서(*Customizing library models for Java and Kotlin*):
  *"You can model the methods and callables that control data flow in any framework or library.
  This is especially useful for custom frameworks or niche libraries, that are not supported by
  the standard CodeQL libraries."* → **사내 프레임워크가 정확히 이 기능의 사용처다.**
  2024-06 CodeQL 2.17.5 릴리스노트: *"Data models can now be added with data extensions. In this
  way source, sink and summary models can be added in extension .model.yml files, rather than by
  writing classes in QL code."*
  단, CodeQL 문서는 이 기능을 여전히 **"currently in beta and subject to change"** 로 표기한다.
- **Semgrep taint mode** — `pattern-sources`/`pattern-sinks`를 사용자가 패턴으로 열거한다.
  관련 문서가 "패턴 매칭만으로는 `eval(user_input)`(취약)과 `eval("safe_literal")`(안전)을 구분할
  수 없다"고 스스로 인정 → **결정론 판별의 근본 한계를 벤더가 문서화**한 사례.
- **IntelliJ / Spring 플러그인** — XML 빈을 DOM-API로 파싱하고
  `@Convert(SpringBeanResolveConverter.class)` + `@RequiredBeanType` 으로 특정 속성값이 빈 참조로
  resolve 되도록 등록한다. IntelliLang의 *reference injection* 은 문자열 리터럴을 클래스·파일 등
  다른 엔티티 참조로 취급한다 → **어느 API의 어느 인자가 참조인지를 플러그인이 하드코딩**한다.
- **OpenRewrite** — `MethodMatcher` 가 `"org.mockito.Matchers anyVararg()"` 같은 메서드 패턴
  문자열로 호출부를 지정하고, 상속·인터페이스·오버로드까지 시그니처 기반으로 매칭한다.
  타입 귀속(type attribution)을 사용한다.
- **CAST Imaging Reference Finder** — 정규식 기반 문자열 매칭. CAST 문서 자체가
  "이 기술은 컨텍스트와 무관하게 객체 이름이 언급된 곳이면 어디든 참조를 탐지한다. 그 결과
  문자열이 이름과 우연히 일치하면 논리적으로 무관해도 잘못된 링크가 만들어질 수 있다"고 경고하며,
  "잘못된 참조를 걸러내려면 사용자가 감수해야 할 점이 있다"고 명시한다.
  → **우리가 실측한 "이름 일치만으로 연결"의 실패가 상용 도구에서도 동일하게 나타난다.**

**결론 (Q2)**: 화이트리스트 없이 동작하며 학계·산업에서 검증된 범용 판별 신호는 **문헌·구현
어디에도 없다.** 우리가 검토한 (a)~(d) 후보의 한계 판단이 정확했다.

### 2. "정의 vs 언급"을 가르는 결정론 장치 — SCIP SymbolRole (Q2)

SCIP(Sourcegraph Code Intelligence Protocol)의 `scip.proto` 는 각 occurrence(소스 범위)에
`symbol_roles` 라는 int32 비트마스크를 붙인다.

```protobuf
enum SymbolRole {
  UnspecifiedSymbolRole = 0;
  // Is the symbol defined here? If not, then this is a symbol reference.
  Definition = 0x1;
  // Is the symbol imported here?
  Import = 0x2;
  WriteAccess = 0x4;        // Is the symbol written here?
  ReadAccess = 0x8;         // Is the symbol read here?
  Generated = 0x10;         // Is the symbol in generated code?
  Test = 0x20;              // Is the symbol in test code?
  ForwardDefinition = 0x40;
}
```

판정은 완전히 결정론적이다: `(occurrence.symbol_roles & Definition) != 0` 이면 정의, 아니면 참조.
`symbol_roles` 는 occurrence 마다 붙는 단일 int32 비트열이고, occurrence 는 half-open
`[start, end)` 바이트/문자 범위에 붙는다 → **우리의 span 보존 계약과 정확히 같은 모델이다.**
Glean 도 동일하게 "정의 위치"와 "cross-reference(XRef)"를 별도 predicate 로 설계한다
(`FileEntityXRefLocations` 등).

**이식 가능성**: `semantic_kind` 만으로는 `<bean id>` 와 `<field name>` 이 둘 다 `xml_element` 라
구분 불가라는 우리 문제는, SCIP 의 **role 축**을 조각에 추가하면 풀린다.
형식 종류별로 "이 조각이 코드가 조회할 수 있는 키를 정의하는 declaration 인가"를 결정론 규칙으로
확정한다. 예:

| 조각 | 역할 |
|---|---|
| MyBatis `<select\|insert\|update\|delete id=…>` | **조회 가능 키 정의 = 참** |
| Spring `<bean id=…>` | **참** |
| properties `key=…` | **참** |
| JNDI 이름 (배포서술자 `<ejb-name>`) | **참** |
| UTL_FILE 디렉터리 별칭 | **참** |
| entitymodel `<field name=…>` · 컬럼명 | **거짓** (스키마 필드 정의 — 다른 역할) |

**핵심 통찰**: 판별을 **코드 쪽 호출 규약만이 아니라 조각 쪽 역할에도 부여**하는 것.

### 3. 사내 롱테일 프레임워크: 통계적 발견 + 결정론 승인 (Q3)

- **API 사용 패턴 마이닝 선례는 풍부하다.** MAPO, UP-Miner(Microsoft, succinctness·coverage 지표
  제시), PAM 등이 코드베이스에서 API 사용 패턴을 통계적으로 마이닝한다. 이들은 "다수 사용이 곧
  정답"이라는 가정에 기반하며, 표본이 적으면 실패한다고 명시된다.
- **neuro-symbolic(LLM 제안 + 결정론 승인) 실제 사례**: MoCQ (arXiv:2504.16057,
  *"Automated Static Vulnerability Detection via a Holistic Neuro-symbolic Approach"*, Penghui Li 외)
  는 LLM 이 취약점 탐지 패턴/쿼리를 생성하고 정적 분석(Joern·CodeQL 통합)이 trace 기반 심볼릭
  검증으로 피드백 루프를 돌려 정제한다. 최신판 초록:
  *"MoCQ uncovered 46 new vulnerability patterns that security experts had missed and discovered
  25 previously unknown vulnerabilities in real-world applications."*
  (주의: 초기판 v1/v3 에서는 *"12 patterns … seven previously unknown vulnerabilities"* 였다가
  최신판에서 46/25 로 갱신 — **수치가 판본마다 다르므로 인용 시 판본 명시 필요.**)
- **핵심 검증 장치의 정밀도 한계**: cDep (ESEC/FSE 2020, Chen·Wang·Legunsen·Li·Xu,
  doi:10.1145/3368089.3409727) 은 클라우드 시스템에서 설정 의존성을 정적 taint 분석으로 발견한다.
  초록: *"cDep finds 87.9% (275/313) of the related subset of dependencies from our study.
  cDep also finds 448 previously undocumented dependencies, with a 6.0% average false positive rate."*
  → 이 **평균 6.0% 오탐률**은 우리 FP 0 제약에는 **부적합**하다. 6% FP 는 즉시 규칙 비활성화 대상.

### 4. 그래프 스키마: 관계 타입으로 나눠라 (Q4)

- **OpenLineage**: 암묵적 데이터셋 레벨 리니지(inputs×outputs 카티전 곱)의 "의미 왜곡" 문제가
  실제 이슈(#4359)로 제기됐고, 명시적 리니지를 root-level 속성으로 도입하자는 제안이 진행 중이다.
  → **"추론된 연결"과 "명시된 연결"을 섞으면 안 된다**는 것이 리니지 커뮤니티의 학습된 교훈.
- **Apache Atlas**: Type-and-Entity 모델로 엣지의 관계 성격(join/FK 등)을 타입으로 부여한다.
  관계 종류를 타입으로 구분하는 것이 표준.
- **실무 판단**: "적재 참조"와 "이름 언급"을 **관계 타입으로 분리**(예: `LOADS_FROM` vs
  `NAME_MENTIONS`)하는 것이 신뢰도 속성 하나로 다는 것보다 우수하다.
  이유: 소비자(LLM 컨텍스트 조립)가 타입으로 필터하면 실수로 섞을 수 없다.
  속성 방식은 소비자가 임계값 필터를 잊으면 오염된다.

### 5. 계약 축소 마이그레이션: 병기 → 이관 → 제거 (Q5)

GraphQL 스키마 진화가 정확한 선례를 제공한다. Apollo/GitLab/Hasura 공통 관행:

1. `@deprecated` 지시어로 옛 엣지 종류를 표시(제거 안 함).
2. 관측 도구로 어느 소비자가 아직 옛 종류를 쓰는지 추적. GitLab 은 `?remove_deprecated=true`
   파라미터로 "제거된 미래 스키마"에 대해 소비자가 미리 테스트하게 한다.
3. 사용이 0이 되면 제거. Apollo 는 *"schema checks 가 최근 운영 트레이싱 데이터 창에 대해 변경을
   검사해 deprecated 필드 롤오버가 클라이언트 파손 없이 완료 가능한지 확인한다"* 고 명시.

### 6. 더 나은 문제 프레임 (Q6)

**"설정 의존성(configuration dependency)" 연구가 우리 문제에 가장 정확히 맞닿은 인접 프레임이다.**
cDep(ESEC/FSE 2020)과 ConfTainter(ASE 2023)가 "코드가 외부 설정 값을 읽어 동작을 바꾸는 지점"을
taint 분석으로 추적한다 → 우리의 "키로 외부 내용 적재"와 동형이다.

다만 이들도 **getter API 를 사람이 라벨링한 카탈로그**(configuration engine)에서 taint 를 시작한다고
명시한다("기존 기법은 configuration engine 에 대한 주석에 의존해 커버리지가 제한적"). 즉
**인접 프레임도 화이트리스트에서 출발한다.**

데이터 리니지 프레임(OpenLineage/Atlas)은 런타임 이벤트 기반이라 정적 분석·FP 0 제약에는 부적합하다.

## Details

**Q1 판별 근거 — 인자 위치가 축**: CodeQL 의 `Member[X].Argument[n]`, OpenRewrite 의 시그니처 패턴,
Semgrep 의 `pattern` 내 메타변수 위치 모두 **인자 위치·타입**을 근거로 삼는다.
애노테이션은 Spring `@Value`/`@RequiredBeanType` 처럼 보조 신호.
카탈로그 항목 수의 공개 수치는 **도구별로 공개 수치 없음**이나, CodeQL 은 프레임워크별 다수 모델을
커뮤니티가 유지하며 VS Code "model editor" 로 편집·검증한다.
형태는 **데이터 파일**(CodeQL yml, Semgrep yaml)이 지배적이고, OpenRewrite 는 코드+yaml 혼합,
IntelliJ 는 코드(플러그인).

**Q2 이식 결론**: SCIP SymbolRole 은 순수 데이터(int32 비트) + span 이므로 우리의
순수 Python·서브프로세스 0 제약에 **완전히 적용 가능**하다. 언어서버 불필요 —
이미 하는 조각화 단계에서 형식 종류별 결정론 규칙으로 role 비트만 세팅하면 된다.

**Q3 과적합 방지**: UP-Miner 의 succinctness/coverage 지표, held-out 검증, 반례 샘플이 필수.
FP 0 제약 하에서는 통계적 발견을 **후보 제안**으로만 쓰고, 발견된 후보 API 는 반드시
(1) 해당 문자열이 실제 조각 키와 매칭되고 (2) held-out 코퍼스에서도 정밀도 100% 일 때만 카탈로그 승격.

**Q4 계약 설계**: SCIP `is_definition` 필드, Glean 의 별도 XRef predicate 가 "생산자가 종류를
명시하고 소비자가 종류로 질의"하는 계약. 신뢰도 속성은 랭킹·UI 강조에만 쓰고 존재 여부 판정에는
쓰지 말 것.

**Q5 지표**: 축소 전후를 정밀도(승인 엣지 중 진짜 적재 참조 비율)·재현율(진짜 적재 참조 중 승인
비율)·변별력(엣지가 실제로 유용한 필터를 제공하는가)으로 평가.
우리 149건 진짜 참조(SQL 119·JNDI 17·파일 11·경로 2)를 골든셋으로 고정하라.

## Recommendations

> 아래 모든 권고는 우리 제약(FP 0, 순수 Python, 서브프로세스 0, 사내 롱테일)에 적용 가능 여부를 명시한다.

**1단계 — 그래프 스키마부터 쪼갤 것 (즉시, 적용 가능)**
`LOADS_FROM`(적재 참조)과 `NAME_MENTIONS`(이름 언급) 두 엣지 타입을 분리 도입하고,
현재 이름일치 엣지 전부를 `NAME_MENTIONS` 로 강등. LLM 컨텍스트 조립기는 `LOADS_FROM` 만
소비하도록 계약 고정.
- **중단 임계값**: 골든 149건이 `LOADS_FROM` 으로 100% 재분류되지 않으면 다음 단계 금지.

**2단계 — 조각에 SCIP 식 role 비트를 부여 (적용 가능, 순수 Python)**
형식 종류별 결정론 규칙으로 각 조각에 "조회 가능 키 정의(`is_lookup_key_definition`)" 불리언을
세팅. `LOADS_FROM` 엣지는 **조각의 `is_lookup_key_definition=참`** AND **코드 측 카탈로그 매칭**이
둘 다 성립할 때만 생성.
- **중단 임계값**: ofbiz 필드명 우연일치(`userLoginId`·`unitPrice`·`grandTotal` 등)가
  `LOADS_FROM` 에서 실측 0건이 되지 않으면 배포 금지. 1건이라도 나오면 규칙 비활성화.

**3단계 — 코드 측 카탈로그를 명시적 데이터 파일로 외부화 (적용 가능)**
CodeQL Models-as-Data 를 모방한 순수 데이터 카탈로그(예: `catalog.yaml`):
`[래퍼클래스, 메서드명, 키인자위치, 조각종류]`. 공개 프레임워크를 아래로 등록:

| 관용구 | 키 인자 |
|---|---|
| iBATIS/MyBatis `insert/selectOne(ns.id, …)` | 0번 인자 |
| Spring `getBean(id)` | 0번 인자 |
| `ResourceBundle.getString(key)` | 0번 인자 |
| JNDI `lookup(name)` | 0번 인자 |
| `UTL_FILE.FOPEN(dir, file)` | 0번(디렉터리)·1번(파일) |

- **중단 임계값**: 위 카탈로그만으로 골든 재현율 **≥ 95%** 가 안 나오면 4단계(사내 마이닝)로
  진입해 속도를 보강.

**4단계 — 사내 롱테일: 통계적 후보 + 결정론 승인 (적용 가능하나 게이트 필수)**
"이 미지 메서드의 n번 인자 문자열이 조각 키와 X% 이상 일치한다"를 통계로 발견 →
LLM(로컬 GPU)이 규약 설명 후보 제시 → 결정론 검증기가 held-out 코퍼스에서 승인.
승인 조건: **held-out 정밀도 100%** AND 반례 샘플(공백 포함 값·문장 조각) 0 오연결.
- **중단 임계값**: held-out 정밀도가 100% 미만이면 그 API 를 카탈로그에 넣지 말 것(FP 0 계약).
  MoCQ 류·cDep 의 6% FP 는 우리 기준 실패.

**5단계 — 옛 엣지 제거 (GraphQL식 마이그레이션)**
`NAME_MENTIONS` 를 deprecated 표기 → 소비자 이관 확인 → 사용 0 확인 후 제거.
- **중단 임계값**: 어떤 소비자든 `NAME_MENTIONS` 를 여전히 소비하면 제거 금지.

## Caveats

- **공개 수치와 추정 구분**: CodeQL 카탈로그 항목 수·유지 인력은 **공개 수치 없음**.
  cDep 의 6.0% 평균 FP·87.9%(275/313) 재현·448건 신규 발견, MoCQ 의 46개 신규 패턴·25개 취약점
  (최신판 기준)은 논문 공개 수치이나 **우리 도메인(한국 SI 레거시)에서의 재현 보장 없음**.
  Semgrep 의 "패턴만으로 안전/취약 구분 불가"는 벤더 자체 문서.
  CodeQL Models-as-Data 는 공식 문서에 **베타**.
- **SCIP 는 컴파일러 정밀 심볼용**: SCIP 자체는 언어 툴체인(컴파일러/타입체커) 전체 실행을 전제한다
  (대형 저장소 30~90분). 우리는 **SymbolRole 의 데이터 모델 개념만** 차용하는 것이지 SCIP 인덱서를
  도입하는 게 아니다 → 서브프로세스 0 제약 유지됨.
- **neuro-symbolic 선례의 목표 차이**: MoCQ 등은 취약점 탐지(재현율 중시)가 목표라 우리의
  FP 0(정밀도 절대우선)과 최적화 방향이 반대다. 구조(LLM 제안 + 결정론 승인)만 차용하고
  임계값은 훨씬 보수적으로. MoCQ 수치는 판본마다 다르므로 인용 시 판본 명시.
- **데이터 리니지 프레임 부적합**: OpenLineage/Atlas 는 런타임 이벤트·ETL 중심이라 정적 소스
  분석에 직접 이식 불가. 스키마 설계 교훈(관계 타입 분리, 명시 vs 추론 구분)만 유효.
- **화이트리스트의 근본 한계**: 카탈로그에 없는 사내 API 는 미연결로 남는다. 이는 우리
  "FP 0 > 재현율" 제약의 **의도된 대가**이며, 4단계 마이닝으로 점진 확장하는 것이 유일한 정공법이다.
