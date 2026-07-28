# DB 저장 프로시저의 외부 파일 참조·입출력을 지식 그래프로 연결하는 전략 — 실태 조사 및 설계 권고

## TL;DR
- **저장 프로시저의 파일 입출력은 "기능은 모든 상용 DBMS에 존재하나, 코드에 리터럴로 정적 해결 가능한 경우는 소수"**다. Oracle External Table/DIRECTORY 객체는 정적 링킹이 매우 잘 되는 고가치 타깃이고, UTL_FILE·BULK INSERT·COPY는 파일명이 날짜/업무일자로 동적 조립되는 비율이 높아 FP0(false positive 0) 원칙 하에서 상당수를 포기해야 한다.
- **앱 코드와 별도 전략이 아니라 "공통 참조-해석기(Resolver) 인터페이스 + 언어/플랫폼별 플러그인"으로 통합**하되, 그래프 스키마는 하나(코드단위 → [참조엣지] → 외부아티팩트/그 조각)로 통일하는 것이 옳다. 이것이 Glean·SCIP·OpenLineage·Atlas가 실제로 채택한 구조다.
- **투자 가치는 "선별적 高"**: External Table/DIRECTORY(DDL에 경로가 리터럴로 박힘)와 리터럴 UTL_FILE는 낮은 비용·0 FP로 연결 가능하므로 1순위. 동적 파일명, xp_cmdshell, 콜체인을 넘는 추적은 명시적으로 포기(미연결) 권고.

## Key Findings

### 1. 기능 존재 ≠ 실무 빈도 (근거 기반)
- **UTL_FILE은 "표준 관용구"이자 동시에 "쇠퇴 중"이라는 두 신호가 공존**한다. Oracle 커뮤니티 인사 Connor McDonald는 2018년 6월 20일 글 "The death of UTL_FILE"에서 첫 문장에 "I agree UTL_FILE is dead but not because of the scheduler now allowing 'sql_script' job types. I agree mostly because of Java"라고 적었으나, 같은 글의 댓글은 "There are still reasons to use utl_file. There are many more use cases ... than one individual can think of"라고 반박한다. 즉 신규 개발에서는 줄지만 **레거시에는 대량 잔존**한다는 뜻이다.
- **한국/일본 SI 관행에서 UTL_FILE 파일 입출력은 배치 프로그래밍의 표준 패턴으로 문서화**되어 있다. 일본 PL/SQL 배치 설계 사이트(oracle.tf17.net)는 "ファイル入出力の共通化"(파일 입출력의 공통화)를 배치 설계의 첫 항목으로 다루며, UTL_FILE 파라미터를 최소화한 공통 함수로 감싸는 관행을 명시한다. 한국 은행 전산 관련 글(brunch.co.kr)은 "은행 전산 시스템의 경우 오만가지 별 해괴한 배치들이 존재"하며 "데이터만 쏙쏙 뽑아 엑셀 파일로 만들어두는 배치"가 흔함을 서술한다.
- **금융권 인터페이스는 파일(전문) 기반이 주류**다. 한국 EAI/배치 문서(BX Docs, SW연구소 BXi)는 "File을 사용하는 인터페이스 유형에서 FTP 방식으로 파일을 송수신", "File to File 인터페이스 유형"을 EAI 배치의 핵심 유형으로 명시한다. 즉 프로시저가 직접 파일을 쓰기보다 **DB→테이블→EAI가 파일화**하는 흐름과, **프로시저가 UTL_FILE로 직접 파일을 생성**하는 흐름이 병존한다.

### 2. DBMS×기능×실무빈도×정적해결 매트릭스

