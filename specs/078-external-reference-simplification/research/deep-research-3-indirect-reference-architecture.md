# Deep Research 3 — 코드↔외부 아티팩트 참조 연결의 범용 아키텍처 (2026-07-22)

> **보존 한계**: 사용자가 전달한 원본 첨부(`compass_artifact_wf-4b2d85c6-…`)가 인코딩 손상
> 상태로 도착해 byte-exact 보존이 불가능했다. 아래는 **의미 보존 복원본**이며, 인용된
> 영문 원문 문구는 손상되지 않은 부분에서 그대로 옮겼다. 원본 파일이 확보되면 교체한다.
> (같은 사유로 deep-research-1/2도 복원본으로 보관 중 — `README.md` 참조)

## TL;DR

- **하이브리드가 정답이다.** 범용 리터럴-키 매칭을 버리지는 말되, 그 위에 ① 컴파일러/타입
  기반 심볼 해석, ② 프레임워크별 규약 플러그인, ③ 제한된 결정론적 상수·문자열-결합
  데이터플로우를 얹은 **3층 구조**로 가야 한다.
- 순수 리터럴 매칭은 CAST가 "accurate client server links"를 놓으면 나오는 **grep 티어**와
  동일하며, CAST 자신이 이를 *"their authenticity cannot be absolutely determined by CAST"*
  링크로 격리한다 → **FP0 목표와 근본적으로 상충**한다.
- **간접 참조 사다리의 안전 경계 = "단일 컴파일 단위 내에서 effectively-final 상수의
  결정론적 폴딩으로 단일 확정 문자열이 나오는 지점까지."** 직접 리터럴 → 같은 파일 내
  final 상수 → 컴파일타임 상수 문자열 결합까지는 FP0 유지 가능. 그 이상(가변 상수,
  콜-파라미터 다홉)은 범위 폭발·정밀도 붕괴 지점이므로 **기본은 미연결**, 프레임워크
  규약이 명시적으로 뒷받침할 때만 예외.
- **선례는 부분적으로 존재한다.** CAST(프레임워크 확장 + grep 폴백 2층),
  OpenRewrite/Moderne LST(컴파일러 타입 + 서식 보존 + "no false positives"),
  IDE(IntelliJ Spring/MyBatis, Spring Tools LS)가 각각 조각을 구현. 그러나
  "코드→외부 조각을 FP0·span보존으로 그래프화하고 LLM 컨텍스트로 쓰는" **통합 제품화는
  신규 영역**이며, 선례들의 전략을 조합해야 한다.

## Key Findings

1. **리터럴-키 매칭은 프로덕션 품질의 1차 전략이 아니다.** CAST는 "파싱+해석(resolution)"으로
   만든 정확 링크와, 문자열 grep으로 만든 "dynamic/not sure" 링크를 명시적으로 분리한다.
   CAST 문서: Reference Finder는 *"less selective than parser based technology used for other
   links traced by the analyzer"*이며 *"detects a reference to an object wherever its name is
   mentioned, regardless of the context… As a result, incorrect links may be traced if a string
   happens to match a given name even if it is not logically related to the corresponding
   object."* **우리 현재 접근이 정확히 이 grep 티어에 해당한다.**
2. **최신 도구 대부분은 FP0이 아니라 반대(recall/soundness) 우선 — 단 두 부류가 예외.**
   CodeQL·Infer류 SAST는 soundness를 위해 과대근사한다(실증: 대규모 프로젝트 NPD 탐지에서
   95%+ false alarm 보고). 반면 ① OpenRewrite/Moderne LST는 타입 기반 매칭으로
   *"100% accurate method matching — No false positives from similar-looking code"*를 표방하고,
   ② IDE 프레임워크 지원(IntelliJ Spring facet, MyBatis 플러그인)은 사용자에게 보여주는
   것이라 정밀도를 극도로 중시한다. **우리 FP0 목표의 롤모델은 이 두 부류다.**
