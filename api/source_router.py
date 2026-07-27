"""FastAPI 소스 서빙 라우터 — 노드 ``file_path`` 로 디스크 원본을 읽어 반환.

코드뷰(스키마 탭 '테이블 참조' → 소스 패널)가 **브라우저 업로드 없이** 동작하게 한다.
데스크톱(폴더 선택)·영속 그래프 모드에는 업로드 파일이 없으므로 분석 시 디스크에 남은
원본(`SETTINGS.analysis_data.dir/source/<project>/...`)을 서버가 직접 읽어 준다.

노드 ``file_path`` 는 형태가 섞여 있다(절대경로 / 프로젝트 상대 `/foo.c` / 파일명). 그래서
**소스 루트 기준으로 실제 파일을 해소**한다. 해소 결과는 항상 소스 루트 안이어야 한다(경로 이탈 차단).

    GET /robo/source?file_path=<경로>&start_line=<int=1>&end_line=<int=0>
        end_line ≤ 0 → 파일 끝까지(기본: 전체). 코드뷰는 전체 + 줄 하이라이트.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache
from api.source_resolver import AmbiguousSourceFileError, resolve_source_file

router = APIRouter(prefix="/robo")


@router.get("/source")
async def get_source(
    file_path: str = Query(..., description="노드 file_path (절대/프로젝트상대/파일명)"),
    start_line: int = Query(1, ge=1),
    end_line: int = Query(0, ge=0),
) -> dict:
    """노드 ``file_path`` 의 본문을 반환. 소스 루트 아래서 못 찾으면 404."""
    if not file_path.strip():
        raise HTTPException(status_code=400, detail="file_path required")

    source_root = (SETTINGS.analysis_data.dir / "source").resolve()
    try:
        resolved = resolve_source_file(file_path, source_root)
    except AmbiguousSourceFileError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if resolved is None:
        raise HTTPException(status_code=404, detail="source file not found")

    cache = SourceFileCache()
    lines = cache.lines(str(resolved))
    if start_line > max(1, len(lines)):
        raise HTTPException(status_code=416, detail="start_line exceeds source length")
    end = min(end_line, len(lines)) if end_line > 0 else len(lines)
    excerpt = cache.excerpt(str(resolved), start_line, end)
    return {
        "file_path": str(resolved),
        "content": excerpt.numbered_text,
        "line_count": len(lines),
        "start_line": start_line,
        "end_line": end,
        "coordinate_system": excerpt.coordinate_system,
        "source_status": excerpt.status,
        "found": True,
    }