| DBMS | 기능 | 실무 빈도(레거시) | 파일명 정적해결 가능성 | 근거·비고 |
|---|---|---|---|---|
| Oracle | **UTL_FILE** (FOPEN/GET_LINE/PUT_LINE) | **높음** (배치·리포트·audit·정산) | **중** — 리터럴도 많으나 `'rpt_'||TO_CHAR(SYSDATE,'DDMMYY')||'.csv'` 동적 조립이 지배적 | FOPEN 첫 인자=DIRECTORY 객체명(대개 리터럴), 둘째=파일명(동적 빈번). Medium/OraFAQ/AskTom 예제 다수가 SYSDATE 조립 |
| Oracle | **DIRECTORY 객체** | **높음** (UTL_FILE·외부테이블의 전제) | **매우 높음** — `CREATE DIRECTORY x AS '/path'` 경로가 DDL에 리터럴 | 물리경로가 DDL에 고정. 프로시저→디렉터리객체→물리경로 간접구조가 정적해결에 이상적 |
| Oracle | **External Table** (ORGANIZATION EXTERNAL + LOCATION) | **중~높음** (ETL·마스터적재·대량업로드) | **매우 높음** — `LOCATION('emp1.dat','emp2.dat')`이 DDL에 리터럴 | Oracle 공식 문서·Medium 프레임워크 사례. DEFAULT DIRECTORY + LOCATION 조합 정적해결 용이 |
| Oracle | **BFILE/BFILENAME** | 낮음~중 (이미지·문서 등 비정형) | 중 — `BFILENAME('DIR','img.gif')` 리터럴이나 대개 컬럼값(데이터)로 동적 | 파일 참조가 **행 데이터**에 저장되는 경우가 많아 정적해결 불가 영역 |
| Oracle | **DBMS_LOB** (LOADFROMFILE 등) | 중 | BFILE 경유 → BFILE과 동일 한계 | |
| SQL Server | **BULK INSERT** | **높음** (파일적재 표준) | **중~높음** — `BULK INSERT t FROM 'C:\data\x.csv'` 리터럴 잦음, 변수화도 존재 | MS Learn 표준. FROM 절 경로가 리터럴이면 정적해결 쉬움 |
| SQL Server | **OPENROWSET(BULK…)** | 중~높음 | 중~높음 (리터럴 경로 잦음) | `INSERT…SELECT FROM OPENROWSET(BULK 'path')` |
| SQL Server | **xp_cmdshell** | 중 (레거시, 보안상 억제) | **낮음** — 셸 명령 문자열 조립, bcp 등 외부호출 | 셸 문자열 파싱은 FP 위험 큼 → 포기 권고 |
| SQL Server | **SSIS 연계** | 높음 | 코드 밖(.dtsx) — 프로시저 텍스트만으론 불가 | 별도 아티팩트 파서 필요 |
| PostgreSQL | **COPY … FROM/TO 'file'** | 중 (신흥·마이그레이션 대상) | **높음** — 파일명 리터럴이 문법상 자연스러움 | 서버측 파일. `\copy`(클라이언트)와 구분 필요 |
| PostgreSQL | **file_fdw** (foreign table) | 낮음~중 | **매우 높음** — `OPTIONS(filename '…')`이 DDL에 리터럴 | External Table과 동형(DDL 리터럴) |
| PostgreSQL | **pg_read_file** | 낮음 | 중 | superuser/역할 제한 |
| DB2 | **IMPORT/EXPORT/LOAD** | 중~높음 (배치) | 중~높음 (파일 리터럴), 다만 대개 CLP 스크립트/db2move | 프로시저 내부보다 셸/CLP 레벨이 많음 |
| Sybase ASE | **bcp** | 중 (레거시) | 낮음 — 셸/외부 유틸, 파일명 배치변수 | 프로시저 텍스트 밖 |
| Tibero | **PSM(UTL_FILE 상당), tbLoader** | 중 (국내 공공·금융 Oracle 대체) | 중 (Oracle 관용구 계승) | Oracle 호환 지향 → UTL_FILE류 패턴 유사(유추) |
| Altibase | **파일 입출력/이관 유틸** | 중 (통신·금융 국내) | 중 | (유추) |

> **핵심 판별**: "파일명이 DDL/DML에 **리터럴**로 박히는 기능"(DIRECTORY, External Table, file_fdw, 리터럴 BULK INSERT/COPY)은 정적해결·FP0에 이상적. "파일명이 **런타임 조립**되는 기능"(동적 UTL_FILE, xp_cmdshell, bcp)은 FP0 원칙상 상당수 포기 대상.

