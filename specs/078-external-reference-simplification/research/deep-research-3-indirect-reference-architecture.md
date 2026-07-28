# 코드↔외부 아티팩트 참조 연결의 범용 아키텍처 — 권고안

## TL;DR
- **하이브리드가 정답이다.** 범용 리터럴-키 매칭을 버리지 말되, 그 위에 (1) 컴파일러/타입 기반 심볼 해석, (2) 프레임워크별 규약 플러그인, (3) 제한된 결정론적 상수·문자열-결합 데이터흐름을 얹은 3층 구조로 가야 한다. 순수 리터럴 매칭은 CAST가 "accurate client server links"를 끄면 나오는 grep 기반 검색과 동일하며, CAST 자신이 이를 "their authenticity cannot be absolutely determined by CAST"(진위를 절대적으로 확정할 수 없음) 링크로 격리한다 — 즉 FP0 목표와 근본적으로 상충한다.
- **간접 참조 사다리의 안전 경계는 "단일 컴파일 단위 내에서 불변(effectively-final) 상수의 결정론적 폴딩까지"이다.** 직접 리터럴 → 같은 파일 내 final 상수 → 상수 문자열 결합까지는 FP0을 유지할 수 있다. 다른 클래스의 상수(가시성이 public이면 변경 가능성 때문에 Semgrep도 전파를 포기)와 콜-파라미터 다홉 우회는 범위 폭발·정밀도 붕괴가 시작되는 지점이므로, 기본은 "미연결"이 정답이고 프레임워크 규약이 명시적으로 뒷받침할 때만 예외적으로 연결한다.
- **선례는 존재하나 부분적이다.** CAST(프레임워크 확장 + grep 폴백 2층), OpenRewrite/Moderne LST(컴파일러 정확 + 글자 보존 + "no false positives"), IDE(IntelliJ Spring/MyBatis, Spring Tools LS)가 각각 조각을 구현했다. 하지만 "코드↔외부 조각을 FP0·span보존으로 그래프화하고 LLM 컨텍스트로 쓰는" 통합 정형화는 신규 영역이며, 위 선례에서 전략을 조합해야 한다.

## Key Findings

1. **리터럴-키 매칭은 프레임의 하한선이지 정답이 아니다.** CAST의 설계가 결정적 증거다. CAST는 "파싱+해석(resolution)"으로 만든 정확 링크와, 문자열 grep으로 만든 "dynamic/not sure" 링크를 명시적으로 분리한다. CAST 문서는 Reference Finder가 "less selective than parser based technology used for other links traced by the analyzer"이며, "detects a reference to an object wherever its name is mentioned, regardless of the context... As a result, incorrect links may be traced if a string happens to match a given name even if it is not logically related to the corresponding object"라고 직접 경고한다. 우리의 현재 접근은 정확히 이 grep 티어에 해당한다.

2. **최신 도구들은 "FP0 우선"이 아니라 대부분 반대(recall/soundness 우선)다 — 단 두 부류는 예외.** CodeQL·Infer 같은 SAST는 soundness(무결성=FN0)를 위해 과대근사한다. 실증 연구(arXiv 2601.18844)는 "state-of-the-art open-source SATs (e.g., CodeQL and Infer) exhibit over 95% false alarm rates when detecting Null Pointer Dereference (NPD) bugs in large-scale projects such as the Linux Kernel"라고 보고한다. 반면 (a) OpenRewrite/Moderne LST는 타입 기반 매칭으로 "100% accurate method matching – No false positives from similar-looking code"를 표방하고, (b) IDE 프레임워크 지원(IntelliJ Spring facet, MyBatis 플러그인)은 사용자에게 보여주는 것이라 정밀도를 극도로 중시한다. 우리 FP0 목표에는 이 두 부류가 롤모델이다.

3. **결정론적 상수 전파의 안전 경계는 "불변성"과 "가시성"이다.** Semgrep은 intra-procedural 상수 전파를 하되, `public` 필드는 "doesn't propagate its value to other classes on the assumption that it could have mutated"이고 `private`이며 한 번만 대입되면 "Semgrep assumes it is immutable"라 전파한다 — 이것이 FP0을 지키는 실무적 휴리스틱의 좋은 예다. CodeQL은 "Local data flow is easier, faster, and more precise than global data flow"이며 global 데이터흐름은 "may report spurious flows that cannot in fact happen"이라고 명시한다.

