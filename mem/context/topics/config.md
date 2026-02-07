## spring.application.name
- 값: order-service
- 영향: Spring Boot 애플리케이션 식별자
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.url
- 값: jdbc:postgresql://localhost:5432/orderdb
- 영향: PostgreSQL DB 연결 (db=orderdb)
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.username
- 값: order_user
- 영향: DB 접속 계정
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.password
- 값: ${DB_PASSWORD:secret}
- 영향: DB 접속 비밀번호(환경변수 DB_PASSWORD 기본값 secret)
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.driver-class-name
- 값: org.postgresql.Driver
- 영향: JDBC 드라이버 지정
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.hikari.maximum-pool-size
- 값: 10
- 영향: HikariCP 최대 커넥션 풀 크기
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.hikari.minimum-idle
- 값: 5
- 영향: HikariCP 최소 유휴 커넥션
- 발견 파일: SAMPLE/config/application.yaml

## spring.datasource.hikari.connection-timeout
- 값: 30000
- 영향: HikariCP 커넥션 타임아웃(ms)
- 발견 파일: SAMPLE/config/application.yaml

## spring.jpa.hibernate.ddl-auto
- 값: validate
- 영향: 스키마 검증(DDL 자동 생성/수정 없음)
- 발견 파일: SAMPLE/config/application.yaml

## spring.jpa.show-sql
- 값: true
- 영향: 실행 SQL 로깅
- 발견 파일: SAMPLE/config/application.yaml

## spring.jpa.properties.hibernate.format_sql
- 값: true
- 영향: SQL 포매팅 로깅
- 발견 파일: SAMPLE/config/application.yaml

## spring.jpa.properties.hibernate.dialect
- 값: org.hibernate.dialect.PostgreSQLDialect
- 영향: Hibernate 방언(PostgreSQL)
- 발견 파일: SAMPLE/config/application.yaml

## mybatis.mapper-locations
- 값: classpath:mapper/**/*.xml
- 영향: MyBatis 매퍼 XML 경로
- 발견 파일: SAMPLE/config/application.yaml

## mybatis.type-aliases-package
- 값: com.example.order.domain
- 영향: MyBatis 타입 별칭 패키지(도메인 엔티티)
- 발견 파일: SAMPLE/config/application.yaml

## mybatis.configuration.map-underscore-to-camel-case
- 값: true
- 영향: 컬럼명 underscore → camelCase 자동 매핑
- 발견 파일: SAMPLE/config/application.yaml

## mybatis.configuration.lazy-loading-enabled
- 값: true
- 영향: 지연 로딩 활성화
- 발견 파일: SAMPLE/config/application.yaml

## ofbiz.delegator.name
- 값: default
- 영향: OFBiz delegator 식별자
- 발견 파일: SAMPLE/config/application.yaml

## ofbiz.delegator.datasource
- 값: localpostgres
- 영향: OFBiz 데이터소스 이름
- 발견 파일: SAMPLE/config/application.yaml

## ofbiz.entity-engine.xml-path
- 값: entitydefs/
- 영향: OFBiz entity-engine XML 정의 경로
- 발견 파일: SAMPLE/config/application.yaml

## ofbiz.entity-engine.cache-enabled
- 값: true
- 영향: OFBiz 엔티티 캐시 활성화
- 발견 파일: SAMPLE/config/application.yaml

## ofbiz.service-engine.xml-path
- 값: servicedef/
- 영향: OFBiz service-engine XML 정의 경로
- 발견 파일: SAMPLE/config/application.yaml

## order.default-currency
- 값: USD
- 영향: 주문 기본 통화
- 발견 파일: SAMPLE/config/application.yaml

## order.max-items-per-order
- 값: 100
- 영향: 주문당 최대 품목 수 제한
- 발견 파일: SAMPLE/config/application.yaml

## order.auto-cancel.enabled
- 값: true
- 영향: 장기 미결 주문 자동취소 기능 토글
- 발견 파일: SAMPLE/config/application.yaml

## order.auto-cancel.days
- 값: 30
- 영향: 자동취소 기준(일)
- 발견 파일: SAMPLE/config/application.yaml

## logging.level.com.example.order
- 값: DEBUG
- 영향: 주문 서비스 패키지 로그 레벨
- 발견 파일: SAMPLE/config/application.yaml

## logging.level.org.hibernate.SQL
- 값: DEBUG
- 영향: Hibernate SQL 로그 레벨
- 발견 파일: SAMPLE/config/application.yaml

## logging.level.org.mybatis
- 값: DEBUG
- 영향: MyBatis 로그 레벨
- 발견 파일: SAMPLE/config/application.yaml