### 3. 참조의 단위와 "조각(fragment)" 관용구
- **DB 쪽에는 앱의 `getBean("id")` 같은 "파일 안의 이름있는 조각을 키로 집는" 관용구가 사실상 없다.** UTL_FILE은 순차 라인 I/O(GET_LINE 루프)이고, External Table/COPY는 파일 **전체**를 테이블로 매핑한다. 따라서 **참조 단위는 원칙적으로 "파일 전체"**다.
- 예외적 "조각" 유사물: (a) External Table의 **컬럼/필드 매핑**(ACCESS PARAMETERS의 FIELDS)은 파일 내부 구조를 이름으로 참조 → **파일→필드**의 조각 링크 가능. (b) UTL_FILE.FCOPY의 start_line/end_line은 라인 범위 조각이나 리터럴 상수일 때만 의미. (c) BFILE은 **행 데이터**가 파일조각을 가리키므로 정적 코드분석 범위 밖.
- **간접 구조 모델링(가장 중요)**: `프로시저 → DIRECTORY객체/외부테이블(DB 1급 객체) → 물리 파일`의 2-hop을 그대로 그래프로 표현한다. DIRECTORY 객체와 External Table은 **DDL에 물리경로/파일명이 리터럴로 존재**하므로, DDL을 파싱해 "DB객체→물리파일" 엣지를 먼저 만들고, 프로시저 코드는 "코드→DB객체" 엣지만 만들면 된다. 이 분해가 결정론·FP0을 극대화한다(프로시저 텍스트에서 물리경로를 추론할 필요 없음).

### 4. 선례 — 무엇을 훔칠 것인가

**(a) CAST Imaging — 부정적 선례가 오히려 교훈.** CAST **SQL Analyzer**(PL/SQL·T-SQL 담당)의 공식 객체/링크 목록에는 **UTL_FILE·External Table·DIRECTORY로 생성되는 물리/논리 파일 노드가 존재하지 않는다.** 생성 객체는 Database/Schema/Table/View/Procedure/Function/Synonym/Database Link/DML Script File/sourceFile 등이고, 링크는 useSelect/useInsert/useUpdate/useDelete, callLink, relyonLink, referLink, inheritLink뿐이다(doc.castsoftware.com, SQL Analyzer 3.6). 반면 **CAST Mainframe(COBOL/JCL) Analyzer는 파일 객체("JCL Data Set")를 만들고 `accessOpenLink/accessReadLink/accessWriteLink/accessCloseLink` 엣지와 `PROTOTYPE` 링크로 프로그램↔파일을 연결**한다(CMS - External Files JCL 문서: "An ACCESS type link (with or without the R, W or E options) is created between the step and the file"). 즉 **CAST조차 파일-액세스 그래프 machinery는 COBOL/JCL에만 구현했고 PL/SQL에는 미구현** — 이 영역이 미개척 고가치임을 시사한다(공개 객체목록에 근거한 추론; 아래 Caveats 참조). CAST의 Database Link 모델링 방식("caller object → database link object via relyOn links, called object → caller object via Use/Call links")은 **우리의 "프로시저→DIRECTORY객체→물리파일" 간접 모델의 직접적 선례**다.

**(b) OpenLineage — 파일을 1급 Dataset 노드로 두는 표준 네이밍.** OpenLineage는 Job(변환)과 Dataset(데이터 단위)을 핵심 엔티티로 하고, Job이 input/output Dataset을 가지며 그래프를 엮는다("an OpenLineage backend learns about Datasets primarily by receiving information about Jobs"). 파일 Dataset 네이밍은 공식 명명규약(openlineage.io/docs/spec/naming, v1.50.0)에 명문화되어 있다: **로컬 파일 → Namespace=`file`, Name=`{path}`; 원격 파일 → Namespace=`file://{host}`, Name=`{path}`.** Oracle 테이블은 `oracle://{host}:{port}` / `{serviceName}.{schema}.{table} or {sid}.{schema}.{table}`, PostgreSQL은 `postgres://{host}:{port}` / `{database}.{schema}.{table}`. 표 헤더 원문: "Employing a unique naming strategy per resource ensures that the spec is followed uniformly regardless of metadata producer." **훔칠 모델: (i) 파일을 1급 노드로 승격, (ii) namespace+name으로 전역 유일 식별, (iii) 읽기/쓰기 방향은 Job의 input/output으로 표현(엣지에 방향 부여).** 특히 파일과 DB 테이블을 **동일한 Dataset 추상**으로 다루면서 스킴(scheme)만 달리하는 설계는 우리의 통합 스키마 논거를 직접 뒷받침한다.

