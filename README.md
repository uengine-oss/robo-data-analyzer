# ROBO Analyzer

**소스 코드를 분석하여 Neo4j 그래프로 변환하는 AI 기반 코드 분석 서비스**

이 프로젝트는 클린 아키텍처로 전환하기 위해 먼저 소스 코드를 분석하고 이해하는 것을 목적으로 합니다. 레거시 코드를 분석하여 구조화된 그래프 데이터로 변환하고, 이를 통해 시스템의 구조와 의존성을 시각화합니다.

---

## 새 개발자라면 여기부터

처음 합류하셨다면 **이 순서** 로 읽으세요:

1. **[파이프라인_순서도.md](파이프라인_순서도.md)** — 파이프라인 전체가 어떻게 흐르는지 그림·예시로 설명. 코드 몰라도 읽힘. **10 분이면 전체 그림 잡힘**.
2. **이 README 의 "분석 흐름" 섹션** — Phase 0/1/2/3 한눈에
3. **[docs/TECHNICAL_ISSUES.md](docs/TECHNICAL_ISSUES.md)** — 실제 부딪힌 문제·해결 기록
4. **[작업보고_2026-04-16.md](작업보고_2026-04-16.md)** — 최근 대규모 리팩토링 배경·방법

### 자주 찾는 위치 빠른 참조

| 무엇이 궁금한가 | 파일 |
|---|---|
| 파이프라인 진입점 | [service/source_analyze_service.py](service/source_analyze_service.py) |
| Phase 1 (재료 준비) 본체 | [analyzer/strategy/framework/pipeline/phase_ast.py](analyzer/strategy/framework/pipeline/phase_ast.py) |
| Phase 1 프로파일 (프레임워크/DB 패턴 요약) | [analyzer/strategy/framework/scanning/](analyzer/strategy/framework/scanning/) |
| Phase 2 (링킹) 본체 | [analyzer/strategy/framework/pipeline/phase_linking.py](analyzer/strategy/framework/pipeline/phase_linking.py) |
| Phase 2 엔티티 식별 DeepAgent | [analyzer/strategy/framework/linking/agent/entity.py](analyzer/strategy/framework/linking/agent/entity.py) |
| Phase 3 (설명) 본체 | [analyzer/strategy/framework/pipeline/phase_enrichment.py](analyzer/strategy/framework/pipeline/phase_enrichment.py) |
| 메서드 분석 프롬프트 | [rules/framework/code_analysis.yaml](rules/framework/code_analysis.yaml) |
| 파일 스캐너 (결정론 분류) | [analyzer/strategy/framework/parsing/scanner.py](analyzer/strategy/framework/parsing/scanner.py) |
| XML 파서 (SQL/Entity 추출) | [analyzer/strategy/framework/parsing/parsers/xml_universal.py](analyzer/strategy/framework/parsing/parsers/xml_universal.py) |
| Neo4j 쿼리 빌더 | [analyzer/strategy/framework/graph/queries.py](analyzer/strategy/framework/graph/queries.py) |
| 설정 / 환경변수 | [config/settings.py](config/settings.py) + `.env` |

### 5 분 안에 돌려보기

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. .env 편집 (API 키 + Neo4j 접속)
cp .env.example .env   # 없으면 README "환경변수 설정" 참고

# 3. 테스트 프로젝트를 data/source/ 에 넣기

# 4. 단일 분석 실행
python -m test.test_analyzer

# 5. 결과 확인
#    Neo4j Browser (http://localhost:7474) 에서
#    MATCH (n) RETURN n LIMIT 100
```

---

## 📋 목차

1. [개요](#개요)
2. [주요 기능](#주요-기능)
3. [시스템 아키텍처](#시스템-아키텍처)
4. [분석 흐름](#분석-흐름)
5. [프로젝트 구조](#프로젝트-구조)
6. [시작하기](#시작하기)
7. [API 엔드포인트](#api-엔드포인트)
8. [분석 전략](#분석-전략)
9. [그래프 데이터 구조](#그래프-데이터-구조)
10. [설정](#설정)
11. [기술 스택](#기술-스택)
12. [기술 이슈 및 해결](./docs/TECHNICAL_ISSUES.md)

---

## 개요

ROBO Analyzer는 소스 코드를 분석하여 Neo4j 그래프 데이터베이스에 구조화된 정보를 저장하는 서비스입니다. 

### 핵심 목적

- **레거시 코드 분석**: 기존 코드베이스를 분석하여 구조와 의존성 파악
- **그래프 기반 시각화**: Neo4j를 활용한 코드 구조 시각화
- **AI 기반 분석**: LLM을 활용한 코드 요약 및 관계 추출
- **클린 아키텍처 전환 준비**: 코드 분석을 통한 리팩토링 기반 마련

### 핵심 설계 원칙

**"결정론 먼저, LLM 은 꼭 필요한 곳만, 모든 재료는 관계 짓기 전에 준비"**

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1 — 재료 준비 (결정론 + 프로파일 Deep Agent 1회)      │
│    파일 스캔 + AST 파싱 + XML/설정 파싱 + 인덱스 구축        │
│  ↓                                                           │
│  Phase 2 — 연결 짓기 (결정론 → LLM 배치 → DeepAgent 3종)    │
│    SQL→테이블, 함수 호출, 엔티티 식별, 미해결 참조 해결      │
│  ↓                                                           │
│  Phase 3 — 설명 붙이기 (LLM 배치)                           │
│    메서드·클래스·테이블 요약                                 │
└─────────────────────────────────────────────────────────────┘
```

**왜 이 순서인가**: Phase 3 에서 함수를 정확히 설명하려면 "이 함수가 어떤 테이블을 읽고 쓰는지, 어떤 함수를 부르는지" 를 미리 알아야 합니다. 그래서 Phase 2 관계 → Phase 1 재료 순으로 **앞에서 뒤가 필요로 하는 것을 만든다**.

**왜 이렇게 분리했나**:

1. **결정론 먼저** — 이름이 완벽 매치되는 건 LLM 없이 0.1 초 만에 처리. 비용·시간 절약
2. **LLM 배치로 묶기** — 애매한 것만 20 개씩 묶어 LLM 호출 → 수백 번 → 수십 번으로 감소
3. **DeepAgent 최후 수단** — 배치로도 못 풀 복잡한 의미 추적만 에이전트에게 위임
4. **프로파일로 레거시 대응** — 엔티티 XML 없는 프로젝트도 `profile.summary` 수색 지침을 DeepAgent 에 주입해 테이블 추출 가능

---

## 주요 기능

### 1. Framework 분석 (Java/Kotlin)

- **AST 기반 구조 분석**: 클래스, 인터페이스, 메서드, 필드 추출
- **상속/구현 관계**: `EXTENDS`, `IMPLEMENTS` 관계 생성
- **메서드 호출 추적**: `CALLS` 관계를 통한 메서드 호출 체인 분석
- **의존성 분석**: `DEPENDENCY` 관계를 통한 타입 의존성 파악
- **클래스 관계**: `ASSOCIATION`, `COMPOSITION` 관계 추출

