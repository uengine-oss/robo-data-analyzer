"""이 프로젝트에 올린 소스를 다시 읽어 준다 — 「코드」 화면의 파일 목록.

**파일 목록이 브라우저에만 있었다.** 업로드 응답(``{files, ddlFiles}``)을 화면이
``localStorage`` 에 넣어 두고, 다음에 열 때 거기서만 되살렸다. 그래서 같은 프로젝트를
다른 PC·다른 브라우저·시크릿 창에서 열면 분석 결과(코드 그래프)는 있는데 「코드」
화면은 업로드 첫 화면으로 비어 있었다(2026-11-18, golden-1117). 웹판은 여러 사람이
같은 프로젝트를 보므로 늘 이렇게 된다.

올린 파일은 파서가 ``<data>/projects/<graph>/{source,ddl}`` 에 그대로 둔다
(``antlr-code-parser`` 의 ``ParserWorkspace.uploadFiles``). 이 서비스도 같은 볼륨을
같은 규약으로 본다(`shared/config/workspace.py`). 그래서 여기서 읽어 **업로드 응답과
같은 모양**으로 돌려준다 — 화면은 받은 것을 업로드 직후와 똑같이 쓴다.

읽기만 한다. 경로는 요청이 고를 수 없다(작업 폴더는 ``X-Neo4j-Database`` 로만 정해지고,
그 값은 ``sanitize`` 를 거친다). 폴더 밖을 가리키는 링크는 따라가지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from shared.config.workspace import data_dir

# 화면이 이 내용을 통째로 localStorage 에 넣는다(업로드 때와 같다). 지나치게 큰 소스가
# 응답을 키우지 않게 상한을 둔다 — 넘는 파일은 이름만 주고 내용은 비운다.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_FILES = 5000

# 파서 ``SourceTextCodec`` 과 같은 순서. 전부 실패하면 파서처럼 "[binary file]".
_CODECS = ("utf-8", "euc-kr", "cp949")
BINARY_MARK = "[binary file]"
TOO_LARGE_MARK = "[file too large to show]"


def decode(data: bytes) -> str:
    for codec in _CODECS:
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return BINARY_MARK


def _walk(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    root = base.resolve()
    found: list[Path] = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        # 폴더 밖으로 나가는 것은 건너뛴다(링크된 상위 폴더 등).
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        found.append(path)
    return found


def read_uploaded_sources() -> dict:
    """``{files, ddlFiles, truncated}`` — 업로드 응답과 같은 칸(+ 잘렸는지)."""
    corpus = data_dir()
    budget = MAX_TOTAL_BYTES
    count = 0
    truncated = False
    out: dict[str, list[dict[str, str]]] = {"files": [], "ddlFiles": []}

    for key, folder in (("files", corpus / "source"), ("ddlFiles", corpus / "ddl")):
        base = folder
        for path in _walk(base):
            if count >= MAX_FILES:
                truncated = True
                break
            rel = path.relative_to(base).as_posix()
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or size > budget:
                content = TOO_LARGE_MARK
                truncated = True
            else:
                content = decode(path.read_bytes())
                budget -= size
            out[key].append({"fileName": rel, "fileContent": content})
            count += 1

    return {**out, "truncated": truncated}
