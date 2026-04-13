# detect_java — Java / Kotlin / Groovy / Scala 탐색 매뉴얼

**답안지 없음**. 이 문서는 탐색 팁입니다. 구체적 프레임워크 이름(Spring Boot, Quarkus 등)은 AI 가 증거로 직접 추론.

## 1순위: 빌드 파일

- `pom.xml` (Maven), `build.gradle` / `build.gradle.kts` (Gradle), `settings.gradle`
- `<dependency>` 또는 `implementation` 블록이 핵심. 의존성 80% 확정.

## 2순위: 설정 파일

- `application.yml`, `application.properties`, `application-*.yml`, `bootstrap.yml`
- DB 연결(`jdbc:mysql://`, `jdbc:postgresql://`), 프로파일, 포트 여기에.

## 3순위: 패키지 import 빈도 + 호기심 탐색

- `grep_imports('.java,.kt')` → 빈도 분석
- 미지의 패키지 `xx.yy.zz.*` 가 자주 보이면:
  - `find_definition('ClassName', '.java,.kt')` → 선언 추적
  - 주석 + 어노테이션으로 프레임워크 실마리

## 주요 탐색 신호

- `@SpringBootApplication`, `@RestController`, `@Service` 등 어노테이션
- `@Entity`, `@Table`, `@Column` → ORM 의심 (JPA/Hibernate 계열)
- `@Mapper`, `SqlSessionFactory` → MyBatis 계열
- `pom.xml` 의 `<parent>` 태그 → 프레임워크 부모 POM
- 패키지 경로 `com.company.project` → 조직/도메인 단서