### 2. DBMS 분석 (PL/SQL)

- **프로시저/함수 분석**: PROCEDURE, FUNCTION, TRIGGER 노드 생성
- **테이블 스키마 분석**: DDL 파일에서 테이블/컬럼 구조 추출
- **데이터 접근 추적**: `FROM`, `WRITES` 관계를 통한 테이블 접근 분석
- **프로시저 호출 체인**: `CALL` 관계를 통한 프로시저 호출 추적
- **외래키 관계**: `FK_TO_TABLE` 관계를 통한 테이블 관계 파악
- **컨텍스트 인식 분석**: 중첩된 DML 구조에서 별칭 오인 방지 및 문맥 유지
  - 부모 노드의 컨텍스트(별칭 매핑, DML 유형, 조인 조건)를 자식 노드에 전달
  - 별칭을 실제 테이블명으로 오인하지 않도록 정확한 테이블명만 추출

### 3. 병렬 처리 및 의존성 관리

- **파일 레벨 병렬**: Phase 1/Phase 2에서 최대 5개 파일 동시 처리
- **배치 레벨 병렬**: LLM 분석 시 배치 단위 병렬 처리
- **자식→부모 의존성 보장**: `completion_event` 기반으로 부모 노드는 자식 완료 후 실행
- **성공/품질 추적**: `node.ok` 플래그로 불완전 요약 전파 방지
- **Cypher 동시성 보호**: `asyncio.Lock`을 통한 Neo4j 쿼리 동시성 제어

### 4. Fail-Fast 오류 처리

- **즉시 중단 원칙**: 분석 중 오류 발생 시 즉시 중단 (조용한 실패/부분 실패 불허)
- **예외 전파**: 모든 예외는 `AnalysisError`로 래핑되어 상위로 전파
- **타입 검증**: `validate_dict_result()` 유틸리티로 LLM 결과 타입 검증
- **토큰 낭비 방지**: 중간 실패 시 추가 LLM 호출 없이 즉시 종료

**오류 처리 패턴:**

```python
# ❌ 잘못된 패턴 (조용한 실패)
except Exception as e:
    log_process("ANALYZE", "ERROR", f"오류: {e}", logging.WARNING)
    # 계속 진행... (토큰 낭비)

# ✅ 올바른 패턴 (Fail-Fast)
except Exception as e:
    log_process("ANALYZE", "ERROR", f"오류: {e}", logging.ERROR)
    raise  # 즉시 중단
```

**LLM 결과 검증:**

```python
# dict 타입이 아니면 즉시 AnalysisError 발생
validated = self.validate_dict_result(llm_result, "청크 분석", batch_id)
summary = validated.get('summary', '')
```

### 5. 실시간 스트리밍

- **NDJSON 형식**: 실시간 분석 진행 상황 전달
- **이벤트 타입**: `message`, `data`, `error`, `complete`
- **진행률 추적**: 파일별, 전체 분석 진행률 표시

### 6. User Story 자동 생성

- **요구사항 문서 생성**: 분석 결과에서 User Story 및 Acceptance Criteria 자동 생성
- **포괄적 도출**: 모든 비즈니스 로직(CRUD, 배치, 집계 등)에서 User Story 도출
- **불완전 스킵**: 하위 분석 실패 시 최종 Summary/UserStory 생성 스킵
- **마크다운 형식**: 구조화된 마크다운 문서 생성

---

## 시스템 아키텍처

### 전체 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────┐
│                        Client (Frontend)                      │
└────────────────────────────┬──────────────────────────────────┘
                             │ HTTP/NDJSON Stream
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              API Router (router.py)                  │   │
│  │  - POST /robo/analyze/                               │   │
│  │  - DELETE /robo/data/                               │   │
│  └──────────────────┬───────────────────────────────────┘   │
│                      │                                        │
│  ┌───────────────────▼───────────────────────────────────┐   │
│  │      Analysis Orchestrator (orchestrator.py)          │   │
│  │  - 요청 파싱 및 검증                                   │   │
│  │  - 파일 탐색                                          │   │
│  │  - 분석 전략 선택                                      │   │
│  └───────────────────┬───────────────────────────────────┘   │
└──────────────────────┼────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Analyzer Strategy Layer                     │
│  ┌──────────────────────┐  ┌──────────────────────┐        │
│  │  Framework Analyzer   │  │   DBMS Analyzer      │        │
│  │  (Java/Kotlin)       │  │   (PL/SQL)           │        │
│  └──────────┬───────────┘  └──────────┬───────────┘        │
│             │                          │                     │
│  ┌──────────▼───────────┐  ┌───────────▼───────────┐        │
│  │  AST Processor       │  │  AST Processor        │        │
│  │  - Phase 1: AST      │  │  - Phase 1: AST       │        │
│  │  - Phase 2: LLM     │  │  - Phase 2: LLM       │        │
│  └──────────┬───────────┘  └──────────┬───────────┘        │
└─────────────┼─────────────────────────┼──────────────────────┘
              │                         │
              └──────────┬──────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Core Services                             │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │  Neo4j Client   │  │   LLM Client     │                 │
│  │  - Graph Query  │  │   - OpenAI API   │                 │
│  │  - Lock Control │  │   - Custom LLM   │                 │
│  └──────────────────┘  └──────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
              │                         │
              ▼                         ▼
