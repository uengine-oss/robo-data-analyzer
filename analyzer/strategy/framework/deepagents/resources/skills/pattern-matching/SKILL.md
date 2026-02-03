---
name: pattern-matching
description: external_hints 패턴 분석 및 적절한 해석 전략 선택. 힌트 패턴을 파싱하여 올바른 파서/도구로 라우팅.
---

# Pattern Matching Skill

`external_hints`의 패턴을 분석하여 적절한 해석 전략과 도구를 선택합니다.

## When to Use

- 새로운 후보(candidate) 처리 시작 시
- `external_hints` 패턴을 분석하여 어떤 스킬/도구를 사용할지 결정할 때
- 복합 패턴 (여러 힌트 조합) 처리 시

## Pattern Categories

### 1. OFBiz Patterns (→ ofbiz-parser skill)

| 패턴 | 의미 | 예시 |
|------|------|------|
| `entity=XXX` | OFBiz 엔티티 참조 | `entity=OrderHeader` |
| `service=XXX` | OFBiz 서비스 참조 | `service=createOrder` |
| `delegator.XXX` | Delegator 메서드 호출 | `delegator.findByPrimaryKey` |

### 2. MyBatis Patterns (→ mybatis-parser skill)

| 패턴 | 의미 | 예시 |
|------|------|------|
| `mapper=XXX` | MyBatis 매퍼 네임스페이스 | `mapper=OrderMapper` |
| `queryId=XXX` | SQL 쿼리 ID | `queryId=selectOrderById` |
| `sqlSession.XXX` | SqlSession 메서드 호출 | `sqlSession.selectList` |

### 3. JPA Patterns (→ jpa-parser skill)

| 패턴 | 의미 | 예시 |
|------|------|------|
| `jpa_entity=XXX` | JPA 엔티티 클래스 | `jpa_entity=OrderEntity` |
| `orm_mapping=XXX` | orm.xml 매핑 | `orm_mapping=Order` |
| `entityManager.XXX` | EntityManager 호출 | `entityManager.find` |

### 4. Direct SQL Patterns

| 패턴 | 의미 | 예시 |
|------|------|------|
| `table=XXX` | 직접 테이블 참조 | `table=ORDERS` |
| `sql=XXX` | 인라인 SQL 조각 | `sql=SELECT * FROM` |

### 5. Config File Patterns

| 패턴 | 의미 | 예시 |
|------|------|------|
| `config=XXX` | 설정 파일 참조 | `config=database.yml` |
| `property=XXX` | 설정 속성 참조 | `property=db.table.prefix` |

## Decision Flow

```
external_hints 분석
    │
    ├─ entity=* 또는 service=* ──→ OFBiz Parser
    │
    ├─ mapper=* 또는 queryId=* ──→ MyBatis Parser
    │
    ├─ jpa_entity=* ──────────────→ JPA Parser (Neo4j 조회)
    │
    ├─ table=* ───────────────────→ 직접 DDL 확인
    │
    └─ 패턴 불명확 ────────────────→ grep으로 파일 검색 후 결정
```

## Hint Parsing Rules

### 단일 힌트

```
entity=OrderHeader
  └─ key=value 형식
  └─ key: "entity"
  └─ value: "OrderHeader"
```

### 복합 힌트

```
["entity=OrderHeader", "service=createOrder"]
  └─ 순서대로 처리
  └─ 관계 파악 (같은 도메인)
```

### 동적 값

```
entity=${entityName}
  └─ 동적 표현식
  └─ 변수 바인딩 추적 필요
  └─ confidence 낮게 설정
```

## Examples

### 명확한 패턴

```
# 입력
external_hints: ["entity=Product"]

# 분석
- 패턴: entity=XXX (OFBiz)
- 스킬: ofbiz-parser
- 도구: parse_ofbiz_entity
- 검색 경로: /docs/entitydef/
```

### 복합 패턴

```
# 입력
external_hints: ["service=createOrder", "entity=OrderHeader"]

# 분석
- 패턴 1: service=createOrder (OFBiz 서비스)
- 패턴 2: entity=OrderHeader (OFBiz 엔티티)
- 전략: 서비스 → 엔티티 순서로 해석
- 관계: 서비스가 엔티티를 사용
```

### 불명확한 패턴

```
# 입력
external_hints: ["Order", "findById"]

# 분석
- key=value 형식 아님
- grep으로 전체 검색
- 결과 기반 패턴 추론
- confidence 낮게 설정 (0.5 이하)
```

## Confidence Guidelines

| 상황 | confidence |
|------|------------|
| key=value 형식 + 파일 발견 | 0.9 |
| key=value 형식 + 파일 미발견 | 0.6 |
| 패턴 불명확 + 추론 성공 | 0.5 |
| 동적 값 (${...}) | 0.3 |
| 해석 불가 | 0.0 |

## Tool Selection

| 힌트 패턴 | 첫 번째 도구 | 두 번째 도구 |
|-----------|-------------|-------------|
| entity=* | grep /docs/entitydef/ | parse_ofbiz_entity |
| service=* | grep /docs/servicedef/ | parse_ofbiz_service |
| mapper=* | grep /docs/mapper/ | parse_mybatis_mapper |
| queryId=* | grep /docs/mapper/ | parse_mybatis_mapper |
| jpa_entity=* | query_neo4j | (어노테이션 분석) |
| table=* | read_file /ddl/ | (DDL 파싱) |