**(c) Apache Atlas — DataSet/Process 이원 모델.** Atlas는 `Referenceable`을 상속하는 `DataSet`(hive_table, hdfs_file 등)과 `Process`(inputs[]·outputs[] 배열)로 리니지를 표현하며, "a table loaded with data in a file, a report generated from a table/view"를 대표 사례로 든다(Atlas Classification Propagation 문서 원문: "…scenarios where a dataset derives it data from other datasets - like a table loaded with data in a file, a report generated from a table/view, etc."). 관계 predicate는 DataSet→Process 입력, Process→DataSet 산출로 방향을 갖는다. **Process.inputs/outputs 배열 = 방향성 있는 읽기/쓰기 엣지**의 또 다른 검증된 설계다.

**(d) Glean / SCIP — 다언어 조직화의 정석.** Glean은 **언어마다 별도 스키마(predicate)**를 두고, 공통 질의는 `codemarkup`이라는 **언어중립 추상층을 derived predicate로 유도**한다(Glean 공식 Introduction 원문: "Note that Glean doesn't force all the data into a single schema; there can be arbitrary amounts of language-specific detail in the schema for each language. Language-neutral abstractions can be built by deriving facts using Angle"). SCIP는 **언어별 독립 indexer가 공통 protobuf 포맷**을 emit하고, SCIP-IO 같은 오케스트레이터가 언어 감지→per-language indexer 실행→**deterministic 병합**한다. 한 indexer 실패 시 성공분만 partial index로 발행한다("publishes the successful output as a partial index"). **훔칠 구조: 언어/플랫폼별 독립 추출기 + 공통 스키마로의 결정론적 병합 + 부분 실패 격리.**

### 5. 통합 vs 분리 — 결론과 아키텍처

**결론: "스키마·추상은 통합, 추출기는 분리"** — Glean/SCIP 패턴 그대로.

- **통합해야 하는 것(공통 추상)**: 그래프 스키마. 앱의 "코드→외부아티팩트"와 DB의 "프로시저→파일"은 **동형(isomorphic)**이다 — 둘 다 (CodeUnit) →[Reference edge: read|write|readwrite, resolution=exact|unresolved]→ (ExternalArtifact [+ Fragment]). 노드 타입(ExternalArtifact=File)과 엣지 방향(read/write) 설계를 나눌 이유가 없다. OpenLineage·Atlas가 파일과 테이블을 같은 DataSet 추상으로 다루는 것과 동일 논리.
- **분리해야 하는 것(추출기/Resolver 플러그인)**: PL/SQL의 UTL_FILE 관용구, T-SQL의 BULK INSERT, DDL(DIRECTORY/External Table) 파싱은 각기 문법·규약이 완전히 다르다. Java의 getBean과 한 코드로 묶으면 유지보수 지옥이 된다.

**권고 아키텍처 (모듈·인터페이스 수준, 1안)**

```
core/
  graph_schema        # 언어중립 노드/엣지 정의 (CodeUnit, ExternalArtifact, Fragment, ReferenceEdge{dir, resolution, span})
  resolver_api        # trait ReferenceResolver
  registry            # 언어/플랫폼 → resolver 등록 (plugin registry)
  merger              # per-resolver 산출 fact를 결정론적 병합 (SCIP-IO식, 부분실패 격리)
  verifier            # 결정론 검증기: LLM 제안을 승인/기각 (span 대조, 리터럴 여부 확인)

resolvers/
  plsql/
    utl_file_resolver        # FOPEN 리터럴 인자만 해결, 동적조립은 unresolved 마킹
    directory_ddl_resolver   # CREATE DIRECTORY → (DirObject)→(File) 엣지
    external_table_resolver  # ORGANIZATION EXTERNAL LOCATION → (ExtTable)→(File[]), FIELDS→Fragment
  tsql/
    bulk_insert_resolver     # BULK INSERT / OPENROWSET(BULK) 리터럴 FROM 경로
  postgres/
    copy_resolver
    file_fdw_resolver        # OPTIONS(filename) DDL
  app_java/  app_python/     # 기존 3층 하이브리드 (심볼/타입 → 상수 데이터플로우 → 프레임워크 규약)
```