┌──────────────────────┐    ┌──────────────────────┐
│   Neo4j Database     │    │   LLM API Service    │
│   (Graph Storage)    │    │   (AI Analysis)      │
└──────────────────────┘    └──────────────────────┘
```

### 레이어 구조

1. **API Layer** (`api/`)
   - FastAPI 기반 REST API
   - 요청/응답 처리
   - 스트리밍 응답 관리

2. **Orchestration Layer** (`api/orchestrator.py`)
   - 분석 프로세스 오케스트레이션
   - 파일 탐색 및 검증
   - 전략 선택 및 실행

3. **Analysis Layer** (`analyzer/strategy/`)
   - **BaseStreamingAnalyzer**: 공통 분석 프레임 (템플릿 메서드 패턴)
     - Neo4j 초기화 및 제약조건 보장
     - 증분/신규 모드 판단
     - 공통 스트리밍 메시지
     - User Story 생성 (공통 포맷)
     - 완료 통계 및 예외 처리
  - **FrameworkPipeline / DbmsPipeline**: 전략별 구현
     - Phase 1: 모든 파일 AST 그래프 생성 (병렬)
     - Phase 1.5: 부모 컨텍스트 생성 (DBMS만, Top-down)
     - Phase 2: 모든 파일 LLM 분석 (병렬, 자식→부모 의존성 보장)
     - Phase 3: User Story 문서 생성
   - **AST Processor**: AST 처리 및 쿼리 생성
     - `build_static_graph_queries()`: Phase 1 쿼리 생성
     - `_generate_parent_contexts()`: Phase 1.5 컨텍스트 생성 (DBMS만)
     - `run_llm_analysis()`: Phase 2 LLM 분석 (배치 처리, 의존성 보장, 컨텍스트 활용)

4. **Service Layer** (`analyzer/`, `util/`)
   - Neo4j 클라이언트
   - LLM 클라이언트
   - 유틸리티 함수

5. **Configuration Layer** (`config/`)
   - 환경변수 중앙 관리
   - 설정 싱글톤

---

## 분석 흐름

> **전체 흐름을 자연어로 풀어쓴 문서**: [파이프라인_순서도.md](파이프라인_순서도.md)
> 코드 몰라도 읽히는 단계별 설명 + 순서도 그림 포함.

### 🔄 전체 분석 단계 (한눈에 보기)

```
┌──────────────────────────────────────────────────────────────────┐
│                     입력: 프로젝트 폴더                           │
│             (소스 코드 + XML + YAML + SQL + DDL)                  │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 0 — DDL 처리 (DBMS 전략만 실행)                             │
│   CREATE TABLE 문 파싱 → Table/Column/FK 노드 선행 생성           │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1 — 재료 준비 (결정론 + Deep Agent 1회)                     │
│                                                                   │
│   Step 1. 프로파일 ← 새로 추가됨                                   │
│     • 파일 스캔 (scanner.scan_project) → catalog + plan           │
│     • 결정론 메타 수집 (확장자/매니페스트/imports/대표 파일 뼈대)  │
│     • Deep Agent 1회 호출 → 프로젝트의 프레임워크/DB 접근 패턴     │
│       자연어 요약 (profile.summary) 생성                          │
│     • 결과: pipeline_state.codebase_profile                       │
│                                                                   │
│   Step 2. AST 파싱                                                 │
│     • 소스 파일(plan.source_ast_jobs) → 함수/클래스/변수 노드      │
│     • 파일별 Neo4j 스트리밍                                        │
│     • ⚡ 병렬 처리                                                 │
│                                                                   │
│   Step 3. 아티팩트 파싱                                            │
│     • SQL/Entity XML/Config YAML → SqlStatement/Artifact/Config   │
│       chunk 생성                                                   │
│                                                                   │
│   Step 4. 인덱스 구축 (메모리)                                     │
│     • sql_index / module_index / config_index / artifact_index    │
│     • Phase 2 의 O(1) 조회용                                       │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2 — 연결 짓기 (결정론 → LLM 배치 → DeepAgent)               │
│                                                                   │
│   Step 1. 결정론 매칭                                              │
│     • 이름 완벽 매치 (SELECT FROM users ↔ users 테이블)            │
│                                                                   │
│   Step 2. LLM 배치 (병렬)                                          │
│     • Table LLM : SQL 쿼리 → 테이블·컬럼 추출                      │
│     • Call  LLM : 함수 호출 판정                                   │
│                                                                   │
│   Step 3. DeepAgent 병렬 (3종)                                     │
│     • 엔티티 식별 DeepAgent ← codebase_profile.summary 가          │
│                                system_prompt 에 "수색 지침" 주입  │
│     • 변수 참조 추적 (결정론만, LLM 미사용)                        │
│     • 미해결 참조 DeepAgent                                        │
│                                                                   │
│   Step 4. FK 반영 + 후처리                                         │
│     • READS/WRITES/CALLS/REFERS_TO/FK 관계 Neo4j 저장              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 3 — 설명 붙이기 (LLM 배치)                                   │
│                                                                   │
│   Step 1. 컨텍스트 수집 (상수/참조변수/테이블 샘플)                │
│                                                                   │
│   Step 2. 메서드 요약 (20개씩 배치 LLM)                            │
│     • summary / stereotype / actor / dependencies / tableInsights │
│     • tableInsights 는 기존 테이블에만 역할·컬럼 설명 추가         │
│       (새 노드 생성 금지, pipeline_state.table_insights_map 누적) │
│                                                                   │
│   Step 3. Field 비활성화 + Module (클래스) 요약                    │
│                                                                   │
│   Step 4. Actor 병합 + Table Description 보강                      │
│     • 메서드 summary + table_insights_map 을 LLM 에 투입           │
│     • 테이블별 최종 description 생성                               │
│     • 기존 Table/Column 노드에 MATCH + SET (생성 금지)             │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│         결과: Neo4j 그래프 (노드 + 관계 + 자연어 설명)             │
└──────────────────────────────────────────────────────────────────┘
```

### 왜 프로파일이 Phase 1 에 들어가는가

엔티티/매퍼 XML 이 전혀 없는 레거시 (ProFrame · Pro\*C 시뮬 등) 는 Phase 2 의 결정론·LLM 배치가 테이블을 못 뽑음. 이때 **Phase 1 Step 1 프로파일이 뽑아둔 `codebase_profile.summary`** 가 Phase 2 엔티티 식별 DeepAgent 의 system_prompt 에 "수색 지침" 으로 주입되어, `mpfmdbio("zord_acnt_s0023")` 같은 **프로젝트 특이 패턴** 을 근거로 실제 코드에서 테이블을 추출하도록 유도한다.

### 📊 병렬 처리 구조

```
                           파일 1         파일 2         파일 3         파일 4
                             │              │              │              │
Phase 1 (AST)        ┌───────┴───────┬──────┴──────┬──────┴──────┬───────┴───────┐
                     │ AST 생성      │ AST 생성    │ AST 생성    │ AST 생성      │
                     └───────────────┴─────────────┴─────────────┴───────────────┘
                                            │
                                   ⚡ 5개 파일 병렬
                                            ↓
Phase 1.5 (Context)  ┌───────────────┬─────────────┬─────────────┬───────────────┐
(DBMS만)             │ 컨텍스트 생성 │ 컨텍스트    │ 컨텍스트    │ 컨텍스트      │
                     └───────────────┴─────────────┴─────────────┴───────────────┘
                                            │
                                   ⚡ 5개 파일 병렬
                                            ↓
Phase 2 (LLM)        ┌───────────────┬─────────────┬─────────────┬───────────────┐
                     │ 배치 #1~#5    │ 배치 #1~#3  │ 배치 #1~#8  │ 배치 #1~#4    │
                     │ ⚡ 배치 병렬   │ ⚡ 배치 병렬 │ ⚡ 배치 병렬 │ ⚡ 배치 병렬   │
                     └───────────────┴─────────────┴─────────────┴───────────────┘
                                            │
                                   ⚡ 5개 파일 병렬
                                            ↓
                              ┌─────────────────────────┐
                              │   Neo4j 저장 (순차)      │
                              │   단일 트랜잭션 보장     │
                              └─────────────────────────┘
