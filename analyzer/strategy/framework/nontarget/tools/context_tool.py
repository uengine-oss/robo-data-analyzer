"""컨텍스트 유틸리티

토큰 계산 유틸리티만 제공합니다.
Agent 전용 도구들은 삭제되었습니다.
"""

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


def count_tokens_tiktoken(text: str, model: str = "gpt-4") -> int:
    """tiktoken으로 정확한 토큰 계산
    
    Args:
        text: 토큰을 계산할 텍스트
        model: 사용할 모델 (기본: gpt-4)
        
    Returns:
        토큰 수
    """
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except ImportError:
        log_nontarget("TOOL", "tiktoken 미설치 - 대략적 계산 사용")
        return len(text) // 4
    except Exception as e:
        log_nontarget("TOOL", f"토큰 계산 오류: {e}")
        return len(text) // 4