- **ReferenceResolver 인터페이스(개념)**: `resolve(ast_node, symbol_table) -> Option<Reference{artifact_id, fragment?, direction, span, confidence=EXACT_ONLY}>`. 반환은 **리터럴로 확정 가능할 때만** Some, 그 외 None(미연결). LLM은 후보만 제안하고 verifier가 원문 span과 리터럴 여부를 대조해 승인 — 결정론·FP0 보장.
- **가장 깨끗한 패턴**: 플러그인 **레지스트리 + 방문자(Visitor)**의 조합. 언어별 AST 방문자가 관용구를 인식해 resolver를 호출하고, resolver는 순수 함수(부작용 없음)로 fact를 반환, merger가 병합. 규칙 테이블(rule table)은 "UTL_FILE.FOPEN의 2번째 인자가 파일명"류 **관용구 카탈로그**를 데이터로 외부화하는 데 사용(프레임워크 규약 플러그인과 동형). Glean의 "언어별 스키마 + derived predicate로 언어중립층 유도"가 이 조합의 검증된 원형이다.

## Details

**정적 해결의 실효 범위 (동적 파일명 문제).** UTL_FILE 예제의 압도적 다수가 `v_file_name := 'User_Details_Report_' || TO_CHAR(SYSDATE,'DDMMYYHH24MI') || '.csv';` 형태로 파일명을 **런타임 조립**한다(Medium·OraFAQ·AskTom 예제 일관). 이는 앱 코드 리서치의 "제한된 상수·문자열 데이터플로우"층으로도 **부분만** 해결된다(접두사 'User_Details_Report_'는 상수, 날짜부는 런타임). FP0 원칙 하에서 선택지는 둘: (1) 완전 리터럴만 물리파일에 연결, (2) 부분 상수는 "파일 **패턴/디렉터리**"에만 연결하고 구체 파일은 미연결. **권고: (2)의 절충 — DIRECTORY 객체(경로 확정)까지는 연결하고, 그 안의 구체 파일명이 동적이면 "이 프로시저는 이 디렉터리에 write한다"는 디렉터리 수준 엣지까지만 확정.** 이는 0 FP를 지키면서 LLM 컨텍스트에 "어느 디렉터리의 인터페이스 규약 파일을 봐야 하는지"를 제공하는 실용적 타협이다.

**External Table/DIRECTORY가 최고 가치인 이유.** 이 둘은 파일 경로/이름이 **DDL 텍스트에 리터럴로 존재**한다(`CREATE DIRECTORY d AS '/appl/gl/log'`, `LOCATION('empxt1.dat','empxt2.dat')`). DDL은 프로시저와 별개 아티팩트로 카탈로그(ALL_DIRECTORIES, 외부테이블 메타데이터 등)에도 존재. 따라서 **런타임 추론 0, 문자열 조립 0, FP 0**로 "DB객체→물리파일" 엣지를 만들 수 있고, 프로시저는 이 DB객체를 심볼로 참조(SELECT FROM ext_table, FOPEN('D',...))하므로 심볼해석층(1층)만으로 "프로시저→DB객체" 연결이 끝난다. **2-hop 합성으로 프로시저→물리파일이 결정론적으로 완성된다.** ora2pg가 마이그레이션 시 DIRECTORY를 "external table 또는 external_file extension"으로 별도 변환 항목으로 두는 것도 이 객체가 파일 참조의 명확한 앵커임을 방증한다.

**span 보존.** 각 Reference 엣지는 트리거가 된 원문 토큰의 (start,end) 바이트 오프셋을 보존해야 한다(FOPEN 인자 리터럴의 정확한 위치, LOCATION 리스트 각 파일명의 위치). SCIP의 range 인코딩(`[startLine,startChar,endChar]` 3요소 또는 4요소, 인덱스 페이로드 ~50% 절감을 위해 채택)과 동일 원칙을 적용한다.

**콜체인 무한추적 금지와의 정합.** 파일명이 상위 프로시저 파라미터로 전달되는 경우(예: `pr_write(p_dir, p_file)`) 무한 역추적하면 FP·비용 폭증. **권고: 같은 컴파일 단위(패키지) 내 1-hop 상수전파까지만 허용, 그 이상은 unresolved.** 이는 앱 리서치의 "제한된 데이터플로우"와 동일한 경계이며, 일본 배치 관행(UTL_FILE을 공통 함수로 감싸 파라미터로 dir/file을 넘기는 패턴)이 흔하다는 점에서 1-hop 역전파는 실무적으로 중요하다 — 공통 wrapper 호출부에서 리터럴 인자를 잡아야 실효 recall이 확보되기 때문이다.

## Recommendations

