---
name: jpa-parser
description: JPA/Hibernate 어노테이션 및 orm.xml 파싱으로 엔티티-테이블 매핑 추출. external_hints에 jpa_entity=XXX 패턴이 있을 때 사용.
---

# JPA Parser Skill

JPA(Java Persistence API) 어노테이션과 orm.xml 파일을 파싱하여 엔티티-테이블 매핑 정보를 추출합니다.

## When to Use

- `external_hints`에 `jpa_entity=XXX` 패턴이 있을 때
- `@Entity`, `@Table` 어노테이션이 있는 클래스 분석 시
- `EntityManager` 호출 분석 시
- `orm.xml` 파일 분석 시

## File Patterns

| 힌트 패턴 | 대상 |
|-----------|------|
| `jpa_entity=Order` | Neo4j에서 `@Entity` 클래스 검색 |
| `orm_mapping=Order` | `/docs/META-INF/orm.xml` |

## Tools

### parse_jpa_entity

Neo4j에서 JPA 엔티티 클래스의 어노테이션 정보를 조회합니다.

```
query_neo4j(query='''
MATCH (c:CLASS)-[:HAS_ANNOTATION]->(a:ANNOTATION)
WHERE c.name = "Order" AND a.name IN ["Entity", "Table"]
RETURN c.name, a.name, a.attributes
''')
```

### parse_orm_xml

JPA orm.xml 파일에서 엔티티-테이블 매핑을 파싱합니다.

```
parse_jpa_orm(file_path="/docs/META-INF/orm.xml")
```

**반환값:**
- `entity_name`: 엔티티 클래스명
- `table_name`: 매핑된 테이블명
- `columns`: 컬럼 매핑 목록

## Examples

### 어노테이션 기반 해석

```
# 입력
external_hints: ["jpa_entity=OrderEntity"]

# 단계
1. Neo4j에서 OrderEntity 클래스의 @Table 어노테이션 조회
2. @Table(name="ORDERS") → 테이블명 "ORDERS" 추출
3. @Column 어노테이션으로 컬럼 매핑 추출
```

### orm.xml 기반 해석

```
# 입력
external_hints: ["orm_mapping=Order"]

# 단계
1. /docs/META-INF/orm.xml 읽기
2. <entity class="Order"> 엘리먼트 찾기
3. <table name="ORDERS"/> 에서 테이블명 추출
```

## JPA Annotation Patterns

```java
@Entity
@Table(name = "ORDERS", schema = "SALES")
public class Order {
    
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "ORDER_ID")
    private Long id;
    
    @Column(name = "ORDER_DATE", nullable = false)
    private LocalDateTime orderDate;
    
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "CUSTOMER_ID")
    private Customer customer;
    
    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL)
    private List<OrderItem> items;
}
```

## orm.xml Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<entity-mappings xmlns="http://xmlns.jcp.org/xml/ns/persistence/orm"
                 version="2.2">
    
    <entity class="com.example.Order">
        <table name="ORDERS" schema="SALES"/>
        <attributes>
            <id name="id">
                <column name="ORDER_ID"/>
                <generated-value strategy="IDENTITY"/>
            </id>
            <basic name="orderDate">
                <column name="ORDER_DATE" nullable="false"/>
            </basic>
        </attributes>
    </entity>
    
</entity-mappings>
```

## Resolution Strategy

1. **어노테이션 우선**: 코드의 `@Table` 어노테이션 확인
2. **orm.xml 대체**: 어노테이션 없으면 orm.xml 확인
3. **기본 명명 규칙**: 둘 다 없으면 클래스명 → 테이블명 변환
   - `OrderEntity` → `ORDER_ENTITY` (스네이크 케이스)

## EntityManager Patterns

| Java 코드 | 분석 방향 |
|-----------|----------|
| `em.find(Order.class, id)` | Order 엔티티의 테이블 확인 |
| `em.createQuery("FROM Order")` | JPQL → 엔티티 → 테이블 |
| `em.createNativeQuery("SELECT * FROM ORDERS")` | 직접 테이블 참조 |

