"""소스 파일 텍스트 캐시 — 같은 파일을 여러 코드노드/range 가 읽을 때 1회만 read.

**run 인스턴스 단위** — 실행기가 한 run에 하나를 만들고 step2(code_text 부여)·step4
DBMS(DML 본문)·step5(컨텍스트 빌드)·step6(range 본문)가 공유한다. 모듈 전역 캐시는
쓰지 않아 장수명 API 서버의 run 사이 stale 데이터를 막는다.

read/decode 실패는 **조용히 넘기지 않는다** (헌법 IV / spec 131 C-023/C-027).
원문을 exact하게 읽지 못하면 source↔AST closure가 성립하지 않으므로 예외를 전파한다.
"""
from __future__ import annotations

from pathlib import Path

from shared.events.runtime_logger import log_warning
from shared.files.source_excerpt import SourceExcerpt


_LOG = "shared.files.cache"


class SourceFileCache:
    """파일 경로 → exact UTF-8 텍스트 라인 캐시. read/decode 실패는 fail-closed."""

    def __init__(self) -> None:
        self._bytes: dict[str, bytes] = {}
        self._read_errors: dict[str, SourceFileReadError] = {}
        self._lines: dict[str, list[str]] = {}

    def read_bytes(self, file_path: str | Path) -> bytes:
        """Return exact file bytes, cached for the run; read failures stay explicit.

        External-file decoders need the original bytes to distinguish UTF-8 from
        legacy Korean encodings.  Keeping that boundary here prevents step3 from
        introducing a second disk cache beside the one already shared by AST,
        linking and context construction.
        """
        key = str(file_path)
        cached = self._bytes.get(key)
        if cached is not None:
            return cached
        previous_error = self._read_errors.get(key)
        if previous_error is not None:
            raise previous_error
        try:
            raw = Path(file_path).read_bytes()
        except OSError as error:
            wrapped = SourceFileReadError(key, error)
            self._read_errors[key] = wrapped
            log_warning(
                _LOG,
                "source_read_failed",
                f"소스 파일 read 실패 — 원문을 사용할 수 없다: {error!r}",
                file_path=key,
            )
            raise wrapped from error
        self._bytes[key] = raw
        return raw

    def lines(self, file_path: str) -> list[str]:
        """파일 텍스트 라인 목록. invalid UTF-8 byte를 삭제하지 않고 예외로 드러낸다."""
        cached = self._lines.get(file_path)
        if cached is not None:
            return cached
        lines = self.read_bytes(file_path).decode("utf-8", errors="strict").splitlines()
        self._lines[file_path] = lines
        return lines

    def slice(self, file_path: str, start_line: int, end_line: int) -> str:
        """1-indexed ``start_line``~``end_line`` 본문 (``end_line`` ≤ 0 이면 파일 끝까지)."""
        lines = self.lines(file_path)
        if not lines:
            return ""
        end = end_line if end_line > 0 else len(lines)
        return "\n".join(lines[max(start_line - 1, 0):end])

    def excerpt(self, file_path: str, start_line: int, end_line: int) -> SourceExcerpt:
        """Return the single raw/numbered contract for an absolute source range."""
        lines = self.lines(file_path)
        return SourceExcerpt.from_lines(
            file_path,
            lines,
            start_line,
            end_line,
            read_failed=file_path in self._read_errors,
        )


class SourceFileReadError(OSError):
    """A cached, explicit read failure at the shared source boundary."""

    def __init__(self, file_path: str, cause: OSError) -> None:
        super().__init__(f"cannot read source file {file_path!r}: {cause}")
        self.file_path = file_path
        self.cause = cause
