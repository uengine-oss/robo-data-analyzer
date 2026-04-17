"""Framework 분석 전략 (범용 — Java/Kotlin/C/Python 등).

- FrameworkPipeline: BaseStreamingAnalyzer 구현, Phase 오케스트레이션
"""

from .pipeline.framework_pipeline import FrameworkPipeline

__all__ = ["FrameworkPipeline"]
