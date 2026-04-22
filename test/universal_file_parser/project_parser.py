"""프로젝트 단위 파싱 — 100개 파일도 병렬 처리.

흐름:
  1. 스캔: node_modules/.git/build 등 제외, 10MB 이상 제외, 타겟 언어 제외
  2. 분류: 라이브러리 경로 / DeepAgent 경로
  3. 실행:
     - 라이브러리 경로: ThreadPool 16 workers (CPU-bound)
     - DeepAgent 경로: asyncio + semaphore=3 (LLM rate limit)
  4. 집계: 성공/실패/토큰/시간 요약 반환
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .detect import detect
from .library_parse import is_library_supported
from .parse import parse

logger = logging.getLogger(__name__)


SKIP_DIRS = {
    'node_modules', 'venv', '.venv', 'dist', 'build',
    'target', 'out', '.git', '.svn', '.hg',
    '.idea', '.vscode', '__pycache__',
    '.pytest_cache', '.mypy_cache',
}

SKIP_EXTS = {
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.bmp', '.webp',
    '.zip', '.tar', '.gz', '.rar', '.7z',
    '.mp3', '.mp4', '.wav', '.avi', '.mov',
    '.exe', '.dll', '.so', '.dylib', '.class', '.pyc', '.o',
    '.log', '.cache', '.tmp', '.lock',
}

LARGE_FILE_LIMIT = 10 * 1024 * 1024  # 10MB


def analyze_project(
    project_path: str,
    target_extensions: set[str] | None = None,
    max_workers_library: int = 16,
    max_concurrent_llm: int = 3,
) -> dict:
    """프로젝트 폴더 전체 분석.

    Args:
        project_path: 분석 루트
        target_extensions: 타겟 언어 (분석 제외, 예: {'.c', '.h', '.java'})
        max_workers_library: 라이브러리 경로 병렬 워커
        max_concurrent_llm: DeepAgent 경로 동시 LLM 호출 수

    Returns:
        {
            'total_files': int,
            'scanned_files': int,
            'results': [parse() 결과 dict, ...],
            'summary': {
                'library_path': int,
                'deepagent_path': int,
                'raw_fallback': int,
                'total_llm_calls': int,
                'total_tokens': int,
                'elapsed_sec': float,
            }
        }
    """
    start = time.time()
    target_extensions = target_extensions or set()

    # 1. 스캔
    files, total_files = _scan_files(project_path, target_extensions)

    # 2. 분류
    classified = _classify(files)
    logger.info(
        "project_parser: library=%d, deepagent=%d, total=%d",
        len(classified['library']), len(classified['deepagent']), len(files)
    )

    results: list[dict] = []

    # 3-A. 라이브러리 경로 (ThreadPool)
    if classified['library']:
        with ThreadPoolExecutor(max_workers=max_workers_library) as pool:
            results.extend(pool.map(_safe_parse, classified['library']))

    # 3-B. DeepAgent 경로 (asyncio + semaphore)
    if classified['deepagent']:
        deepagent_results = asyncio.run(_process_deepagent_async(
            classified['deepagent'], max_concurrent=max_concurrent_llm
        ))
        results.extend(deepagent_results)

    summary = _summarize(results, elapsed=time.time() - start)

    return {
        'total_files': total_files,
        'scanned_files': len(files),
        'results': results,
        'summary': summary,
    }


# =============================================================================
# 스캔
# =============================================================================

def _scan_files(project_path: str, target_extensions: set[str]) -> tuple[list[str], int]:
    """프로젝트 폴더를 순회해 분석 대상 파일 리스트 반환."""
    files: list[str] = []
    total = 0

    for root, dirs, filenames in os.walk(project_path):
        # SKIP_DIRS 필터링 (in-place 수정으로 하위 재귀 차단)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for name in filenames:
            total += 1
            path = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()

            if ext in target_extensions or ext in SKIP_EXTS:
                continue

            try:
                size = os.path.getsize(path)
                if size > LARGE_FILE_LIMIT:
                    continue

                with open(path, 'rb') as f:
                    head = f.read(8192)

                # 문서 매직바이트는 허용
                if _is_document_binary(head):
                    files.append(path)
                    continue
                # 일반 바이너리 차단
                if b'\x00' in head:
                    continue

                files.append(path)
            except (PermissionError, OSError):
                continue

    return files, total


def _is_document_binary(head: bytes) -> bool:
    """PDF/PPTX/XLSX/DOCX 매직바이트 여부."""
    if head.startswith(b'%PDF'):
        return True
    if head.startswith(b'PK'):   # PPTX/XLSX/DOCX 전부 zip 기반
        return True
    return False


# =============================================================================
# 분류
# =============================================================================

def _classify(files: list[str]) -> dict[str, list[str]]:
    """파일을 라이브러리 경로 / DeepAgent 경로로 분류."""
    classified = {'library': [], 'deepagent': []}

    for path in files:
        try:
            with open(path, 'rb') as f:
                data = f.read(8192)
            fmt, _ = detect(path, data)
        except Exception:
            continue

        if fmt == 'binary':
            continue  # 차단

        if is_library_supported(fmt):
            classified['library'].append(path)
        else:
            classified['deepagent'].append(path)

    return classified


# =============================================================================
# 실행
# =============================================================================

def _safe_parse(path: str) -> dict:
    """parse() 예외 안전 래퍼 — ThreadPool 에서 하나 실패해도 다른 파일 계속.
    결과 dict 에 '_path' 필드를 붙여서 집계 시 사용.
    """
    try:
        r = parse(path)
        r['_path'] = path
        return r
    except Exception as e:
        logger.warning("parse(%s) 실패: %s", path, e)
        return {
            'result': {'_error': str(e)},
            'flat': {},
            'elapsed_sec': 0,
            'tokens': 0,
            'strategy': 'error',
            'format': 'error',
            'validation': {'ok': False, 'reason': str(e)},
            '_path': path,
        }


async def _process_deepagent_async(paths: list[str], max_concurrent: int = 3) -> list[dict]:
    """DeepAgent 경로를 asyncio + semaphore 로 동시 실행 제한."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def one(path: str):
        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _safe_parse, path)

    return await asyncio.gather(*[one(p) for p in paths])


# =============================================================================
# 집계
# =============================================================================

def _summarize(results: list[dict], elapsed: float) -> dict:
    from collections import Counter

    strategies = Counter(r.get('strategy', 'unknown') for r in results)

    library_count = sum(
        n for s, n in strategies.items() if s.startswith('library_')
    )
    deepagent_count = strategies.get('deepagent', 0)
    raw_fallback_count = strategies.get('raw_fallback', 0)

    total_tokens = sum(r.get('tokens', 0) for r in results)
    total_llm_calls = sum(1 for r in results if r.get('tokens', 0) > 0)

    return {
        'library_path': library_count,
        'deepagent_path': deepagent_count,
        'raw_fallback': raw_fallback_count,
        'total_llm_calls': total_llm_calls,
        'total_tokens': total_tokens,
        'elapsed_sec': round(elapsed, 2),
    }
