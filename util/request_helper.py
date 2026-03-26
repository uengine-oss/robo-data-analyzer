"""API 요청 헬퍼 함수

API 요청에서 헤더 정보를 추출하는 유틸리티 함수들입니다.
"""

from fastapi import HTTPException, Request



def extract_api_key(request: Request, missing_status: int = 401) -> str:
    """API 키 추출 (헤더에서만 추출, 환경변수 폴백 없음)
    
    주의: 이 함수는 환경변수로 폴백하지 않습니다.
    헤더에 API 키가 없으면 HTTPException을 발생시킵니다.
    
    Args:
        request: FastAPI Request 객체
        missing_status: API 키가 없을 때 반환할 HTTP 상태 코드
        
    Returns:
        API 키 문자열
        
    Raises:
        HTTPException: API 키가 없을 때
    """
    # 헤더에서 API 키 추출
    api_key = (
        request.headers.get("OpenAI-Api-Key") or
        request.headers.get("Anthropic-Api-Key")
    )
    
    if not api_key:
        raise HTTPException(
            missing_status, 
            "요청 헤더 누락: OpenAI-Api-Key 또는 Anthropic-Api-Key 헤더가 필요합니다."
        )
    return api_key


def extract_locale(request: Request) -> str:
    """Accept-Language 헤더에서 로케일 추출
    
    Args:
        request: FastAPI Request 객체
        
    Returns:
        로케일 문자열 (기본값: "ko")
    """
    return request.headers.get("Accept-Language", "ko")

