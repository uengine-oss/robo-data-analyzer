# Relations (candidates log)

## 2026-02-07
- order-entitymodel.xml blocks refer to tables ORDER_HEADER, ORDER_ITEM (ofbiz entity definitions)
  - block 2936 -> ORDER_HEADER
  - block 2938 -> ORDER_ITEM
  - block 2937 -> ORDER_HEADER (closing)
  - block 2939 -> ORDER_ITEM (closing)
- Column-level mentions (not FK inferred): ORDER_ITEM.orderId, ORDER_ITEM.orderItemSeqId, ORDER_ITEM.productId; ORDER_HEADER.orderId, ORDER_HEADER.orderTypeId, ORDER_HEADER.statusId

- application.yaml blocks define config keys used by Spring Boot / MyBatis / OFBiz modules
  - block 2928 (spring.*) -> refers to Spring Boot app name + datasource + JPA settings
  - block 2929 (mybatis.*) -> refers to MyBatis mapper-locations/type-aliases-package
  - block 2930 (ofbiz.*) -> refers to OFBiz delegator/entity-engine/service-engine xml paths
  - block 2931 (order.*) -> refers to OrderController @Value injected keys (order.max-items-per-order, order.default-currency)
  - block 2932 (logging.*) -> refers to logging categories org.hibernate.SQL/org.mybatis/com.example.order
