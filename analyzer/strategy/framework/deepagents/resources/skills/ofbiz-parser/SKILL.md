---
name: ofbiz-parser
description: OFBiz entitydef/servicedef XML 파일 파싱 및 엔티티/서비스 추출. external_hints에 entity=XXX 또는 service=XXX 패턴이 있을 때 사용.
---

# OFBiz Parser Skill

Apache OFBiz 프레임워크의 엔티티 정의(entitydef)와 서비스 정의(servicedef) XML 파일을 파싱합니다.

## When to Use

- `external_hints`에 `entity=XXX` 패턴이 있을 때
- `external_hints`에 `service=XXX` 패턴이 있을 때
- `GenericValue`, `Delegator`, `EntityQuery` 관련 코드 분석 시
- `dispatcher.runSync()`, `dispatcher.runAsync()` 호출 분석 시

## File Patterns

| 힌트 패턴 | 대상 파일 경로 |
|-----------|---------------|
| `entity=Product` | `/docs/entitydef/*-entitymodel.xml` |
| `entity=OrderHeader` | `/docs/entitydef/*-entitymodel.xml` |
| `service=createOrder` | `/docs/servicedef/*-services.xml` |

## Tools

### parse_ofbiz_entity

OFBiz entitydef XML 파일에서 엔티티 정의를 파싱합니다.

```
parse_ofbiz_entity(file_path="/docs/entitydef/order-entitymodel.xml")
```

**반환값:**
- `entity_name`: 엔티티 이름
- `table_name`: 매핑된 테이블 이름
- `fields`: 필드 목록 (name, type, col-name)
- `relations`: 관계 목록

### parse_ofbiz_service

OFBiz servicedef XML 파일에서 서비스 정의를 파싱합니다.

```
parse_ofbiz_service(file_path="/docs/servicedef/order-services.xml")
```

**반환값:**
- `service_name`: 서비스 이름
- `engine`: 실행 엔진 (java, entity-auto 등)
- `invoke`: 호출 메서드
- `attributes`: 입출력 속성

## Examples

### 엔티티 해석 예시

```
# 입력
external_hints: ["entity=OrderHeader"]

# 단계
1. grep으로 /docs/entitydef/ 에서 "OrderHeader" 검색
2. 발견된 파일에서 parse_ofbiz_entity 실행
3. table_name 추출 → "ORDER_HEADER"
```

### 서비스 해석 예시

```
# 입력
external_hints: ["service=createOrder"]

# 단계
1. grep으로 /docs/servicedef/ 에서 "createOrder" 검색
2. 발견된 파일에서 parse_ofbiz_service 실행
3. 연관 엔티티/테이블 추출
```

## Entity XML Structure

```xml
<entity entity-name="OrderHeader" table-name="ORDER_HEADER">
    <field name="orderId" type="id" col-name="ORDER_ID"/>
    <field name="orderTypeId" type="id"/>
    <prim-key field="orderId"/>
    <relation type="one" fk-name="ORD_HDR_STYP" rel-entity-name="StatusItem">
        <key-map field-name="statusId"/>
    </relation>
</entity>
```

## Service XML Structure

```xml
<service name="createOrder" engine="java" 
         location="org.apache.ofbiz.order.order.OrderServices" 
         invoke="createOrder">
    <attribute name="orderTypeId" type="String" mode="IN"/>
    <attribute name="orderId" type="String" mode="OUT"/>
</service>
```

