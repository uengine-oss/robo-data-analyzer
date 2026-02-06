## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis (추가), ofbiz
- schema: public
- db: default
- description: 주문 헤더(마스터) 테이블. 주문을 식별하는 orderId를 중심으로 주문 유형(orderTypeId), 주문명(orderName), 주문일시(orderDate), 주문 상태(statusId), 통화(currencyUom) 등의 핵심 속성을 관리하며, ORDER_ITEM(주문 라인)의 부모로서 주문 단위 트랜잭션의 기준이 된다.
- PK: orderId
- 컬럼: orderId(PK), orderTypeId, orderName, orderDate, statusId, currencyUom, grandTotal, createdStamp(INSERT에서 생성), lastUpdatedStamp(UPDATE에서 갱신)
- 사용처(MyBatis): OrderMapper.xml
  - SELECT: selectOrderById/selectOrdersByStatus/selectOrderWithItems (FROM)
  - INSERT: insertOrderHeader (WRITES)
  - UPDATE: updateOrderStatus (WRITES)
- 사용처(OFBiz servicedef): order-services.xml
  - createOrderHeader (WRITES 추정, pk OUT/nonpk IN)
  - updateOrderHeader (WRITES 추정)
  - getOrderHeaderInformation (FROM 추정)

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis (추가), ofbiz
- schema: public
- db: default
- description: 주문 상세(라인) 테이블. ORDER_HEADER의 특정 주문(orderId)에 속한 개별 품목을 orderItemSeqId로 구분해 관리하며, 상품(productId), 수량(quantity), 단가(unitPrice), 라인 상태(statusId), 라인 설명(itemDescription) 등 주문 라인 처리에 필요한 값을 저장한다.
- PK: (orderId, orderItemSeqId)
- 컬럼: orderId(PK, FK→ORDER_HEADER.orderId), orderItemSeqId(PK), productId, quantity, unitPrice, itemDescription, statusId
- 사용처(MyBatis): OrderMapper.xml
  - SELECT: selectOrderWithItems (FROM)
  - INSERT: insertOrderItem (WRITES)
  - UPDATE: updateOrderItem (WRITES)
  - DELETE: deleteOrderItem (WRITES)
- 사용처(OFBiz servicedef): order-services.xml
  - createOrderItem (WRITES 추정, orderItemSeqId OUT)
  - cancelOrderItem (WRITES 추정)

## PRODUCT (추정)
- entity_name: Product
- mapping_type: ofbiz
- schema: public
- db: default
- description: (추정) OFBiz Product 엔티티/테이블. ORDER_ITEM.productId에서 참조.

## ORDER_TYPE (추정)
- entity_name: OrderType
- mapping_type: ofbiz
- schema: public
- db: default
- description: (추정) OFBiz OrderType 엔티티/테이블. ORDER_HEADER.orderTypeId에서 참조.

## STATUS_ITEM (추정)
- entity_name: StatusItem
- mapping_type: ofbiz
- schema: public
- db: default
- description: (추정) OFBiz StatusItem 엔티티/테이블. ORDER_HEADER.statusId 및 ORDER_ITEM.statusId에서 참조 가능.
