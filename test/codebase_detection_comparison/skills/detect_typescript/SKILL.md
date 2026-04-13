# detect_typescript — TypeScript / JavaScript 탐색 매뉴얼

**답안지 없음**. 탐색 팁만.

## 1순위: package.json

- `dependencies`, `devDependencies` 에 프레임워크·ORM·DB 드라이버 전부 명시.
- `scripts` 블록으로 빌드·실행 방식 파악.

## 2순위: 설정 파일

- `tsconfig.json` → TypeScript 확정
- `schema.prisma` → Prisma ORM 확정
- `nest-cli.json`, `next.config.*` → 프레임워크 확정
- `.env`, `docker-compose.yml` → DB URL

## 3순위: import 빈도 + 호기심

- ES module: `from 'package-name'` / CommonJS: `require('package-name')`
- 스코프 패키지 `@nestjs/...` 처럼 `@org/pkg` 형태 주목
- 미지 패키지면 `find_definition` / `read_file_range` 로 사용 패턴 추적

## 주요 탐색 신호

- `@Module`, `@Controller`, `@Injectable` → NestJS 계열
- `@Entity`, `@Column` → TypeORM
- `PrismaClient` → Prisma
- 파일명 `page.tsx`, `layout.tsx`, `app/` 디렉토리 → Next.js App Router
- `express()` 호출, `app.get()` → Express 계열
