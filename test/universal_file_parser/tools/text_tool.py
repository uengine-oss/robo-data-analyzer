"""텍스트 미지 포맷용 extract 도구 — read_sample."""

from langchain_core.tools import tool


READ_SAMPLE_DEFAULT = 4096   # 한 번에 4KB 샘플 (AI 가 필요하면 n_bytes 인자로 확장)


def make_read_sample(state):
    @tool
    def read_sample(n_bytes: int = READ_SAMPLE_DEFAULT, offset: int = 0) -> str:
        """파일 일부를 텍스트로 반환.

        Args:
            n_bytes: 읽을 바이트 수 (기본 4096). 전체 파일을 원하면 크게 지정.
            offset: 시작 위치 (기본 0). 큰 파일을 이어서 읽을 때 사용.

        큰 파일은 여러 번 호출해서 이어 읽는 것이 토큰 효율적입니다.
        """
        start = max(int(offset), 0)
        n = max(int(n_bytes), 128)
        end = start + n
        chunk = state.data[start:end]
        text = chunk.decode('utf-8', errors='replace')
        total = len(state.data)
        return (
            f"{text}\n"
            f"[offset={start}, read={len(chunk)} bytes, total={total} bytes"
            f"{' — 끝' if end >= total else f', 남음={total - end} bytes'}]"
        )
    return read_sample