```

### 🔗 Phase 2 내부 의존성 처리

```
          자식 노드 (리프)              부모 노드
               │                          │
               ▼                          │
        ┌─────────────┐                   │
        │ 배치 #1     │ ←── 리프 먼저     │
        │ (SELECT ×4) │                   │
        └──────┬──────┘                   │
               │ completion_event         │
               ▼                          ▼
        ┌─────────────┐           ┌─────────────┐
        │ 배치 #2     │           │ 배치 #6     │ ←── 자식 완료 후 실행
        │ (UNION_ALL) │           │ (MERGE)     │
        └──────┬──────┘           └──────┬──────┘
               │ completion_event         │ completion_event
               ▼                          ▼
        ┌─────────────────────────────────────────┐
        │ 최상위 노드 (FUNCTION/PROCEDURE)         │
        │ 모든 자식 summary 통합                   │
        └─────────────────────────────────────────┘
```

### 전체 분석 시퀀스 다이어그램

```
Client                    API Router          Orchestrator         Analyzer          Neo4j Client        LLM Client
  │                           │                    │                  │                  │                  │
  │── POST /robo/analyze/ ────>│                    │                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │── create_orch() ──>│                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │<── orchestrator ───│                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │── discover_files() ─>│                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │<── file_names ──────│                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │── run_analysis() ──>│                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │                    │── analyze() ──────>│                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── ensure_constraints() ──>│          │
  │                           │                    │                  │<── OK ────────────────────│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── Phase 1: AST ────────────>│          │
  │                           │                    │                  │                  │                  │
  │<── message: "Phase 1 시작" ──│                    │                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── build_static_graph() ──>│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │<── queries ────────────────│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── run_graph_query() ──────>│          │
  │                           │                    │                  │                  │                  │
  │<── data: graph ────────────│                    │                  │<── graph ──────────────────│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── Phase 2: LLM ────────────>│          │
  │                           │                    │                  │                  │                  │
  │<── message: "Phase 2 시작" ──│                    │                  │                  │                  │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── run_llm_analysis() ────────────────>│
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │<── analysis_result ────────────────────│
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── build_queries() ────────>│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── run_graph_query() ──────>│          │
  │                           │                    │                  │                  │                  │
  │<── data: graph ────────────│                    │                  │<── graph ──────────────────│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │                  │── Phase 3: User Story ────>│          │
  │                           │                    │                  │                  │                  │
  │<── data: user_story ───────│                    │                  │<── document ───────────────│          │
  │                           │                    │                  │                  │                  │
  │                           │                    │<── complete ──────│                  │                  │
  │                           │                    │                  │                  │                  │
  │<── complete ───────────────│                    │                  │                  │                  │
```

### Framework 분석 상세 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│                     소스 파일 목록                               │
│  - OrderService.java                                            │
│  - OrderRepository.java                                         │
│  - OrderController.java                                         │
│  ... (총 N개 파일)                                              │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼ (파일 5개 병렬 처리)
┌─────────────────────────────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ...    │
│  │  File 1      │  │  File 2      │  │  File 3      │         │
│  │  AST 로드    │  │  AST 로드    │  │  AST 로드    │         │
│  │  (JSON)      │  │  (JSON)      │  │  (JSON)      │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                  │                  │
│         ▼ (Phase 1)       ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ 정적그래프   │  │ 정적그래프    │  │ 정적그래프    │        │
│  │  노드생성    │  │  노드생성     │  │  노드생성     │        │
│  │  - CLASS     │  │  - CLASS      │  │  - CLASS      │        │
│  │  - METHOD    │  │  - METHOD     │  │  - METHOD     │        │
│  │  - FIELD     │  │  - FIELD      │  │  - FIELD      │        │
│  │  - 관계      │  │  - 관계       │  │  - 관계       │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         │                 │                  │                  │
│         ▼ (Phase 2)       ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ LLM 분석    │  │ LLM 분석     │  │ LLM 분석     │        │
│  │ (청크별)    │  │ (청크별)     │  │ (청크별)     │        │
│  │  - summary  │  │  - summary   │  │  - summary   │        │
│  │  - CALLS    │  │  - CALLS     │  │  - CALLS     │        │
│  │  - DEPEND   │  │  - DEPEND    │  │  - DEPEND    │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
└─────────┼─────────────────┼──────────────────┼─────────────────┘
          │                 │                  │
          └─────────────────┴────────┬─────────┘
                                    │
                                    ▼ (Cypher Lock 보호)
                      ┌──────────────────────────────────┐
                      │         Neo4j 그래프 저장         │
                      │  - 노드 생성/업데이트             │
                      │  - 관계 생성                      │
                      └──────────────────────────────────┘
                                    │
                                    ▼
                      ┌──────────────────────────────────┐
                      │      User Story 문서 생성        │
                      │  - 클래스별 요약                  │
                      │  - User Story 추출               │
                      │  - Acceptance Criteria 생성      │
                      └──────────────────────────────────┘
```

### DBMS 분석 상세 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│                    DDL 파일 처리 (Phase 0)                      │
│  - schema.sql                                                  │
│  - tables.sql                                                  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    테이블/컬럼 노드 생성                        │
│  - Table 노드                                                  │
│  - Column 노드                                                 │
│  - FK_TO_TABLE 관계                                            │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     소스 파일 목록                               │
│  - procedure1.sql                                              │
│  - procedure2.sql                                              │
│  ... (총 N개 파일)                                              │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼ (파일 5개 병렬 처리)
┌─────────────────────────────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ...    │
│  │  File 1      │  │  File 2      │  │  File 3      │         │
│  │  AST 로드    │  │  AST 로드    │  │  AST 로드    │         │
│  │  (JSON)      │  │  (JSON)      │  │  (JSON)      │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                  │                  │
│         ▼ (Phase 1)       ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ 정적그래프   │  │ 정적그래프    │  │ 정적그래프    │        │
│  │  노드생성    │  │  노드생성     │  │  노드생성     │        │
│  │  - PROCEDURE│  │  - PROCEDURE │  │  - PROCEDURE │        │
│  │  - FUNCTION │  │  - FUNCTION  │  │  - FUNCTION  │        │
│  │  - Variable │  │  - Variable  │  │  - Variable  │        │
│  │  - 관계      │  │  - 관계       │  │  - 관계       │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         │                 │                  │                  │
│         ▼ (Phase 1.5)     ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ 부모 컨텍스트│  │ 부모 컨텍스트│  │ 부모 컨텍스트│        │
│  │  생성        │  │  생성        │  │  생성        │        │
│  │  (Top-down)  │  │  (Top-down)  │  │  (Top-down)  │        │
│  │  - 별칭 매핑 │  │  - 별칭 매핑 │  │  - 별칭 매핑 │        │
│  │  - DML 유형  │  │  - DML 유형  │  │  - DML 유형  │        │
│  │  - 조인 조건 │  │  - 조인 조건 │  │  - 조인 조건 │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         │                 │                  │                  │
│         ▼ (Phase 2)       ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ LLM 분석    │  │ LLM 분석     │  │ LLM 분석     │        │
│  │ (배치별)    │  │ (배치별)     │  │ (배치별)     │        │
│  │  - summary  │  │  - summary   │  │  - summary   │        │
│  │  - CALL     │  │  - CALL      │  │  - CALL      │        │
│  │  - FROM     │  │  - FROM      │  │  - FROM      │        │
│  │  - WRITES   │  │  - WRITES    │  │  - WRITES    │        │
│  │  (컨텍스트  │  │  (컨텍스트   │  │  (컨텍스트   │        │
│  │   활용)     │  │   활용)      │  │   활용)      │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
└─────────┼─────────────────┼──────────────────┼─────────────────┘
          │                 │                  │
          └─────────────────┴────────┬─────────┘
                                    │
                                    ▼ (Cypher Lock 보호)
                      ┌──────────────────────────────────┐
                      │         Neo4j 그래프 저장         │
                      │  - 노드 생성/업데이트             │
                      │  - 관계 생성                      │
                      └──────────────────────────────────┘
                                    │
                                    ▼
                      ┌──────────────────────────────────┐
                      │      User Story 문서 생성        │
                      │  - 프로시저별 요약                │
                      │  - User Story 추출               │
                      │  - Acceptance Criteria 생성      │
                      └──────────────────────────────────┘