3. **결정론적 상수 전파의 안전 경계는 "불변성"과 "가시성"이다.** Semgrep은 intra-procedural
   상수 전파를 하되 `public` 필드는 *"doesn't propagate its value to other classes on the
   assumption that it could have mutated"*, `private`이며 한 번만 대입되면
   *"Semgrep assumes it is immutable"*로 전파한다. CodeQL도 *"Local data flow is easier,
   faster, and more precise than global data flow"*이며 global은 *"may report spurious flows
   that cannot in fact happen"*이라 명시.
4. **콜체인 무한 추적은 범위 폭발의 정의 그 자체.** Semgrep taint는 단일 파일 CFG로 제한
   (*"only carries out single-file analysis, so the control flow graph never exceeds the size
   of a file"*). CAST Inference Engine도 컨텍스트 추적 깊이를 늘리면 *"impacts both accuracy
   and performance"*라 명시. **한 수 아래로 자르는 것이 업계 관행.**
5. **span 글자 보존의 정본은 OpenRewrite LST와 SCIP.** LST는 format-preserving으로 공백·주석까지
   보존해 *"the tree can be printed out to reconstitute the original source code without
   clobbering formatting"*. SCIP/Glean은 정의·참조 위치를 정수 범위(range) 사실로 저장.

## (1) 언어 × 프레임워크 × 참조유형 × 정적 해결가능성

분류: **(a)** 정적 해결 가능(코드가 키를 직접 명명, 심볼/상수 해석으로 결정론 도달) ·
**(b)** 프레임워크 매개(코드에 참조가 안 보이고 규약/애노테이션/명명관례로만 성립) ·
**(c)** 런타임 동적(외부 입력·리플렉션·문자열 조립).

| 언어 | 프레임워크/관용구 | 참조 대상 | 유형 | 레거시 비중(추정) | FP0 연결 가능성 |
|---|---|---|---|---|---|
| Java | Spring `getBean("id")` | XML `<bean id>` | (a) 리터럴이면 직접 | 높음 | 높음 |
| Java | `@Value("${key}")` | properties/yml 키 | (a) 애노테이션 내 리터럴 | 높음 | 높음 |
| Java | `@Autowired`/컴포넌트 스캔 | 타입/스테레오타입 | (b) 프레임워크 매개 | 매우 높음 | 타입해석 필요, 규약 플러그인 |
| Java | MyBatis/iBatis `selectOne("ns.id")` | 매퍼 XML `namespace+id` | (a) 문자열 / (b) 매퍼 인터페이스 | 높음 | 높음(네임스페이스+id 결합) |
| Java | JPA/Hibernate named query | 애노테이션/XML | (a)/(b) | 중간 | 중간~높음 |
| Java | JNDI `lookup("java:comp/…")` | 배포서술자/서버 설정 | (a) 리터럴 / (c) 상수 조립 | 중간(EJB 레거시 높음) | 리터럴만 높음 |
| Java | `ResourceBundle.getString(key)` | .properties 키 | (a) 리터럴 / (c) 변수 | 높음 | 리터럴만; `@PropertyKey` 있으면 향상 |
| Python | Django `reverse("name")`/URLconf | `urls.py` name= | (a) 리터럴 | 높음 | 높음 |
| Python | Flask `url_for("endpoint")` | 라우트 함수명/데코레이터 | (a)/(b) | 중간 | 높음 |
| Python | SQLAlchemy/Django ORM | 모델 클래스↔테이블 | (b) 규약(`__tablename__`) | 높음 | 규약 플러그인 필요 |
| Python | `config[…]`/settings | settings.py/env | (a)/(c) | 높음 | 리터럴 키만 |
| C/레거시 | embedded SQL(EXEC SQL) | DB 스키마/테이블 | (a) 파싱 | 높음 | 높음(SQL 파싱) |
| C/레거시 | config 문자열/`getenv` | .conf/env | (a)/(c) | 높음 | 리터럴만 |
| JS/TS | DI 토큰(Angular/Nest) | provider 등록 | (b) 타입/토큰 | 중간 | 타입해석 필요 |
| JS/TS | 라우팅 문자열 | 라우트 테이블 | (a) 리터럴 | 중간 | 높음 |
| COBOL | CICS `LINK/XCTL PROGRAM(x)` | PPT/프로그램 | (a) 리터럴 / (c) 변수 MOVE | 매우 높음 | 리터럴만; 변수경유는 낮음 |
| COBOL | `COPY` 카피북 | 카피북 멤버 | (a) 직접 명명 | 매우 높음 | 매우 높음 |
| DBMS | 저장프로시저 호출 | 다른 프로시저/테이블 | (a) 카탈로그/파싱 | 높음 | 높음(SQL 카탈로그) |

**핵심 관찰**: 레거시에서 가장 흔한 두 부류는 **(a) 리터럴 직접 명명**(우리가 이미 잡는 것)과
**(b) 프레임워크 매개**(우리가 놓치는 것)다. (c)는 원리상 FP0으로 해결 불가하므로 미연결이
정답. CAST의 MyBatis 확장이 좋은 참고: 매퍼 쿼리를 파싱해 연결하되
*"Links to corresponding database tables can also be resolved, provided that the SQL database
has been extracted and DDL has been created"* — 즉 **대상 아티팩트가 존재·색인될 때만**
결정론적 링크를 만든다.

## (2) 간접 참조 해결 사다리 — 권고 경계와 단계별 FP 위험

| 단계 | 예시 | 결정론? | FP 위험 | 권고 |
|---|---|---|---|---|
| 0. 직접 리터럴 | `getBean("orderService")` | 예 | 거의 0 | **항상 연결** |
| 1. 같은 파일 내 불변 상수 1홉 | `static final String X="orderService"; getBean(X)` | 예 | 매우 낮음 | **연결** |
| 2. 상수 문자열 결합(전부 컴파일타임 상수) | `PREFIX + "BeanName"` (둘 다 final) | 예 | 낮음 | **연결** — 모든 피연산자가 상수로 폴딩될 때만 |
| 3. 다른 클래스/전역 상수 | `Constants.JNDI_X` | 조건부 | 중간 | **조건부 연결**: 상수가 `final`·재대입 없음이 확인될 때만. 가변이면 미연결(Semgrep 관행) |
| 4. 콜 파라미터 1홉 우회 | `f(x){getBean(x)}`, `f("id")` | 조건부 | 중간~높음 | **프레임워크 규약이 뒷받침할 때만**; 순수 범용에선 1홉까지 |
| 5. 콜 파라미터 다홉/컬렉션/조건분기 | 값이 여러 함수·필드를 경유 | 아니오 | 높음 | **미연결**(범위 폭발·정밀도 붕괴) |
| 6. 런타임 조립/외부입력/리플렉션 | `getBean(request.getParam())` | 아니오 | 판정 불가 | **미연결** |

**원칙적 경계**: "단일 컴파일 단위 내에서 effectively-final 상수의 결정론적 상수 폴딩으로
**단일 확정 문자열**이 나오는 지점까지"가 범용 층의 안전선(단계 0~2, 조건부 3). 그 이상은
반드시 프레임워크 규약 플러그인이 명시적 근거를 제공할 때만. 이 경계는 Semgrep(단일 파일)과
CodeQL(local이 global보다 정밀)의 공통 관행과 일치.

## (3) 권고 아키텍처 — 3층 하이브리드

**Layer 0 — 심볼/타입 해석 기반** (SCIP/LSIF 또는 컴파일러 LST)
- 모든 리터럴·상수·심볼을 **span과 함께 사실(fact)로 색인**. OpenRewrite LST 또는 SCIP를 모델로.
- 이 층이 상수 정의·가시성·final 여부·타입을 제공 → 사다리 하위의 결정론 기반.
- OpenRewrite처럼 **타입 미해결 시 조용히 실패하지 말고 명시적 미해결(Quarks) 처리** →
  미해결은 미연결로 귀결되어 FP0 유지.

**Layer 1 — 결정론적 상수·문자열 데이터플로우** (범위 제한)
- intra-procedural + effectively-final 상수까지만. Semgrep의 "가변·public은 전파 안 함" 규칙 채택.
- 문자열 결합은 **모든 피연산자가 컴파일타임 상수로 폴딩될 때만** 확정 문자열 산출.
- 이론적 근거는 문자열 분석(Christensen/Møller/Schwartzbach, *Precise analysis of string
  expressions*, SAS'03)이나, 그 기법은 **과대근사(복수 후보)**를 낳으므로 우리는
  **"단일 확정 값"만 채택**(복수 후보 = FP 위험이므로 배제). 타임아웃·상한 차단 필수.

**Layer 2 — 프레임워크 규약 플러그인** (테이블 링킹과 동일 패러다임 확장)
- 프레임워크별 "참조 문법"을 아는 결정론 리졸버: Spring bean id, MyBatis namespace+id,
  Django url name, JPA, JNDI, ResourceBundle 등.
- 직접 선례: CAST의 프레임워크별 확장(MyBatis/Hibernate/JDBC/Entity Framework/NHibernate),
  IntelliJ의 facet/DOM 기반 리졸버(`SpringBeanResolveConverter`, `@RequiredBeanType`).
- 각 플러그인은 **화이트리스트 방식**(아는 관용구만) + "값이 상수로 해결될 때만 링크".

**LLM의 역할 (제안자, 승인 아님)**
- 로컬 GPU LLM은 ① 새 프레임워크 관용구 후보 발굴, ② 어느 인자가 키인지 가설 제시,
  ③ 규약 플러그인 초안 생성에만. 실제 링크는 항상 Layer 0~2의 결정론 검증기가 승인.
- 이는 neuro-symbolic 계열(LLMSA/NESA — 구문·파싱은 결정론, 의미만 LLM 제안 후 Datalog
  제약으로 환각 억제)과 ZeroFalse(*"deterministic prompts, schema-constrained outputs,
  and a complete audit trail"*)가 검증한 구조와 일치.

**span 보존**: 모든 링크는 **소스 측 span**(참조 표현식의 문자 범위)과 **아티팩트 측 span**
(키/조각의 문자 범위)을 **둘 다** 저장. LST/SCIP의 range 인코딩 채택.

## (4) 선례 / 참고 구현

- **CAST Imaging/AIP** — 2층(프레임워크 확장 정확 링크 + grep "dynamic" 폴백)의 가장 성숙한
  상용 선례. 불확실 링크를 버리지 않고 별도 티어로 격리·검토(Automatic Links Validator).
  **훔칠 전략**: 티어 분리. 단 우리는 FP0이므로 그 티어를 "그래프에 넣지 않음"으로 처리.
- **OpenRewrite / Moderne LST** — 컴파일러 타입 어트리뷰션(javac/tsc 해석 재사용) + 서식 보존
  + *"type-attributed, so it can accurately find and update code—with no false positives.
  Furthermore, it is format preserving."* **훔칠 전략**: ① 컴파일러 해석 재사용,
  ② 타입 미해결 시 조용히 실패하지 말고 명시적 미연결(Quarks), ③ 서식/span 보존.
- **IntelliJ IDEA Ultimate (Spring/MyBatis)** — facet/fileset으로 애플리케이션 컨텍스트를
  모델링해 bean/property/MyBatis id를 정확 내비게이션. **훔칠 전략**: 참조를
  "reference contributor + resolver"로 모듈화하는 플러그인 아키텍처.
- **Spring Tools 4 (language server)** — bean/property 심볼 인덱싱을 별도 인덱스로 유지,
  XML·properties 스캔 범위 제한. **훔칠 전략**: 심볼 인덱스 분리 + 스캔 범위 제한.
- **Glean (Meta)** — 스키마 정의 fact DB + Angle(Datalog류) 질의, LSIF/SCIP 수용, 커스텀
  predicate로 도메인 사실 확장, 증분 색인. **훔칠 전략**: 커스텀 predicate로
  "코드→아티팩트 참조" 관계를 1급 사실로 모델링.
- **학술**: JSA(문자열 표현식 정밀 분석), LLMSA/NESA(neuro-symbolic 정적 분석),
  ZeroFalse(LLM으로 FP 감축), MODISCO/KDM(OMG 지식발견 메타모델). arXiv 1906.00937은
  JEE 레거시가 *"rely on external configuration files to link the pieces together, and rely
  on frameworks and containers during runtime"*임을 명시 — 우리 문제의 정확한 배경.

## Recommendations (단계별)

**Stage 1 (즉시, 낮은 위험) — 사다리 0~2 결정론 층 구축**
- 현재 리터럴 매칭 위에 심볼/상수 해석 층(Layer 0)을 깔고, 같은 파일 내 effectively-final
  상수 1홉과 컴파일타임 상수 문자열 결합까지 확장.
- 규칙: **값이 단일 확정 문자열로 폴딩될 때만 연결.** 가변·재대입 상수는 미연결.
- 벤치마크: 골든셋에서 **FP=0 유지하며 recall 상승** 확인. FP가 하나라도 나오면 그 규칙 롤백.

**Stage 2 (단기) — 프레임워크 규약 플러그인 도입 (Layer 2)**
- 레거시 비중 높은 순: Spring bean id/`@Value`, MyBatis namespace+id, JNDI, ResourceBundle,
  Django url name.
- 각 플러그인은 화이트리스트 방식. 벤치마크: **프레임워크별 정밀도 100%를 게이트**로.
  안 맞으면 미연결.

**Stage 3 (중기) — LLM 제안 + 결정론 승인 루프**
- 로컬 GPU LLM으로 미커버 관용구/후보 인자 제안 → 규약 플러그인 초안 → 사람 검토 →
  결정론 검증기가 최종 링크. ZeroFalse의 결정론적 프롬프트·스키마 제약·감사추적 적용.

**임계값 (권고를 바꾸는 지점)**
- 타임아웃 초과 / 값이 복수 후보로 갈라짐 / 피연산자 중 하나라도 비상수 → **즉시 미연결**.
- 어떤 규칙이든 실측 FP > 0이면 **그 규칙 비활성화**(FP0 > recall 원칙).
- 콜체인 추적은 범용 1홉, 프레임워크 규약 하에서만 제한적 확장.
- 대상 아티팩트(스키마/DDL/설정)가 색인되지 않았으면 **링크 시도 자체를 보류**(CAST 관행).

## Caveats

- 표의 "레거시 비중"과 일부 "FP0 연결 가능성" 평가는 문헌·도구 문서 기반 **추정**이며
  코드베이스마다 다르다. 실측으로 보정해야 한다.
- CAST가 명시적 수치("zero FP") 보증을 공표하지는 않는다. 이들의 정밀도는 불확실 링크를
  별도 버킷으로 격리·검토하는 운영 방식으로 달성된다.
- OpenRewrite의 "no false positives"는 **전체 컴파일 클래스패스가 제공될 때** 성립한다.
  레거시에서 클래스패스가 불완전하면 타입 미해결(Quarks)이 되어 recall이 떨어진다 —
  FP0은 유지되되 미연결이 증가.
- 문자열 분석(JSA류)은 원래 정확성 유지를 위해 **과대근사**를 산출한다. 우리는 FP0을 위해
  "단일 확정 값"만 채택하므로, 이 이론을 그대로 쓰면 안 되고 확정 케이스만 필터링해야 한다.
- "코드→외부 아티팩트를 FP0·span보존으로 그래프화하고 LLM 컨텍스트로 주입"하는 **통합
  제품화의 완전한 선례는 확인되지 않았다**. 조각별 선례는 풍부하나 통합은 신규 영역으로
  판단(추정).
