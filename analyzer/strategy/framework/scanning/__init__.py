"""Phase 1 Step 0 — 코드베이스 프로파일 서브스텝.

파이프라인 본 스캔 직전에 프로젝트를 한 번 훑어
프레임워크·DB 접근 패턴 요약 + 리소스 파일 5 카테고리 분류를 생성한다.
결과는 `pipeline_state.codebase_profile` 에 저장되어 후속 단계에서 hint 로 쓰인다.
"""

from analyzer.strategy.framework.scanning.profile_models import CodebaseInfo, CodebaseProfile
from analyzer.strategy.framework.scanning.codebase_profiler import profile

__all__ = ["profile", "CodebaseProfile", "CodebaseInfo"]