```

---

## 프로젝트 구조

```
robo_analyzer_core/
├── main.py                      # FastAPI 애플리케이션 진입점
│
├── api/                         # REST API 레이어
│   ├── router.py               # API 라우터 정의
│   │   ├── POST /robo/analyze/ # 소스 코드 분석 (스트리밍)
│   │   └── DELETE /robo/data/  # 사용자 데이터 삭제
│   └── orchestrator.py         # 분석 오케스트레이터
│       ├── AnalysisOrchestrator # 분석 프로세스 관리
│       ├── discover_source_files() # 소스 파일 탐색
│       └── cleanup_all_data()  # 데이터 정리
│
├── analyzer/                    # 분석 엔진 코어
│   ├── neo4j_client.py         # Neo4j 비동기 클라이언트
│   │   ├── execute_queries()  # Cypher 쿼리 실행
│   │   ├── run_graph_query()  # 그래프 결과 반환
│   │   └── ensure_constraints() # 제약조건 생성
│   │
│   └── strategy/               # 분석 전략 패턴
│       ├── base/               # 공통 기반 클래스
│       │   ├── processor.py   # AstProcessor (공통 처리 로직)
│       │   │   ├── validate_dict_result() # LLM 결과 타입 검증 (Fail-Fast)
│       │   │   ├── run_llm_analysis()   # LLM 분석 실행
│       │   │   └── _apply_summary_to_nodes() # 요약 적용
│       │   └── statement_node.py # StatementNode 클래스
│       │
│       ├── base_analyzer.py    # BaseStreamingAnalyzer (공통 프레임)
│       │   ├── analyze()       # 템플릿 메서드 (공통 흐름)
│       │   ├── run_pipeline() # 전략별 파이프라인 (추상 메서드)
│       │   └── build_user_story_doc() # User Story 생성 (공통)
│       ├── analyzer_factory.py # 전략 팩토리
│       │
│       ├── framework/          # Framework (Java/Kotlin) 분석
│       │   ├── framework_pipeline.py  # FrameworkPipeline (총괄)
│       │   │   ├── run_pipeline()     # Phase 1 → Phase 2 → Phase 3
│       │   │   ├── _load_all_files()  # 모든 파일 로드 (병렬)
│       │   │   ├── _run_phase1()      # Phase 1: 모든 파일 AST (병렬)
│       │   │   └── _run_phase2()      # Phase 2: 모든 파일 LLM (병렬)
│       │   │
│       │   └── ast_processor.py        # FrameworkAstProcessor
│       │       ├── build_static_graph_queries() # Phase 1 쿼리 생성
│       │       └── run_llm_analysis()  # Phase 2 LLM 분석
│       │           ├── 배치 계획 (BatchPlanner)
│       │           ├── 자식→부모 의존성 보장 (completion_event)
│       │           └── 실패 상세 정보 수집
│       │
│       └── dbms/               # DBMS (PL/SQL) 분석
│           ├── dbms_analyzer.py       # DbmsAnalyzer
│           │   ├── run_pipeline()    # DDL → Phase 1 → Phase 2 → Phase 3
│           │   ├── _process_ddl()     # Phase 0: DDL 처리
│           │   ├── _load_all_files()  # 모든 파일 로드 (병렬)
│           │   ├── _run_phase1()      # Phase 1: 모든 파일 AST (병렬)
│           │   └── _run_phase2()      # Phase 2: 모든 파일 LLM (병렬)
│           │
│           └── ast_processor.py       # DbmsAstProcessor
│               ├── build_static_graph_queries() # Phase 1 쿼리 생성
│               ├── _generate_parent_contexts()  # Phase 1.5 컨텍스트 생성
│               └── run_llm_analysis()  # Phase 2 LLM 분석
│                   ├── 배치 계획 (BatchPlanner)
│                   ├── 부모 컨텍스트 대기 (context_ready_event)
│                   ├── 자식→부모 의존성 보장 (completion_event)
│                   └── 실패 상세 정보 수집
│
├── config/                      # 설정 관리
│   └── settings.py             # 환경변수 중앙 관리 (Singleton)
│       ├── Neo4jConfig         # Neo4j 연결 설정
│       ├── LLMConfig           # LLM API 설정
│       ├── ConcurrencyConfig   # 병렬 처리 설정
│       ├── BatchConfig         # 배치 처리 설정
│       └── PathConfig          # 경로 설정
│
├── util/                        # 유틸리티 모듈
│   ├── exception.py            # 계층화된 예외 클래스
│   │   ├── RoboAnalyzerError   # 기본 예외
│   │   ├── AnalysisError        # 분석 오류 (Fail-Fast 중단)
│   │   ├── CodeProcessError    # 코드 처리 오류
│   │   ├── LLMCallError        # LLM 호출 오류
│   │   └── QueryExecutionError # Neo4j 쿼리 오류
│   │
│   ├── stream_utils.py         # NDJSON 스트리밍 유틸리티
│   │   ├── emit_message()      # 메시지 이벤트
│   │   ├── emit_data()         # 데이터 이벤트
│   │   ├── emit_error()        # 에러 이벤트
│   │   ├── emit_complete()     # 완료 이벤트
│   │   └── format_graph_result() # 그래프 결과 포맷팅
│   │
│   ├── logger.py               # 구조화된 로깅
│   │   └── setup_logging()     # 로깅 초기화
│   │
│   ├── llm_client.py           # LLM 클라이언트 팩토리
│   │   └── get_llm()           # LLM 인스턴스 생성
│   │
│   ├── llm_audit.py             # LLM 호출 감사 로깅
│   │   └── invoke_with_audit() # 감사 로그와 함께 LLM 호출
│   │
│   ├── rule_loader.py           # YAML 프롬프트 규칙 로더
│   │   ├── RuleLoader          # 규칙 로더 클래스
│   │   ├── render_prompt()     # 프롬프트 렌더링
│   │   └── execute()            # 프롬프트 실행 (LLM 호출)
│   │
│   └── utility_tool.py         # 범용 유틸리티 함수
│       ├── calculate_code_token() # 토큰 계산
│       └── generate_user_story_document() # User Story 문서 생성
│
├── rules/                       # LLM 프롬프트 규칙 (YAML)
│   ├── framework/              # Framework 분석 프롬프트
│   │   ├── analysis.yaml       # 코드 분석 (summary, dependencies, calls)
│   │   ├── association.yaml    # 필드 기반 ASSOCIATION 관계 분석
│   │   ├── user_story_map.yaml  # User Story Map 단계 (청크별 후보 생성)
│   │   ├── user_story_reduce.yaml # User Story Reduce 단계 (통합/중복제거)
│   │   ├── method_summary_enhancement.yaml # 메서드 summary 보강
│   │   └── stereotype.yaml     # 클래스/메서드 스테레오타입 분류
│   │
│   └── dbms/                   # DBMS 분석 프롬프트
│       ├── analysis.yaml       # 코드 분석
│       ├── column.yaml         # 컬럼 분석
│       ├── ddl.yaml            # DDL 분석
│       ├── dml.yaml            # DML 분석
│       ├── procedure_summary_only.yaml # 프로시저 요약
│       ├── procedure_user_story.yaml # 프로시저 User Story
│       ├── table_summary.yaml  # 테이블 요약
│       └── variables.yaml      # 변수 분석
│
├── docs/                        # 문서
│   └── REFACTORING_GUIDE.md    # 리팩토링 가이드
│
└── test/                        # 테스트
    ├── test_analyzer.py         # 분석기 테스트
    ├── test_neo4j_return_graph.py # Neo4j 그래프 반환 테스트
    └── test_procedure_analyzer.py # 프로시저 분석기 테스트
