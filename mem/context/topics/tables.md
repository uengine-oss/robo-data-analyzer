# 발견된 테이블

(아직 application.yaml에서는 물리 테이블 정의/참조가 직접 나타나지 않음)

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: ofbiz
- schema: public
- 컬럼: orderId, orderTypeId, orderName, externalId, salesChannelEnumId, orderDate, priority, entryDate, pickSheetPrintedDate, statusId
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\entitydef\order-entitymodel.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: ofbiz
- schema: public
- 컬럼: orderId, orderItemSeqId, externalId, orderItemTypeId, orderItemGroupSeqId, isItemGroupPrimary, fromInventoryItemId, budgetId, budgetItemSeqId, productId
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\entitydef\order-entitymodel.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_type_id, order_name, order_date, status_id, currency_uom, grand_total
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_item_seq_id, product_id, quantity, unit_price, item_description
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: status_id, order_type_id, order_date, order_id
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_item_seq_id
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_type_id, order_name, order_date, status_id, grand_total
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 컬럼: order_item_seq_id, product_id, quantity, unit_price
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_type_id, order_name, order_date, status_id, currency_uom, grand_total, created_stamp
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_item_seq_id, product_id, quantity, unit_price, item_description, status_id
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, status_id, last_updated_stamp
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_ITEM
- entity_name: OrderItem
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_item_seq_id, quantity, unit_price, item_description, status_id
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml

## ORDER_HEADER
- entity_name: OrderHeader
- mapping_type: mybatis
- schema: public
- 컬럼: order_id, order_type_id, order_name, order_date, status_id, currency_uom, grand_total, created_stamp, last_updated_stamp
- 발견 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\mapper\OrderMapper.xml
