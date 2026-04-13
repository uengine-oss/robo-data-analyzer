# detect_c_legacy — C / Pro*C / COBOL 레거시 탐색 매뉴얼

**답안지 없음**. 이 문서는 "C 레거시 프로젝트를 만났을 때 어디를 먼저 볼지" 에 대한 탐색 팁입니다.
ProFrame·Tmax·Pro*C 같은 구체적 이름은 여기 안 적습니다 — AI 가 코드 증거로 직접 추론합니다.

## 1순위: Makefile

- `Makefile`, `*.mk`, `CMakeLists.txt`
- `LDFLAGS` / `LIBS` 의 `-l...` 플래그가 핵심 — 어떤 라이브러리/미들웨어 쓰는지 즉시 드러남.

## 2순위: #include 접두어 클러스터

- `grep_imports('.c,.h,.ec,.pc')` → 빈도 분석
- 표준 라이브러리(`stdio`, `pthread`, `unistd` 등) 제외하면 남는 접두어가 프레임워크 후보
- 같은 접두어 3~5개 헤더가 반복 → 독점 프레임워크 의심

## 3순위: 호기심 따라가기 (Go to Definition)

미지 접두어 `xxx` 발견 시:

```
1. collect_unknown_evidence('xxx')
   → 샘플 5파일의 주석/매크로/함수/구조체/도메인어 JSON

2. 흥미로운 함수 호출 보이면:
   find_definition('funcname', '.c,.h')
   → 정의부 파일·줄번호 반환

3. read_file_range(정의_파일, 정의_줄-5, 정의_줄+40)
   → 함수/매크로 구현 읽어보기

4. 구조체 이름이 독특하면:
   find_definition('struct_name')

5. 주석에 고유명사(회사명, 제품명) 있는지:
   grep_pattern('ProductName|VendorName', '.c,.h')
```

## 주요 탐색 신호

- **함수 네이밍 `a000_`, `b200_` 같은 3자리 숫자 클러스터** → EMB 스타일 레이어 아키텍처 의심
- **`*_io_t`, `*_in_t`, `*_out_t` typedef** → 코드 생성 기반 IO 구조체 의심
- **`XXX_TRY` / `XXX_CATCH` 매크로** → 자체 예외처리 프레임워크
- **한국어 주석 + C 코드** → 한국 금융/공공 시스템 가능성 높음
- **DB 접근이 함수 호출 형태 (`func(테이블명, &in, &out)`)** → DBIO 패턴
- **`EXEC SQL`** → Oracle Pro*C Embedded SQL
- **`.pc`, `.ec` 확장자** → Pro*C 전처리 대상 파일

## 자주 놓치는 것

- `#include <xxx.h>` 만 보지 말고 **구조체 typedef 이름의 접두어**도 독점 프레임워크 단서
- 한국어 주석의 **업무 도메인 용어** (`_cd`, `_amt`, `_yn` 접미어) 는 업종 추정에 도움
- Makefile 이 없어도 **파일 명명 규칙** (`pdb_<table>_[siud]\d+\.h`) 으로 DBIO 시스템 추정 가능
