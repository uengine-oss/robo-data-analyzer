"""API 요청/응답 Pydantic 모델"""

from pydantic import BaseModel


class PipelineControlRequest(BaseModel):
    """파이프라인 제어 요청"""
    action: str  # "pause" | "resume" | "stop"
