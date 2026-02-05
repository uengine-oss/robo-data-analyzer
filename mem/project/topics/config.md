## Logging
- logging.level.com.example.order=DEBUG
  - 영향: com.example.order 패키지 하위 클래스/메서드의 DEBUG 로그 활성화
  - 연결: OrderController(4:6a70576a-0691-4137-a837-13c11bec1e05:6149), OrderMapperImpl(4:6a70576a-0691-4137-a837-13c11bec1e05:6180), OrderMapper(4:6a70576a-0691-4137-a837-13c11bec1e05:6169)
- logging.level.org.hibernate.SQL=DEBUG
  - 영향: Hibernate SQL 로거 DEBUG (코드 내 직접 참조 노드 미발견)
- logging.level.org.mybatis=DEBUG
  - 영향: MyBatis 내부 로거 DEBUG (코드 내 직접 참조 노드 미발견)

## Spring Application
- spring.application.name=order-service
  - 영향: Spring Boot 애플리케이션 이름(서비스 식별자)을 order-service로 설정

## Spring Datasource (PostgreSQL)
- spring.datasource.url=jdbc:postgresql://localhost:5432/orderdb
  - 영향: orderdb(PostgreSQL) 접속 JDBC URL
- spring.datasource.username=order_user
- spring.datasource.password=${DB_PASSWORD:secret}
  - 영향: 환경변수 DB_PASSWORD가 없으면 기본값 secret 사용
- spring.datasource.driver-class-name=org.postgresql.Driver
- spring.datasource.hikari.maximum-pool-size=10
- spring.datasource.hikari.minimum-idle=5
- spring.datasource.hikari.connection-timeout=30000

## Spring JPA / Hibernate
- spring.jpa.hibernate.ddl-auto=validate
  - 영향: 스키마 자동 생성/변경 없이 매핑 검증만 수행
- spring.jpa.show-sql=true
- spring.jpa.properties.hibernate.format_sql=true
- spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect

## MyBatis
- mybatis.mapper-locations=classpath:mapper/**/*.xml
  - 영향: MyBatis 매퍼 XML을 classpath:mapper 하위(하위 디렉토리 포함)에서 로딩
- mybatis.type-aliases-package=com.example.order.domain
  - 영향: com.example.order.domain 패키지의 도메인 클래스를 타입 별칭(alias)으로 등록
- mybatis.configuration.map-underscore-to-camel-case=true
  - 영향: DB 컬럼의 snake_case를 Java camelCase 프로퍼티로 자동 매핑
- mybatis.configuration.lazy-loading-enabled=true
  - 영향: 연관 객체/컬렉션 지연 로딩(lazy loading) 활성화
