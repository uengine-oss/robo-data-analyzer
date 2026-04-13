# detect_go — Go 탐색 매뉴얼

**답안지 없음**. 탐색 팁만.

## 1순위: go.mod

- `require` 블록의 의존성이 거의 전부.
- `github.com/gin-gonic/gin`, `gorm.io/gorm` 같은 경로가 그대로 프레임워크 이름.

## 2순위: 멀티라인 import 블록

- Go 는 import 가 `()` 블록. `grep_imports('.go')` 가 각 라인 문자열 캡처.

## 3순위: 호기심 탐색

- 미지 경로 `github.com/vendor/xyz` 가 반복되면:
  - `find_definition('XyzHandler', '.go')` 로 사용 방식 추적
  - `read_file_range` 로 초기화 코드 확인

## 주요 탐색 신호

- `gin.Default()`, `echo.New()` → 웹 프레임워크
- `db.AutoMigrate()` → GORM 의심
- `//go:generate` 지시문 → 코드 생성 기반
- 파일명 `main.go` 위치 → 엔트리포인트