4. **콜체인 무한 추적은 범위 폭발의 정의 그 자체다.** Semgrep은 taint 분석을 단일 파일 CFG로 제한("only carries out single-file analysis, so the control flow graph never exceeds the size of a file")함으로써 성능·정밀도를 지킨다. CAST 메인프레임 분석기는 "Inference Engine"의 콜패스 추적 깊이가 조율 대상이며 깊이를 늘리면 "impacts both accuracy and performance"라고 명시한다. 즉 홉 수 상한은 업계 표준 관행이다.

5. **span 글자 보존의 정본은 OpenRewrite LST와 SCIP다.** LST는 "format-preserving"으로 공백·주석까지 보존해 "the tree can be printed out to reconstitute the original source code without clobbering formatting", 검색 시 span을 훼손하지 않는 마커(`~~>`)만 삽입한다. SCIP/Glean은 definitions·references의 위치를 정수 범위(range)로 저장하는 사실(fact) 기반 그래프다.

## Details

### (1) 언어 × 프레임워크 × 참조유형 × 정적해결가능성 매트릭스

해결가능성 분류: **(a) 정적해결 가능** = 코드가 키를 직접 명명, 심볼/상수 해석으로 결정론적 도달. **(b) 프레임워크 매개** = 코드에 참조가 안 보이고 규약/애노테이션/명명관례로만 성립. **(c) 런타임 동적** = 값이 실행 시점에만 결정(외부 입력, 리플렉션, 문자열 조립).

| 언어 | 프레임워크/관용구 | 참조 대상 | 유형 | 레거시 비중(추정) | FP0 연결 가능성 |
|---|---|---|---|---|---|
| Java | Spring XML `getBean("id")` | XML `<bean id>` | (a) 리터럴이면 직접 | 높음 | 높음(id 리터럴 직접 매칭) |
| Java | Spring `@Value("${key}")` | properties/yml 키 | (a) 애노테이션 내 리터럴 | 높음 | 높음 |
| Java | `@Autowired`/컴포넌트 스캔 | 타입/스테레오타입 | (b) 프레임워크 매개 | 매우 높음 | 타입해석 필요, 규약 플러그인 |
| Java | MyBatis/iBatis `selectOne("ns.id")` | 매퍼 XML `namespace+id` | (a) 문자열 / (b) 매퍼 인터페이스 | 높음 | 높음(네임스페이스+id 결합) |
| Java | JPA/Hibernate named query | 애노테이션/XML | (a)/(b) | 중간 | 중간~높음 |
| Java | JNDI `lookup("java:comp/...")` | 배포서술자/서버 설정 | (a) 리터럴 / (c) 상수 조립 | 중간(EJB 레거시 높음) | 리터럴만 높음 |
| Java | `ResourceBundle.getString(key)` | .properties 키 | (a) 리터럴 / (c) 변수 | 높음 | 리터럴만; `@PropertyKey` 있으면 향상 |
| Python | Django `reverse("name")`/URLconf | `urls.py` name= | (a) 리터럴 | 높음 | 높음 |
| Python | Flask `url_for("endpoint")` | 라우트 함수명/데코레이터 | (a)/(b) | 중간 | 높음 |
| Python | SQLAlchemy/Django ORM | 모델 클래스↔테이블 | (b) 규약(`__tablename__`) | 높음 | 규약 플러그인 필요 |
| Python | `config[...]`/settings | settings.py/env | (a)/(c) | 높음 | 리터럴 키만 |
| C/레거시 | embedded SQL(EXEC SQL) | DB 스키마/테이블 | (a) 파싱 | 높음 | 높음(SQL 파서) |
| C/레거시 | config 문자열/`getenv` | .conf/env | (a)/(c) | 높음 | 리터럴만 |
| JS/TS | DI 토큰(Angular/Nest) | provider 등록 | (b) 타입/토큰 | 중간 | 타입해석 필요 |
| JS/TS | 라우팅 문자열 | 라우트 테이블 | (a) 리터럴 | 중간 | 높음 |
| COBOL | CICS `LINK/XCTL PROGRAM(x)` | PPT/프로그램 | (a) 리터럴 / (c) 변수 MOVE | 매우 높음 | 리터럴만; 변수경유는 낮음 |
| COBOL | `COPY` 카피북 | 카피북 멤버 | (a) 직접 명명 | 매우 높음 | 매우 높음 |
| DBMS | 저장프로시저 호출 | 다른 프로시저/테이블 | (a) 카탈로그/파싱 | 높음 | 높음(SQL 카탈로그) |

