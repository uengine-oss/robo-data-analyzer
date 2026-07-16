"""FastAPI 소스 서빙 라우터 — 노드 ``file_path`` 로 디스크 원본을 읽어 반환.

코드뷰(스키마 탭 '테이블 참조' → 소스 패널)가 **브라우저 업로드 없이** 동작하게 한다.
데스크톱(폴더 선택)·영속 그래프 모드에는 업로드 파일이 없으므로 분석 시 디스크에 남은
원본(`ENV.data.dir/source/<project>/...`)을 서버가 직접 읽어 준다.

노드 ``file_path`` 는 형태가 섞여 있다(절대경로 / 프로젝트 상대 `/foo.c` / 파일명). 그래서
**소스 루트 기준으로 실제 파일을 해소**한다. 해소 결과는 항상 소스 루트 안이어야 한다(경로 이탈 차단).

    GET /robo/source?file_path=<경로>&start_line=<int=1>&end_line=<int=0>
        end_line ≤ 0 → 파일 끝까지(기본: 전체). 코드뷰는 전체 + 줄 하이라이트.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from system.env_catalog import ENV
from pipeline.source_file_cache import SourceFileCache

router = APIRouter(prefix="/robo")


def _under(path: Path, root: Path) -> bool:
    """``path`` 가 ``root`` 와 같거나 그 하위인가(대소문자 무시 — Windows)."""
    p = os.path.normcase(str(path))
    r = os.path.normcase(str(root))
    return p == r or p.startswith(r + os.sep)


def _resolve_source_file(file_path: str, source_root: Path) -> Optional[Path]:
    """노드 file_path(절대/프로젝트상대/파일명)를 소스 루트 아래 실제 파일로 해소. 못 찾으면 None."""
    raw = file_path.strip().replace("\\", "/")
    if not raw:
        return None

    # 1) 절대경로가 소스 루트 안 실파일이면 그대로
    direct = Path(raw)
    if direct.is_absolute():
        rp = direct.resolve()
        if rp.is_file() and _under(rp, source_root):
            return rp

    # 2) 소스 루트에 그대로 이어붙이기(소스루트 상대)
    joined = (source_root / raw.lstrip("/")).resolve()
    if joined.is_file() and _under(joined, source_root):
        return joined

    # 3) 파일명으로 소스 루트 하위 탐색 → 주어진 꼬리경로 일치 우선(프로젝트상대/파일명)
    name = Path(raw).name
    matches = [p for p in source_root.rglob(name) if p.is_file()]
    if not matches:
        return None
    tail = "/" + raw.lstrip("/").lower()
    suffix = [p for p in matches if str(p).replace("\\", "/").lower().endswith(tail)]
    pick = (suffix or sorted(matches, key=lambda p: len(str(p))))[0].resolve()
    return pick if _under(pick, source_root) else None


@router.get("/source")
async def get_source(
    file_path: str = Query(..., description="노드 file_path (절대/프로젝트상대/파일명)"),
    start_line: int = Query(1, ge=1),
    end_line: int = Query(0, ge=0),
) -> dict:
    """노드 ``file_path`` 의 본문을 반환. 소스 루트 아래서 못 찾으면 404."""
    if not file_path.strip():
        raise HTTPException(status_code=400, detail="file_path required")

    source_root = (ENV.data.dir / "source").resolve()
    resolved = _resolve_source_file(file_path, source_root)
    if resolved is None:
        raise HTTPException(status_code=404, detail="source file not found")

    cache = SourceFileCache()
    lines = cache.lines(str(resolved))
    end = end_line if end_line > 0 else len(lines)
    return {
        "file_path": str(resolved),
        "content": cache.slice(str(resolved), start_line, end_line),
        "line_count": len(lines),
        "start_line": start_line,
        "end_line": end,
        "found": True,
    }
