"""파이프라인 제어 서비스

분석 파이프라인의 상태 조회 및 제어 기능을 제공합니다.

주요 기능:
- 파이프라인 상태 조회
- 단계 정보 조회
- 파이프라인 제어 (pause/resume/stop)
"""

from fastapi import HTTPException

from analyzer.pipeline_control import pipeline_controller


def get_pipeline_status() -> dict:
    """파이프라인 상태 조회
    
    Returns:
        {"currentPhase": int, "phaseName": str, "isPaused": bool, 
         "isStopped": bool, "phaseProgress": int, "phases": [...]}
    """
    return pipeline_controller.get_status()


def get_pipeline_phases_info() -> dict:
    """파이프라인 단계 정보 조회
    
    Returns:
        {"phases": [...], "currentPhase": int, "isPaused": bool}
    """
    return pipeline_controller.get_phases_info()


async def control_pipeline_action(action: str) -> dict:
    """파이프라인 제어 (pause/resume/stop)
    
    Args:
        action: 액션 문자열 ("pause", "resume", "stop")
        
    Returns:
        {"message": str}
        
    Raises:
        HTTPException: 잘못된 액션인 경우
    """
    action_lower = action.lower()
    
    if action_lower == "pause":
        pipeline_controller.pause()
        return {"message": "분석 일시정지됨"}
    elif action_lower == "resume":
        pipeline_controller.resume()
        return {"message": "분석 재개됨"}
    elif action_lower == "stop":
        pipeline_controller.stop()
        return {"message": "분석 중단됨"}
    else:
        raise HTTPException(400, f"지원하지 않는 액션: {action}")

