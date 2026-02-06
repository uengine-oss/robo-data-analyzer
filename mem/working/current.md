# 현재 작업
- 처리 중 파일: C:\uEngine\robo\ROBO-A\data\analysis\nontarget\SAMPLE\servicedef\order-services.xml
- 진행 상태: 청킹/요약/Table·Column 적재/관계(REFERS_TO, FROM/WRITES 일부) 생성/description 업데이트 완료
- 발견 사항:
  - OFBiz services definition: createOrderHeader/createOrderItem/updateOrderHeader/cancelOrderItem/getOrderHeaderInformation
  - 간접 참조 관계: (OrderService 클래스, createOrder 메서드, updateOrderStatus 메서드) -> 서비스 정의 블록(REFERS_TO)
  - 서비스 정의 기반 추정 관계: 서비스 블록 -> ORDER_HEADER/ORDER_ITEM (WRITES/FROM, confidence 낮음)
  - FK 확인/보강: ORDER_ITEM.orderId -> ORDER_HEADER.orderId (FK_TO_TABLE 및 FK_TO)
