"""텍스트 미지 포맷용 extract 도구 — read_sample."""

from langchain_core.tools import tool


READ_SAMPLE_MAX_BYTES = 4096
READ_SAMPLE_ABS_MAX = 8192


def make_read_sample(state):
    @tool
    def read_sample(n_bytes: int = READ_SAMPLE_MAX_BYTES) -> str:
        """파일 앞부분을 텍스트로 디코드해 반환. 구조 파악용.

        Args:
            n_bytes: 읽을 바이트 수 (기본 4096, 최대 8192)
        """
        n = min(max(n_bytes, 128), READ_SAMPLE_ABS_MAX)
        sample = state.data[:n]
        text = sample.decode('utf-8', errors='replace')
        return text + f"\n[파일 전체 크기: {len(state.data)} bytes, 표시: {n} bytes]"
    return read_sample
