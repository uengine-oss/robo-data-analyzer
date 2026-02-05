# Tables

## ORDER_HEADER (OrderHeader)
- mapping_type: ofbiz
- description: OFBiz 주문 헤더 엔티티. 주문의 기본 정보(유형, 상태, 통화, 합계, 생성/수정 타임스탬프 등)를 저장.
- columns:
  - orderId (id-ne) — Primary key (PK)
  - orderTypeId (id)
  - orderName (name)
  - externalId (id)
  - salesChannelEnumId (id)
  - orderDate (date-time)
  - priority (indicator)
  - entryDate (date-time)
  - pickSheetPrintedDate (date-time)
  - statusId (id)
  - createdBy (id-vlong)
  - firstAttemptOrderId (id)
  - currencyUom (id)
  - syncStatusId (id)
  - billingAccountId (id)
  - originFacilityId (id)
  - webSiteId (id)
  - productStoreId (id)
  - terminalId (id-long)
  - transactionId (id-long)
  - autoOrderShoppingListId (id)
  - needsInventoryIssuance (indicator)
  - isRushOrder (indicator)
  - internalCode (id)
  - remainingSubTotal (currency-amount)
  - grandTotal (currency-amount)
  - lastUpdatedStamp (date-time)
  - lastUpdatedTxStamp (date-time)
  - createdStamp (date-time)
  - createdTxStamp (date-time)

## ORDER_ITEM (OrderItem)
- mapping_type: ofbiz
- description: OFBiz 주문 품목 엔티티. 주문(orderId) 단위의 상품/수량/가격/배송예정/상태 등의 상세 라인 정보를 저장.
- columns:
  - orderId (id-ne)
  - orderItemSeqId (id-ne)
  - externalId (id)
  - orderItemTypeId (id)
  - orderItemGroupSeqId (id)
  - isItemGroupPrimary (indicator)
  - fromInventoryItemId (id)
  - budgetId (id)
  - budgetItemSeqId (id)
  - productId (id)
  - supplierProductId (id-long)
  - prodCatalogId (id)
  - productCategoryId (id)
  - isPromo (indicator)
  - quoteId (id)
  - quoteItemSeqId (id)
  - shoppingListId (id)
  - shoppingListItemSeqId (id)
  - subscriptionId (id)
  - deploymentId (id)
  - quantity (fixed-point)
  - cancelQuantity (fixed-point)
  - selectedAmount (fixed-point)
  - unitPrice (currency-precise)
  - unitListPrice (currency-precise)
  - unitAverageCost (currency-precise)
  - unitRecurringPrice (currency-precise)
  - isModifiedPrice (indicator)
  - recurringFreqUomId (id)
  - itemDescription (description)
  - comments (comment)
  - correspondingPoId (id)
  - statusId (id)
  - syncStatusId (id)
  - estimatedShipDate (date-time)
  - estimatedDeliveryDate (date-time)
  - autoCancelDate (date-time)
  - dontCancelSetDate (date-time)
  - dontCancelSetUserLogin (id-vlong)
  - shipBeforeDate (date-time)
  - shipAfterDate (date-time)
  - cancelBackOrderDate (date-time)
  - overrideGlAccountId (id)

## ORDER_TYPE (OrderType)
- mapping_type: ofbiz
- description: OrderHeader.orderTypeId가 참조하는 주문 유형 마스터 엔티티(본 블록에서는 relation으로만 참조됨).

## STATUS_ITEM (StatusItem)
- mapping_type: ofbiz
- description: OrderHeader.statusId, OrderItem.statusId가 참조하는 상태 코드 마스터 엔티티(본 블록에서는 relation으로만 참조됨).

## PRODUCT (Product)
- mapping_type: ofbiz
- description: OrderItem.productId가 참조하는 상품 마스터 엔티티(본 블록에서는 relation으로만 참조됨).

---

## 업데이트: 2026-02-05 17:50
### 테이블 정보
#### ORDER_HEADER (entity: OrderHeader)
- 설명: 주문 헤더(기본정보) 엔티티/테이블. 주문 유형, 상태, 통화, 합계 금액, 생성/수정 타임스탬프 등 주문 단위의 공통 정보를 저장한다.
- 컬럼: orderId, orderTypeId, orderName, externalId, salesChannelEnumId, orderDate, priority, entryDate, pickSheetPrintedDate, statusId 외 20개

#### ORDER_ITEM (entity: OrderItem)
- 설명: 주문 품목(라인) 엔티티/테이블. 주문별 품목 시퀀스, 상품, 수량/가격, 배송예정일, 상태 등의 상세 정보를 저장한다.
- 컬럼: orderId, orderItemSeqId, externalId, orderItemTypeId, orderItemGroupSeqId, isItemGroupPrimary, fromInventoryItemId, budgetId, budgetItemSeqId, productId 외 33개
