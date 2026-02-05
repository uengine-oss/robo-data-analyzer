# Classes / Components (auto-updated)

- OrderMapperImpl (elementId: 4:6a70576a-0691-4137-a837-13c11bec1e05:6558)
  - 역할: MyBatis SqlSession 기반 주문 데이터 조회/생성 DAO/Mapper 구현체
  - 설정 연관: spring.datasource.* (DB 연결), mybatis.*
  - MyBatis 연관 근거: SqlSession에서 queryId "com.example.order.mapper.OrderMapper.*"를 직접 호출하며, mybatis.mapper-locations 로딩된 매퍼 정의에 의존

- OrderMapper (elementId: 4:6a70576a-0691-4137-a837-13c11bec1e05:6547)
  - 역할: MyBatis @Mapper 인터페이스. ORDER_HEADER 중심 CRUD SQL 바인딩
  - 설정 연관: spring.datasource.* (DB 연결), mybatis.*

- OrderService (elementId: 4:6a70576a-0691-4137-a837-13c11bec1e05:6629)
  - 역할: 주문 관련 서비스(일부 OFBiz Delegator/EntityQuery 사용)
  - 설정 연관: DB/영속성 레이어 전반 (간접), spring.jpa.* (활성 시)

- OrderController (elementId: 4:6a70576a-0691-4137-a837-13c11bec1e05:6527)
  - 역할: /api/orders REST 컨트롤러
