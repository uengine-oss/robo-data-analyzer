"""Framework(Java/Kotlin) 분석 모듈

주요 클래스:
- FrameworkPipeline: 총괄 파이프라인
- FrameworkAnalyzer: FrameworkPipeline 별칭
- FrameworkAstProcessor: AST 처리 및 LLM 분석
"""

from .framework_pipeline import FrameworkPipeline, FrameworkAnalyzer
from ._ast_core import FrameworkAstProcessor

__all__ = ["FrameworkPipeline", "FrameworkAnalyzer", "FrameworkAstProcessor"]