```

---

## 시작하기

### 요구사항

- **Python**: 3.11 이상
- **Neo4j**: 5.x 이상
- **LLM API**: OpenAI 또는 호환 API

### 설치

```bash
# 1. 저장소 클론
git clone <repository-url>
cd robo_analyzer_core

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp env.example .env
# .env 파일을 편집하여 API 키 및 Neo4j 연결 정보 설정
```

### 환경변수 설정

`.env` 파일에 다음 설정을 추가하세요:

```bash
# Neo4j 설정
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# LLM API 설정
LLM_API_KEY=your-api-key
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4.1
LLM_MAX_TOKENS=32768

# 병렬 처리 설정
FILE_CONCURRENCY=5
MAX_CONCURRENCY=5

# 서버 설정
HOST=0.0.0.0
PORT=5502
```

### 실행

```bash
# 개발 서버
python main.py

# 또는 uvicorn 직접 실행
uvicorn main:app --host 0.0.0.0 --port 5502 --reload
```

서버가 시작되면 다음 URL에서 접근할 수 있습니다:
- API 문서: http://localhost:5502/docs
- 헬스체크: http://localhost:5502/health

---

## API 엔드포인트

### POST `/robo/analyze/`

소스 코드를 분석하여 Neo4j 그래프 데이터 생성

**Request Headers:**
- `OpenAI-Api-Key`: LLM API 키 (필수)
- `Accept-Language`: 출력 언어 (기본: `ko`)

**Request Body:**
```json
{
  "strategy": "framework",  // "framework" 또는 "dbms"
  "target": "java"          // "java", "oracle" 등
}
```

**Response:**
- Content-Type: `application/x-ndjson`
- 스트리밍 응답 (NDJSON 형식)

**응답 이벤트 타입:**

1. **message** (진행 상황 메시지)
```json
{"type": "message", "content": "🚀 프레임워크 코드 분석을 시작합니다"}
{"type": "message", "content": "🏗️ [Phase 1] AST 구조 그래프 생성 (10개 파일 병렬)"}
```

2. **data** (그래프 데이터)
```json
{
  "type": "data",
  "graph": {
    "Nodes": [
      {
        "Node ID": "4:...",
        "Labels": ["CLASS"],
        "Properties": {
          "class_name": "OrderService",
          "startLine": 10,
          "endLine": 150,
          "directory": "com/example",
          "file_name": "OrderService.java"
        }
      }
    ],
    "Relationships": [
      {
        "Relationship ID": "5:...",
        "Type": "CALLS",
        "Start Node ID": "4:...",
        "End Node ID": "6:...",
        "Properties": {"method": "save"}
      }
    ]
  },
  "line_number": 150,
  "analysis_progress": 75,
  "current_file": "OrderService.java"
}
```

3. **error** (오류)
```json
{
  "type": "error",
  "content": "분석 실패: LLM 응답의 라인 번호가 유효하지 않습니다",
  "errorType": "AnalysisError",
  "traceId": "stream-abc123"
}
```

4. **complete** (완료)
```json
{"type": "complete"}
```

**예시:**
```bash
curl -X POST http://localhost:5502/robo/analyze/ \
  -H "Content-Type: application/json" \
  -H "OpenAI-Api-Key: your-api-key" \
  -d '{
    "strategy": "framework",
    "target": "java"
  }'
```

### DELETE `/robo/data/`

사용자 데이터 전체 삭제 (임시 파일 + Neo4j 그래프)

**Request Headers:**
- `OpenAI-Api-Key`: LLM API 키 (필수, 데이터 삭제 시)

**Response:**
```json
{
  "message": "모든 데이터가 삭제되었습니다."
}
```

### GET `/`

헬스체크

**Response:**
```json
{
  "status": "ok",
  "service": "robo-analyzer",
  "version": "2.0.0"
}
```

### GET `/health`

상세 헬스체크

**Response:**
```json
{
  "status": "healthy",
  "service": "robo-analyzer",
  "version": "2.0.0",
  "config": {
    "file_concurrency": 5,
    "max_concurrency": 5
  }
}
```

---

## 분석 전략

### Framework 분석 (Java/Kotlin)

Framework 분석은 3단계로 진행됩니다:

#### Phase 1: AST 그래프 생성

모든 파일을 병렬로 처리하여 정적 그래프 구조를 생성합니다.

**생성되는 노드:**
- `CLASS`, `INTERFACE`, `ENUM`: 클래스 구조
- `METHOD`, `CONSTRUCTOR`: 메서드
- `FIELD`, `PARAMETER`: 필드/파라미터

**생성되는 관계:**
- `PARENT_OF`: 부모-자식 관계 (클래스 → 메서드)
- `NEXT`: 형제 순서 관계 (메서드1 → 메서드2)
- `HAS_METHOD`, `HAS_FIELD`: 포함 관계

**Cypher 쿼리 예시:**
```cypher
MERGE (n:CLASS {
  class_name: 'OrderService'
})
SET n.startLine = 10,
    n.endLine = 150,
    n.directory = 'com/example',
    n.file_name = 'OrderService.java'
