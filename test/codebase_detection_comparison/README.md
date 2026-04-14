# 코드베이스 탐지 v4

프레임워크와 DB 접근 패턴을 자연어 1~2문단으로 설명하는 Deep Agent 모듈.

## 설계 원칙

- **항상 Deep Agent**. 우회 경로 없음.
- **패턴 기반 조기 종료**. 매니페스트/대표 파일에서 결정적 패턴이 보이면 즉시 submit.
- **힌트 금지**. 프레임워크 이름 하드코딩 없음. LLM 이 수집 증거만 보고 판단.
- **무작정 전수 조사 금지**. import 는 30 파일 랜덤 샘플.

## 흐름

```
Deep Agent 루프
├─ turn 1: cache_lookup  (히트 → 즉시 submit)
├─ turn 2: quick_collect  (확장자/매니페스트/설정/import/대표파일)
├─ turn 3: 명확 → submit_report  /  모호 → task(file-tracker) → submit
```

## 사용법

```bash
python -m test.codebase_detection_comparison.analyze c:/uEngine/robo/data/source/apascrt00010t02_20260406
```

프로그래밍:

```python
import asyncio
from test.codebase_detection_comparison import analyze

result = asyncio.run(analyze("/some/project"))
print(result["summary"])
```

## 파일 구조

- `analyze.py` — CLI + 진입점
- `agent.py` — Deep Agent 구성 (도구 3 개 + file-tracker 서브에이전트)
- `collector.py` — 결정론 수집 (1 초 목표)
- `tools/tree_sitter_tools.py` — AST import 샘플링
- `tools/cache.py` — 지문 해시 기반 캐시
- `prompts/` — 컨텍스트 템플릿, 서브에이전트 프롬프트
- `skills/codebase-analysis/SKILL.md` — Deep Agent 행동 지침
- `skills/auto_generated/` — 캐시 저장소

## 성능 목표

| 시나리오 | 시간 | 도구 호출 |
|---|---|---|
| 매니페스트 있음 (Spring/FastAPI/Go 등) | 5~8 s | 2 |
| 매니페스트 없음, 결정적 패턴 관찰 | 8~15 s | 2~3 |
| 완전 미지의 독점 프레임워크 | 15~20 s | 3~4 |
| 재실행 (캐시 히트) | 2~3 s | 2 |

타임아웃 기본 300 초 — `--timeout` 으로 조정.
