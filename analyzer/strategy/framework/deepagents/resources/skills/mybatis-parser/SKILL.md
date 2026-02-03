---
name: mybatis-parser
description: MyBatis mapper XML 파일 파싱 및 SQL 쿼리/테이블 추출. external_hints에 mapper=XXX 또는 queryId=XXX 패턴이 있을 때 사용.
---

# MyBatis Parser Skill

MyBatis/iBatis mapper XML 파일을 파싱하여 SQL 쿼리와 테이블 매핑 정보를 추출합니다.

## When to Use

- `external_hints`에 `mapper=XXX` 패턴이 있을 때
- `external_hints`에 `queryId=XXX` 패턴이 있을 때
- `SqlSession.selectList()`, `selectOne()`, `insert()`, `update()`, `delete()` 호출 분석 시
- `@Mapper` 인터페이스와 연결된 XML 분석 시

## File Patterns

| 힌트 패턴 | 대상 파일 경로 |
|-----------|---------------|
| `mapper=OrderMapper` | `/docs/mapper/OrderMapper.xml` |
| `queryId=selectOrderById` | `/docs/mapper/*.xml` (grep 후 파싱) |

## Tools

### parse_mybatis_mapper

MyBatis mapper XML 파일에서 SQL 문과 테이블 정보를 파싱합니다.

```
parse_mybatis_mapper(file_path="/docs/mapper/OrderMapper.xml")
```

**반환값:**
- `namespace`: 매퍼 네임스페이스
- `statements`: SQL 문 목록
  - `id`: 쿼리 ID
  - `type`: select/insert/update/delete
  - `tables`: 참조 테이블 목록
  - `result_map`: 결과 매핑 (있는 경우)

## Examples

### 매퍼 해석 예시

```
# 입력
external_hints: ["queryId=selectOrderById"]

# 단계
1. grep으로 /docs/mapper/ 에서 "selectOrderById" 검색
2. 발견된 파일에서 parse_mybatis_mapper 실행
3. 해당 쿼리의 tables 추출 → ["ORDERS", "ORDER_ITEMS"]
```

### 네임스페이스로 검색

```
# 입력
external_hints: ["mapper=com.example.OrderMapper"]

# 단계
1. grep으로 namespace="com.example.OrderMapper" 검색
2. 파일에서 모든 SQL 문과 테이블 추출
```

## Mapper XML Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" 
  "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.example.mapper.OrderMapper">
    
    <resultMap id="OrderResultMap" type="Order">
        <id column="ORDER_ID" property="orderId"/>
        <result column="ORDER_DATE" property="orderDate"/>
    </resultMap>
    
    <select id="selectOrderById" resultMap="OrderResultMap">
        SELECT o.ORDER_ID, o.ORDER_DATE, oi.ITEM_ID
        FROM ORDERS o
        LEFT JOIN ORDER_ITEMS oi ON o.ORDER_ID = oi.ORDER_ID
        WHERE o.ORDER_ID = #{orderId}
    </select>
    
    <insert id="insertOrder">
        INSERT INTO ORDERS (ORDER_ID, ORDER_DATE, STATUS)
        VALUES (#{orderId}, #{orderDate}, #{status})
    </insert>
    
</mapper>
```

## SQL Parsing Notes

1. **테이블 추출**: FROM, JOIN, INTO, UPDATE 절에서 테이블명 추출
2. **별칭 처리**: `FROM ORDERS o` → 테이블은 `ORDERS`, 별칭은 `o`
3. **동적 SQL**: `<if>`, `<choose>`, `<foreach>` 내부도 파싱
4. **파라미터**: `#{param}`, `${param}` 구분 ($ 는 SQL 인젝션 위험)

## Common Patterns

| Java 코드 | 대응 XML |
|-----------|----------|
| `selectOne("selectOrderById", id)` | `<select id="selectOrderById">` |
| `insert("insertOrder", order)` | `<insert id="insertOrder">` |
| `mapper.selectOrderById(id)` | 인터페이스 메서드 → XML의 동일 id |