RETURN n
```

#### Phase 2: LLM 분석

모든 파일을 병렬로 처리하여 LLM을 통한 코드 분석을 수행합니다.

**핵심 메커니즘:**
- **배치 처리**: 토큰 제한을 고려한 배치 그룹핑
- **자식→부모 의존성 보장**: `completion_event` 기반으로 부모 노드는 자식 노드 완료 후 실행
- **성공/품질 추적**: `node.ok` 플래그로 자식 실패 시 부모도 불완전 마킹
- **실패 처리**: 배치 실패 시 상세 정보 수집 및 스트림 출력

**추가되는 정보:**
- `summary`: 코드 요약
- `CALLS`: 메서드 호출 관계
- `DEPENDENCY`: 타입 의존성 관계
- `ASSOCIATION`, `COMPOSITION`: 클래스 관계

**Cypher 쿼리 예시:**
```cypher
MATCH (src:CLASS {
  startLine: 10,
  directory: 'com/example',
  file_name: 'OrderService.java'
})
SET src.summary = '주문 서비스를 제공하는 클래스입니다...'
RETURN src
```

```cypher
MATCH (src:CLASS {...})
MATCH (dst:CLASS)
WHERE toLower(dst.class_name) = toLower('OrderRepository')
MERGE (src)-[r:CALLS {method: 'save'}]->(dst)
RETURN r
```

#### Phase 3: User Story 생성

분석 완료 후 Neo4j에서 요약 데이터를 조회하여 User Story 문서를 생성합니다.

### DBMS 분석 (PL/SQL)

DBMS 분석은 5단계로 진행됩니다:

#### Phase 0: DDL 처리

DDL 파일에서 테이블/컬럼 스키마를 추출합니다.

**생성되는 노드:**
- `Table`: 테이블
- `Column`: 컬럼

**생성되는 관계:**
- `HAS_COLUMN`: 테이블 → 컬럼
- `FK_TO_TABLE`: 외래키 관계

#### Phase 1: AST 그래프 생성

프로시저/함수 파일을 분석하여 정적 그래프 구조를 생성합니다.

**생성되는 노드:**
- `PROCEDURE`, `FUNCTION`, `TRIGGER`: 프로시저
- `Variable`: 변수

**생성되는 관계:**
- `PARENT_OF`: 부모-자식 관계
- `NEXT`: 형제 순서 관계

**병렬 처리:**
- 최대 5개 파일 동시 처리 (`file_concurrency`)
- 각 파일의 AST를 독립적으로 생성

#### Phase 1.5: 부모 컨텍스트 생성 (컨텍스트 인식 분석)

각 파일 내부에서 Top-down 방식으로 부모 노드의 컨텍스트를 생성합니다.

**핵심 메커니즘:**
- **스켈레톤 코드 생성**: 자식 블록을 `....`로 압축하여 토큰 수 감소
- **깊이 순 처리**: 얕은 노드부터 순차적으로 컨텍스트 생성
- **조상 컨텍스트 수집**: 최대 300토큰까지 조상 노드의 컨텍스트 결합
- **LLM 요약 생성**: 부모 노드의 핵심 정보(DML 유형, 별칭 매핑, 조인 조건 등) 추출

**생성되는 컨텍스트 정보:**
- DML 유형 및 타겟 테이블 (SELECT, MERGE, INSERT 등)
- 별칭 매핑 (예: "별칭 A = 서브쿼리, B = RDF01HH_TB")
- 조인/매칭 조건
- 핵심 변수/파라미터
- 구조 유형 및 목적

**병렬 처리:**
- 같은 깊이의 노드는 최대 5개 동시 처리 (`MAX_CONCURRENCY`)
- 부모의 `context_ready_event` 대기 후 처리

#### Phase 2: LLM 분석

모든 파일을 병렬로 처리하여 LLM을 통한 코드 분석을 수행합니다.

**핵심 메커니즘:**
- **컨텍스트 활용**: 부모 컨텍스트를 참고하여 별칭 오인 방지 및 문맥 유지
- **배치 처리**: 토큰 제한을 고려한 배치 그룹핑
- **자식→부모 의존성 보장**: `completion_event` 기반으로 부모 노드는 자식 노드 완료 후 실행
- **성공/품질 추적**: `node.ok` 플래그로 자식 실패 시 부모도 불완전 마킹
- **실패 처리**: 배치 실패 시 상세 정보 수집 및 스트림 출력
- **변수 분석**: 변수 선언 노드 분석 및 `DECLARES` 관계 생성
- **테이블 분석**: DML 분석을 통한 `FROM`, `WRITES` 관계 생성 (별칭을 테이블로 오인하지 않음)

**추가되는 정보:**
- `summary`: 프로시저 요약 (부모 컨텍스트 반영)
- `CALL`: 프로시저 호출 관계
- `FROM`: 테이블 읽기 관계 (실제 테이블명만 추출)
- `WRITES`: 테이블 쓰기 관계 (실제 테이블명만 추출)

**병렬 처리:**
- 최대 5개 파일 동시 처리 (`file_concurrency`)
- 각 파일 내부에서 배치 단위 병렬 처리

#### Phase 3: User Story 생성

분석 완료 후 User Story 문서를 생성합니다.

---

## 그래프 데이터 구조

### Framework (Java/Kotlin)

#### 노드 타입

**CLASS**
```json
{
  "Labels": ["CLASS"],
  "Properties": {
    "class_name": "OrderService",
    "directory": "com/example",
    "file_name": "OrderService.java",
    "startLine": 10,
    "endLine": 150,
    "summary": "주문 서비스를 제공하는 클래스입니다...",
    "node_code": "public class OrderService {...}"
  }
}
```

**METHOD**
```json
{
  "Labels": ["METHOD"],
  "Properties": {
    "name": "save",
    "directory": "com/example",
    "file_name": "OrderService.java",
    "startLine": 20,
    "endLine": 35,
    "summary": "주문을 저장하는 메서드입니다...",
    "return_type": "void",
    "node_code": "public void save(Order order) {...}"
  }
}
```

#### 관계 타입

**CALLS** (메서드 호출)
```json
{
  "Type": "CALLS",
  "Start Node ID": "4:...",
  "End Node ID": "6:...",
  "Properties": {
    "method": "save",
    "line_number": 25
  }
}
```

**EXTENDS** (상속)
```json
{
  "Type": "EXTENDS",
  "Start Node ID": "4:...",
  "End Node ID": "8:...",
  "Properties": {}
}
```

**DEPENDENCY** (의존성)
```json
{
  "Type": "DEPENDENCY",
  "Start Node ID": "4:...",
  "End Node ID": "10:...",
  "Properties": {
    "dependency_type": "import"
  }
}
```

### DBMS (PL/SQL)

#### 노드 타입

**PROCEDURE**
```json
{
  "Labels": ["PROCEDURE"],
  "Properties": {
    "procedure_name": "CREATE_ORDER",
    "directory": "procedures",
    "file_name": "order_proc.sql",
    "startLine": 10,
    "endLine": 50,
    "summary": "주문을 생성하는 프로시저입니다...",
    "schema_name": "ORDER_SCHEMA"
  }
}
```

**Table**
```json
{
  "Labels": ["Table"],
  "Properties": {
    "name": "ORDERS",
    "schema": "ORDER_SCHEMA",
    "db": "postgres",
    "description": "주문 테이블",
    "table_type": "BASE TABLE"
  }
}
```

#### 관계 타입

**CALL** (프로시저 호출)
```json
{
  "Type": "CALL",
  "Start Node ID": "4:...",
  "End Node ID": "6:...",
  "Properties": {
    "line_number": 25
  }
}
```

**FROM** (테이블 읽기)
```json
{
  "Type": "FROM",
  "Start Node ID": "4:...",
  "End Node ID": "8:...",
  "Properties": {
    "line_number": 30
  }
}
```

**WRITES** (테이블 쓰기)
```json
{
  "Type": "WRITES",
  "Start Node ID": "4:...",
  "End Node ID": "8:...",
  "Properties": {
    "line_number": 35
  }
}
```

---

## 설정

### 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `NEO4J_URI` | Neo4j 연결 URI | `bolt://127.0.0.1:7687` |
| `NEO4J_USER` | Neo4j 사용자명 | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j 비밀번호 | (필수) |
| `LLM_API_KEY` | LLM API 키 | (필수) |
| `LLM_API_BASE` | LLM API 기본 URL | `https://api.openai.com/v1` |
| `LLM_MODEL` | 사용할 LLM 모델 | `gpt-4.1` |
| `LLM_MAX_TOKENS` | 최대 토큰 수 | `32768` |
| `FILE_CONCURRENCY` | 파일 병렬 처리 수 | `5` |
| `MAX_CONCURRENCY` | 청크 병렬 처리 수 | `5` |
| `HOST` | 서버 호스트 | `0.0.0.0` |
| `PORT` | 서버 포트 | `5502` |