**1단계 (즉시, 최고 ROI — 0 FP·저비용):**
- Oracle **DIRECTORY 객체 DDL 파서** + **External Table LOCATION 파서** 구현. `(DBObject)→(File)` 엣지를 카탈로그/DDL에서 결정론적으로 생성. External Table의 FIELDS를 Fragment 노드로.
- PostgreSQL **file_fdw OPTIONS(filename)** 파서(동형).
- 완전 리터럴 **UTL_FILE.FOPEN / BULK INSERT FROM / COPY FROM|TO** 만 물리파일에 연결. 같은 패키지 내 wrapper 호출부의 리터럴 인자까지 1-hop 전파 허용.
- **벤치마크(단계 진행 임계값)**: 대상 레거시 샘플에서 이 4개 리졸버로 잡히는 파일참조 비율 측정. 파일 I/O를 수행하는 프로시저 중 **리터럴로 해결되는 비율 >30%**면 2단계 진행 가치 충분; **<10%**면 동적 조립이 지배적이라는 뜻이므로 2단계보다 디렉터리 수준 연결로 바로 축소.

**2단계 (동적 파일명 절충):**
- UTL_FILE 파일명이 부분상수+SYSDATE류일 때 **디렉터리 수준 엣지**로 강등 연결(구체 파일은 미연결). 패키지 내 1-hop 상수전파 허용.
- **트리거 조건**: 1단계에서 "완전 리터럴은 소수, 부분동적이 다수"로 나오면 이 단계가 recall을 크게 올린다.

**3단계 (선택적, 낮은 우선순위):**
- T-SQL SSIS(.dtsx), DB2 CLP 스크립트 등 **코드 밖 아티팩트** 파서. 별도 추출기로 분리하되 같은 그래프 스키마에 병합.
- **명시적 포기**: xp_cmdshell 셸 문자열, bcp 배치변수, BFILE 행데이터 기반 참조 — FP 위험 대비 가치 낮음. 미연결로 두고 "미해결 참조 있음(unresolved reference)" 플래그만 부여.

**아키텍처 채택:** 위 "스키마 통합 + 추출기 분리" 1안. 앱 리졸버(Java/Python 3층)와 DB 리졸버를 **동일 ReferenceResolver trait + 레지스트리**에 등록, merger로 결정론 병합(SCIP-IO식 부분실패 격리), verifier로 LLM 제안 승인. 파일 노드 식별자는 OpenLineage 네이밍(`file://{host}` + 절대경로)을 차용해 전역 유일성 확보 — 이로써 앱 코드가 참조하는 파일과 프로시저가 쓰는 파일이 **같은 노드로 자연 병합**된다(교차 언어 리니지의 부수 효과).

## Caveats
- **정량적 빈도 수치는 공개 데이터가 희소하다.** "UTL_FILE·BULK INSERT가 레거시에 흔하다"는 커뮤니티 문서·SI 설계 가이드·마이그레이션 도구(ora2pg가 DIRECTORY를 별도 처리 항목으로 두는 것)에 근거한 **정성 판단**이며, "전체 프로시저 중 N%가 파일 I/O" 같은 감사 통계는 확인하지 못했다. 실제 코드베이스 표본 측정(1단계 벤치마크)으로 대체할 것을 권고 — 표 안의 "높음/중/낮음"은 근거 기반 판단이되 정밀 통계가 아님(추정).
- **CAST의 PL/SQL 파일 미모델링은 공개 문서의 객체목록에 근거한 추론**이다. CAST가 "파일 노드를 만들지 않는다"고 명시 부정한 문서는 찾지 못했다. Extend 확장/신버전에서 다를 수 있으니 확신이 필요하면 CAST 지원팀에 확인 권고.
- **국산 DBMS(Tibero/Altibase)의 파일 I/O 프로시저 빈도는 Oracle 호환성에 근거한 유추**이며, 구체 사용통계는 확보하지 못했다(추정). Tibero는 Oracle PSM 호환을 지향하므로 UTL_FILE류 관용구가 유사할 가능성이 높으나 검증 필요.
- **"동적이 지배적"이라는 판단은 공개 예제(주로 리포트 생성 튜토리얼)의 편향 가능성**이 있다 — 인터페이스 **수신(read)** 프로시저는 상대 시스템이 약속한 고정 파일명을 읽으므로 리터럴 비율이 송신(write)보다 높을 수 있다. 1단계 벤치마크에서 read/write를 분리 측정할 것.