핵심 관찰: 레거시에서 가장 흔한 두 부류는 **(a) 리터럴 직접 명명**(우리가 이미 잡는 것)과 **(b) 프레임워크 매개**(우리가 놓치는 것)이다. (c) 런타임 동적은 원리상 FP0으로 해결 불가하므로 미연결이 정답이다. CAST의 MyBatis 확장이 좋은 참고: 매퍼 쿼리를 파싱해 연결하되 "Links to corresponding database tables can also be resolved, provided that the SQL database has been extracted and DDL has been created" — 즉 대상 아티팩트(스키마)가 존재할 때만 결정론적 링크를 만든다.

### (2) 간접 참조 해결 사다리 — 권고 경계와 단계별 FP 위험

| 사다리 단계 | 예시 | 결정론? | FP 위험 | 권고 |
|---|---|---|---|---|
| 0. 직접 리터럴 | `getBean("orderService")` | 예 | 거의 0 | **항상 연결** |
| 1. 같은 파일 내 불변 상수 1홉 | `static final String X="orderService"; getBean(X)` | 예 | 매우 낮음 | **연결** |
| 2. 상수 문자열 결합(전부 컴파일타임 상수) | `PREFIX + "BeanName"` (둘 다 final) | 예 | 낮음 | **연결**(모든 피연산자가 상수로 폴딩될 때만) |
| 3. 다른 클래스/전역 상수 | `Constants.JNDI_X` (외부 클래스) | 조건부 | 중간 | **조건부 연결**: 상수가 `private`/`final`이고 재대입 없을 때만. `public` 가변이면 미연결(Semgrep 관행) |
| 4. 콜 파라미터 1홉 우회 | `f(x){ getBean(x) }`, `f("id")` | 조건부 | 중간~높음 | **프레임워크 규약이 뒷받침할 때만**; 순수 범용에서는 1홉까지만 |
| 5. 콜 파라미터 다홉/컬렉션/조건분기 | 값이 여러 함수·필드를 경유 | 아니오 | 높음 | **미연결**(범위 폭발·정밀도 붕괴 시작점) |
| 6. 런타임 조립/외부입력/리플렉션 | `getBean(request.getParam())` | 아니오 | 확정 불가 | **미연결** |

**원칙적 경계**: "단일 컴파일 단위 내에서 effectively-final 상수의 결정론적 상수 폴딩으로 완전한 문자열이 나오는 지점까지"가 범용 층의 안전선(단계 0–2, 조건부 3). 그 이상은 반드시 프레임워크 규약 플러그인이 명시적 근거를 제공할 때만 넘어간다. 홉 수는 하드 상한(권고: 범용 1홉, 프레임워크 규약 하에서만 제한적 확장)을 두어 콜체인 무한 추적을 금지한다. 이 경계는 Semgrep(단일 파일)과 CodeQL(local이 global보다 정밀)의 공통 관행과 일치한다.

### (3) 권고 아키텍처: 3층 하이브리드 (패턴 인식 + 심볼 해석 + 데이터흐름)

결론: **순수 범용 리터럴 매칭도, 순수 프레임워크 패턴 인식도 아닌 하이브리드.** 근거는 (i) 프레임워크별 규약을 알면 (b)유형(코드에 안 보이는 참조)을 잡을 수 있고 우리는 이미 테이블 링킹에 규약을 쓰고 있으며, (ii) 컴파일러/타입 해석은 OpenRewrite처럼 FP0에 가장 근접한 방법이고, (iii) 결정론적 상수 폴딩은 간접성의 대부분을 안전하게 흡수한다.

**Layer 0 — 심볼/타입 해석 기반 (SCIP/LSIF 또는 컴파일러 LST)**
- 모든 리터럴·상수·심볼을 정확 위치(span)와 함께 사실(fact)로 색인. OpenRewrite LST 또는 SCIP를 모델로.
- 이 층이 상수 정의, 가시성, final 여부, 타입을 제공 → 사다리 판정의 결정론적 기반.
- OpenRewrite처럼 "타입 미해결 시 조용히 저하되지 말고 명시적 미해결(Quarks) 처리" — 미해결은 미연결로 귀결되어 FP0을 지킨다.