### 설정 파일 구조

설정은 `config/settings.py`에서 중앙 관리됩니다:

```python
from config.settings import settings

# Neo4j 설정
neo4j_uri = settings.neo4j.uri
neo4j_user = settings.neo4j.user

# LLM 설정
llm_api_key = settings.llm.api_key
llm_model = settings.llm.model

# 병렬 처리 설정
file_concurrency = settings.concurrency.file_concurrency
max_concurrency = settings.concurrency.max_concurrency
```

---

## 기술 스택

### 백엔드
- **FastAPI**: 비동기 웹 프레임워크
- **Python 3.11+**: 프로그래밍 언어

### 데이터베이스
- **Neo4j 5.x**: 그래프 데이터베이스
- **Cypher**: 그래프 쿼리 언어

### AI/ML
- **LangChain**: LLM 통합 프레임워크
- **OpenAI API**: LLM 서비스 (또는 호환 API)

### 유틸리티
- **aiofiles**: 비동기 파일 I/O
- **tiktoken**: 토큰 계산
- **Jinja2**: 템플릿 엔진
- **PyYAML**: YAML 파싱

---

## 기여하기

1. 이 저장소를 Fork 합니다
2. 기능 브랜치를 생성합니다 (`git checkout -b feature/amazing-feature`)
3. 변경사항을 커밋합니다 (`git commit -m 'Add amazing feature'`)
4. 브랜치에 푸시합니다 (`git push origin feature/amazing-feature`)
5. Pull Request를 생성합니다

---

## 라이선스

MIT License

---

## 버전 히스토리

### v2.4.0 (현재)
- **Neo4j 배치 처리 최적화**: 성능과 안정성 개선
  - DDL/벡터: UNWIND 배치 처리 (같은 구조의 대량 데이터)
  - AST/LLM 분석: 단일 트랜잭션 순차 실행 (의존성 보장)
  - Cypher 변수명 `__cy_xxx__` 패턴 도입 (충돌 방지)
- **상세 분석 흐름 문서화**: README에 단계별 흐름도 추가
- **트러블슈팅 문서**: `NEO4J_BATCH_TROUBLESHOOTING.md` 추가

### v2.3.0
- **Fail-Fast 오류 처리**: 조용한 실패 및 부분 실패 완전 제거
  - 모든 분석 오류 발생 시 즉시 중단 (`AnalysisError` 전파)
  - 예외 삼키기 패턴 제거 (모든 `except` 블록에서 `raise`)
  - 배치 실패 시 즉시 중단 (부분 성공 불허)
- **LLM 결과 타입 검증 강화**
  - `validate_dict_result()` 유틸리티 메서드 도입
  - dict/tuple 외 타입 반환 시 즉시 `AnalysisError` 발생
  - `asyncio.gather` 결과를 tuple로 변환하여 일관성 유지
- **코드 품질 개선**
  - 중복 예외 처리 패턴 정리
  - DEBUG 로그 제거 및 로깅 최적화
  - 코드 가독성 향상

### v2.2.0
- **컨텍스트 인식 분석 시스템**: 중첩된 DML 구조에서 별칭 오인 방지
  - Phase 1.5: 부모 노드의 컨텍스트 생성 (Top-down)
  - 컨텍스트 전달: 자식 노드 분석 시 부모 컨텍스트 활용
  - 별칭 매핑 정보를 통한 정확한 테이블명 추출
  - 토큰 효율성: 스켈레톤 코드 압축 및 컨텍스트 토큰 제한 (300토큰)
- **Framework 컨텍스트 지원**: Java 코드 특성에 맞는 컨텍스트 생성
- **병렬 처리 개선**: 파일 레벨 병렬 처리 (최대 5개 파일 동시)

### v2.1.0
- **BaseStreamingAnalyzer 도입**: 공통 분석 프레임 분리 (템플릿 메서드 패턴)
- **Phase1/Phase2 구조 통일**: 모든 파일 AST → 모든 파일 LLM (병렬 처리)
- **자식→부모 의존성 보장**: `completion_event` 기반 순서 보장
- **성공/품질 추적**: `node.ok` 플래그로 불완전 요약 전파 방지
- **배치 실패 상세 정보**: 실패 배치 ID, 라인 범위, 에러 메시지 수집 및 스트림 출력
- **User Story 규칙 완화**: 모든 비즈니스 로직(CRUD, 배치 등)에서 User Story 도출
- **통계 필드 정식화**: `llm_batches_failed` 정식 필드 추가

### v2.0.0
- 2단계 분석 아키텍처 (Phase 1: AST → Phase 2: LLM)
- 이중 병렬 처리 (파일 레벨 + 청크 레벨)
- 메서드 호출 처리 단순화 (calls 배열 통합)
- 예외 처리 엄격화
- 환경변수 중앙 관리

---

*작성일: 2025-01-XX*
*마지막 업데이트: 2026-01-13*