**Layer 1 — 결정론적 상수·문자열 데이터흐름 (범위 제한)**
- intra-procedural(단일 함수) + effectively-final 크로스-함수 상수까지만. Semgrep의 "private만 전파" 규칙을 채택.
- 문자열 결합은 모든 피연산자가 컴파일타임 상수로 폴딩될 때만 완전 문자열 산출. Christensen/Møller류 문자열 분석(JSA, "Precise analysis of string expressions", SAS'03)이 이론적 근거지만, 우리는 FSA 과대근사가 아니라 "단일 확정값"만 채택(과대근사=복수 후보=FP 위험이므로 배제).
- 홉 상한·순환 차단 필수.

**Layer 2 — 프레임워크 규약 플러그인 (테이블 링킹과 동일 패러다임 확장)**
- 프레임워크별 "참조 문법"을 아는 결정론적 리졸버: Spring bean id, MyBatis namespace+id, Django url name, JPA, JNDI, ResourceBundle 등.
- CAST의 프레임워크별 확장(MyBatis/Hibernate/JDBC 등 개별 extension)과 IntelliJ의 facet/DOM 기반 리졸버(`SpringBeanResolveConverter`, `@RequiredBeanType`)가 직접 선례.
- 각 플러그인은 "이 관용구에서 이 인자가 이 아티팩트의 이 키를 가리킨다"는 규약을 인코딩하고, 값이 상수로 해결될 때만 링크.

**LLM의 역할 (제안자, 승인 아님)**
- 로컬 GPU LLM은 (i) 새 프레임워크 관용구 후보 발굴, (ii) 어느 인자가 키인지 가설 제시, (iii) 규약 플러그인 초안 생성에만 사용. 실제 링크는 항상 Layer 0–2의 결정론적 검증기가 승인. 이는 LLMSA/NESA(neuro-symbolic: Datalog 제약으로 구문은 파싱이 확정, 의미만 LLM 제안하여 환각 억제)와 ZeroFalse("deterministic prompts, schema-constrained outputs, and a complete audit trail"로 FP 억제)가 검증한 구조와 일치.

**span 보존**: 모든 링크는 소스 측 span(참조 표현식의 문자 범위)과 아티팩트 측 span(키/조각의 문자 범위)을 둘 다 저장. LST/SCIP의 range 인코딩을 채택.

### (4) 선례/참고 구현

- **CAST Imaging/AIP** — 2층(프레임워크 확장 정확 링크 + grep "dynamic" 폴백)의 가장 성숙한 상용 선례. "accurate client server links" 토글, "authenticity cannot be absolutely determined" 격리, 프레임워크별 데이터액세스 확장(MyBatis/Hibernate/JDBC/Entity Framework/NHibernate 등, MyBatis는 DDL 추출 시에만 테이블 링크 해결), 휴리스틱 스코어링 검증기(Automatic Links Validator). **훔칠 전략**: 불확실 링크를 버리지 말고 별도 티어로 격리하되, 우리는 FP0이므로 그 티어를 "그래프에 넣지 않음"으로 처리.
- **OpenRewrite / Moderne LST** — 컴파일러 정확 타입 어트리뷰션(JavaC/tsc의 해석 재사용) + 글자 보존 + "type-attributed, so it can accurately find and update code—with no false positives. Furthermore, it is format preserving" 표방. **훔칠 전략**: (i) 컴파일러 해석 재사용, (ii) 타입 미해결 시 조용히 저하되지 말고 명시적 실패/미연결(Quarks), (iii) 완전 격자 span 보존.
- **IntelliJ IDEA (Ultimate) Spring/MyBatis 지원** — facet/fileset로 애플리케이션 컨텍스트를 모델링해 bean/property/MyBatis id를 정확 내비게이션. **훔칠 전략**: 참조를 "reference contributor + resolver"로 모듈화하는 플러그인 아키텍처.
- **Spring Tools 4 (language server)** — bean/property 심볼 인덱싱을 별도 인덱스로 유지, XML·properties 스캔 범위 제한(출력 폴더 무시). **훔칠 전략**: 심볼 인덱스 분리와 스캔 범위 제한으로 성능·정밀도 확보.
- **Glean (Meta)** — 스키마 정의 사실(fact) DB + Angle(Datalog류) 질의, LSIF/SCIP 수용, 커스텀 predicate로 도메인 사실 확장, 증분 색인. **훔칠 전략**: 커스텀 predicate로 "코드↔아티팩트 참조" 관계를 1급 사실로 모델링.
- **학술**: JSA(Christensen-Møller-Schwartzbach, 문자열 표현식 정밀 분석), LLMSA/NESA(neuro-symbolic 정적분석, Datalog 제약으로 환각 억제), ZeroFalse(LLM으로 FP 감축). MODISCO/KDM(OMG 지식발견 메타모델, JEE 레거시의 XML config·프레임워크 의존성 통합 표현 — arXiv 1906.00937이 JEE 레거시가 "rely on external configuration files to link the pieces together, and rely on frameworks and containers during runtime"임을 명시).

## Recommendations

**Stage 1 (즉시, 낮은 위험) — 사다리 0–2 결정론 층 구축**
- 현재의 리터럴 매칭 위에 심볼/상수 해석 층(Layer 0)을 얹어, 같은 파일 내 effectively-final 상수 1홉과 컴파일타임 상수 문자열 결합까지 확장.
- 규칙: 값이 단일 확정 문자열로 폴딩될 때만 연결. 가변·public·재대입 상수는 미연결.
- 벤치마크: 알려진 참조 세트(golden set)에서 FP=0 유지하며 recall 상승 확인. FP가 하나라도 나오면 그 규칙을 롤백.

**Stage 2 (단기) — 프레임워크 규약 플러그인 도입(Layer 2)**
- 레거시 비중 높은 순서로: Spring bean id/@Value, MyBatis namespace+id, JNDI, ResourceBundle, Django url name.
- 각 플러그인은 화이트리스트 방식(아는 관용구만). CAST/IntelliJ의 프레임워크 확장 패턴을 참고.
- 벤치마크: 각 프레임워크별 정밀도 100%를 게이트로. 애매하면 미연결.

**Stage 3 (중기) — LLM 제안자 + 결정론 승인자 루프**
- 로컬 GPU LLM으로 미커버 관용구/후보 인자를 제안 → 규약 플러그인 초안 생성 → 사람이 검토 → 결정론 검증기가 최종 링크.
- ZeroFalse식 결정론적 프롬프트·스키마 제약·감사추적 적용.

**임계값(권고를 바꾸는 지점)**:
- 홉 상한 초과, 값이 복수 후보로 갈라짐, 피연산자 중 하나라도 비상수 → 자동 미연결.
- 어떤 규칙이든 실측 FP > 0이면 그 규칙 비활성화(FP0 > recall 원칙).
- 콜체인 추적은 범용 1홉, 프레임워크 규약 하에서만 제한적 확장.
- 대상 아티팩트(스키마/DDL/설정)가 색인되지 않은 경우 링크 시도 자체를 보류(CAST MyBatis 관행).

## Caveats
- 표의 "레거시 비중"과 일부 "FP0 연결 가능성" 평가는 문헌·도구 문서에 기반한 **추정**이며 코드베이스마다 다르다. 실측으로 보정해야 한다.
- CAST가 명시적 수치("zero FP") 보증을 공표하지는 않는다. 이들의 정밀도는 불확실 링크를 별도 버킷으로 격리·검토("ignore these links if they prove to be incorrect... validate these links if they prove to be correct")하는 운영 방식으로 달성된다.
- OpenRewrite의 "no false positives"는 **전체 컴파일 클래스패스가 제공될 때** 성립한다. 레거시에서 클래스패스가 불완전하면 타입 미해결(Quarks)이 늘어 recall이 떨어진다 — FP0은 유지되나 미연결이 증가.
- 문자열 분석(JSA류)은 원래 유한상태오토마타 **과대근사**를 산출한다. 우리는 FP0을 위해 "단일 확정값"만 채택하므로, 이 이론을 그대로 쓰면 안 되고 확정 케이스만 필터링해야 한다.
- "코드↔외부 아티팩트를 FP0·span보존으로 그래프화하고 LLM 컨텍스트로 주입"하는 통합 정형화의 완전한 선례는 확인되지 않았다. 조각별 선례는 풍부하나, 통합은 신규 영역으로 판단된다(추정).