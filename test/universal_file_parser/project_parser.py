"""프로젝트 단위 파싱 — 확장자 그룹 단위로 DeepAgent 호출 수 최소화.

흐름:
  1. 스캔: node_modules/.git/build 등 제외, 10MB 이상 제외, 타겟 언어 제외
  2. 분류:
       - library 경로 (확장자 화이트리스트): 기존대로 파일 병렬
       - DeepAgent 경로: 확장자별 그룹
  3. 실행:
       - Library: ThreadPool 16 workers
       - DeepAgent group: 그룹 하나마다 대표 1개 + 형제 샘플 → 파서 코드 → 그룹 전체
                           같은 확장자 여러 파일도 LLM 호출 1회로 완료 (기본).
                           그룹 내 coverage 실패 파일만 개별 DeepAgent 로 폴백.
  4. 집계: 성공/실패/토큰/시간 요약 반환

단일 세션 한정 — 캐시/학습 저장 없음.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .detect import detect
from .flatten import flatten
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
GROUP_COVERAGE_THRESHOLD = 0.7       # 그룹 파서 실행 후 이 값 미만이면 개별 폴백
DEFAULT_SIBLING_K = 3                # 대표 + 이 수만큼의 형제를 DeepAgent 에게 제공


def analyze_project(
    project_path: str,
    target_extensions: set[str] | None = None,
    max_workers_library: int = 16,
    max_concurrent_groups: int = 3,
    sibling_k: int = DEFAULT_SIBLING_K,
) -> dict:
    """프로젝트 폴더 전체 분석.

    Args:
        project_path: 분석 루트
        target_extensions: 타겟 언어 (분석 제외, 예: {'.c', '.h', '.java'})
        max_workers_library: 라이브러리 경로 병렬 워커
        max_concurrent_groups: DeepAgent 그룹 동시 처리 수 (LLM rate limit)
        sibling_k: 대표 파일에 얹어서 에이전트에게 전달할 형제 수

    Returns:
        {
            'total_files': int,
            'scanned_files': int,
            'results': [parse() 결과 dict, ...],
            'summary': {
                'library_path': int,
                'deepagent_group_path': int,     # 대표 파서로 처리된 파일 수
                'deepagent_individual_path': int, # 그룹 파서 실패 → 개별 승격된 파일 수
                'raw_fallback': int,
                'total_llm_calls': int,          # 토큰 발생한 호출 수
                'total_tokens': int,
                'elapsed_sec': float,
                'group_stats': {ext: {files, llm_calls, tokens}},
            }
        }
    """
    start = time.time()
    target_extensions = target_extensions or set()

    # 1. 스캔
    files, total_files = _scan_files(project_path, target_extensions)

    # 2. 분류 (library 직행 / 확장자별 그룹)
    classified = _classify_grouped(files)
    logger.info(
        "project_parser: library=%d, groups=%d (%d files)",
        len(classified['library']),
        len(classified['deepagent_groups']),
        sum(len(v) for v in classified['deepagent_groups'].values()),
    )

    results: list[dict] = []
    group_stats: dict[str, dict] = {}

    # 3-A. 라이브러리 경로 (ThreadPool)
    if classified['library']:
        with ThreadPoolExecutor(max_workers=max_workers_library) as pool:
            results.extend(pool.map(_safe_parse, classified['library']))

    # 3-B. DeepAgent 그룹 (asyncio + semaphore)
    if classified['deepagent_groups']:
        group_results, group_stats = asyncio.run(_process_groups_async(
            classified['deepagent_groups'],
            max_concurrent=max_concurrent_groups,
            sibling_k=sibling_k,
        ))
        results.extend(group_results)

    summary = _summarize(results, elapsed=time.time() - start, group_stats=group_stats)

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

                if _is_document_binary(head):
                    files.append(path)
                    continue
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
    if head.startswith(b'PK'):
        return True
    return False


# =============================================================================
# 분류 (library 경로 / 확장자별 그룹)
# =============================================================================

def _classify_grouped(files: list[str]) -> dict:
    """
    Returns:
      {
        'library':  [path, ...],                 # 라이브러리 직행
        'deepagent_groups': {ext: [path, ...]},  # 확장자별 그룹
      }
    """
    classified: dict = {'library': [], 'deepagent_groups': {}}

    for path in files:
        try:
            with open(path, 'rb') as f:
                data = f.read(8192)
            fmt, _ = detect(path, data)
        except Exception:
            continue

        if fmt == 'binary':
            continue

        if is_library_supported(fmt):
            classified['library'].append(path)
        else:
            # 확장자로 그룹핑 (문서 바이너리는 확장자가 식별자)
            ext = Path(path).suffix.lower() or '__noext__'
            classified['deepagent_groups'].setdefault(ext, []).append(path)

    return classified


# =============================================================================
# 실행 — 라이브러리 (파일별)
# =============================================================================

def _safe_parse(path: str) -> dict:
    """parse() 예외 안전 래퍼. 결과 dict 에 '_path' 첨부."""
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


# =============================================================================
# 실행 — DeepAgent 그룹
# =============================================================================

async def _process_groups_async(
    groups: dict[str, list[str]],
    max_concurrent: int = 3,
    sibling_k: int = DEFAULT_SIBLING_K,
) -> tuple[list[dict], dict[str, dict]]:
    """확장자 그룹들을 동시 실행 (각 그룹은 내부적으로 순차)."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def one_group(ext: str, paths: list[str]):
        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, _process_group_sync, ext, paths, sibling_k
            )

    tasks = [one_group(ext, paths) for ext, paths in groups.items()]
    outputs = await asyncio.gather(*tasks)

    all_results: list[dict] = []
    all_stats: dict[str, dict] = {}
    for ext, paths, group_results, stats in outputs:
        all_results.extend(group_results)
        all_stats[ext] = stats

    return all_results, all_stats


def _process_group_sync(
    ext: str, paths: list[str], sibling_k: int,
) -> tuple[str, list[str], list[dict], dict]:
    """한 그룹(같은 확장자) 처리.

    전략:
      1. 파일 로드 (작은 것부터 정렬)
      2. 가장 작은 것 = 대표, 그 다음 sibling_k 개 = 형제
      3. generate_parser_with_llm(대표, siblings=형제들) → parser_code
      4. parser_code 로 그룹 전체 병렬 실행 (LLM 0회)
      5. coverage 검증 실패 파일만 _safe_parse (개별 DeepAgent 승격)

    Returns:
        (ext, paths, [file_result, ...], stats)
    """
    # 지연 import — 순환 참조 회피
    from .deepagent_parse import generate_parser_with_llm, execute_parser
    from .tools.common_tools import _clean_chunks, _collect_text_from, _coverage

    logger.info("▶ group '%s' start: %d files", ext, len(paths))
    group_start = time.time()

    # 1. 로드 (작은 것부터)
    loaded: list[tuple[str, bytes]] = []
    for p in paths:
        try:
            with open(p, 'rb') as f:
                loaded.append((p, f.read()))
        except Exception as e:
            logger.warning("  read failed %s: %s", p, e)

    if not loaded:
        return ext, paths, [], {'files': 0, 'llm_calls': 0, 'tokens': 0}

    loaded.sort(key=lambda x: len(x[1]))
    rep_path, rep_data = loaded[0]
    siblings = loaded[1:1 + sibling_k]

    fmt_hint, _ = detect(rep_path, rep_data[:8192])

    logger.info(
        "  representative=%s (%d B), siblings=%d, fmt=%s",
        rep_path, len(rep_data), len(siblings), fmt_hint,
    )

    # 2. 대표 + 형제로 파서 생성 (LLM 1회)
    parsed_rep, parser_code, tokens = generate_parser_with_llm(
        rep_path, rep_data, fmt_hint=fmt_hint,
        siblings=siblings,
    )
    llm_calls = 1 if tokens else 0

    # 대표도 실패 = 그룹 파서 불가 → 전부 개별 승격
    if parser_code is None:
        logger.warning("  group parser failed → 전부 개별 승격")
        results = [_safe_parse(p) for p, _ in loaded]
        # _safe_parse 내부 토큰도 집계
        extra_tokens = sum(r.get('tokens', 0) for r in results)
        extra_calls = sum(1 for r in results if r.get('tokens', 0) > 0)
        stats = {
            'files': len(loaded),
            'llm_calls': extra_calls,
            'tokens': extra_tokens,
            'mode': 'all_individual',
            'elapsed_sec': round(time.time() - group_start, 2),
        }
        return ext, paths, results, stats

    # 3. 파서 코드를 그룹 전체에 병렬 적용 (LLM 0회)
    logger.info("  group parser OK — apply to %d files (parallel)", len(loaded))
    results: list[dict] = []
    fallback_paths: list[tuple[int, str]] = []

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = []
        for i, (p, data) in enumerate(loaded):
            if i == 0:
                # 대표는 이미 파싱됨
                futures.append(('rep', i, p, parsed_rep, None))
            else:
                futures.append((
                    'exec', i, p,
                    pool.submit(_apply_parser, parser_code, data, execute_parser,
                                _clean_chunks, _collect_text_from, _coverage),
                    data,
                ))

        # 수집
        for kind, i, p, fut_or_result, data in futures:
            if kind == 'rep':
                result = fut_or_result
                ok = result is not None and (
                    not isinstance(result, list) or len(result) > 0
                )
                cov = 1.0 if ok else 0.0
                err = None
            else:
                try:
                    result, cov, err = fut_or_result.result()
                except Exception as e:
                    result, cov, err = None, 0.0, f"{type(e).__name__}: {e}"
                ok = err is None and result and (
                    not isinstance(result, list) or cov >= GROUP_COVERAGE_THRESHOLD
                )

            if ok:
                results.append(_pack_result(
                    path=p, result=result, fmt=fmt_hint,
                    strategy='deepagent_group_rep' if i == 0 else 'deepagent_group',
                    tokens=tokens if i == 0 else 0,
                    validation_ok=True,
                    validation_reason=f'coverage={cov:.2f}',
                ))
            else:
                fallback_paths.append((i, p))

    # 4. 그룹 파서 실패 파일만 개별 승격
    fallback_tokens = 0
    fallback_calls = 0
    if fallback_paths:
        logger.info("  %d files failed group parser → 개별 승격", len(fallback_paths))
        for _, p in fallback_paths:
            r = _safe_parse(p)
            results.append(r)
            fallback_tokens += r.get('tokens', 0)
            if r.get('tokens', 0) > 0:
                fallback_calls += 1

    # 원래 paths 순서 복원 (scan 순서 유지)
    path_to_result = {r['_path']: r for r in results}
    ordered = [path_to_result[p] for p in paths if p in path_to_result]

    stats = {
        'files': len(loaded),
        'llm_calls': llm_calls + fallback_calls,
        'tokens': tokens + fallback_tokens,
        'mode': 'group_parser',
        'fallback_files': len(fallback_paths),
        'elapsed_sec': round(time.time() - group_start, 2),
    }
    logger.info(
        "▶ group '%s' done: files=%d, llm=%d, tokens=%d, fallback=%d, %.2fs",
        ext, stats['files'], stats['llm_calls'], stats['tokens'],
        stats.get('fallback_files', 0), stats['elapsed_sec'],
    )
    return ext, paths, ordered, stats


def _apply_parser(parser_code, data, execute_parser,
                  clean_chunks_fn, collect_text_fn, coverage_fn):
    """단일 파일에 그룹 파서 코드 실행 + coverage 계산."""
    result = execute_parser(parser_code, data)
    if isinstance(result, list):
        result = clean_chunks_fn(result)
        orig = collect_text_fn(data)
        cov = coverage_fn(result, orig) if orig else 1.0
    else:
        cov = 1.0 if result else 0.0
    return result, cov, None


def _pack_result(path, result, fmt, strategy, tokens,
                 validation_ok, validation_reason) -> dict:
    return {
        'result': result,
        'flat': flatten(result) if result else {},
        'elapsed_sec': 0,         # 그룹 모드에선 파일별 측정 안 함
        'tokens': tokens,
        'strategy': strategy,
        'format': fmt,
        'validation': {'ok': validation_ok, 'reason': validation_reason},
        '_path': path,
    }


# =============================================================================
# 집계
# =============================================================================

def _summarize(results: list[dict], elapsed: float,
               group_stats: dict[str, dict] | None = None) -> dict:
    from collections import Counter

    strategies = Counter(r.get('strategy', 'unknown') for r in results)

    library_count = sum(
        n for s, n in strategies.items() if s.startswith('library_')
    )
    group_rep = strategies.get('deepagent_group_rep', 0)
    group_followers = strategies.get('deepagent_group', 0)
    deepagent_individual = strategies.get('deepagent', 0)
    raw_fallback_count = strategies.get('raw_fallback', 0)

    total_tokens = sum(r.get('tokens', 0) for r in results)
    total_llm_calls = sum(1 for r in results if r.get('tokens', 0) > 0)

    return {
        'library_path': library_count,
        'deepagent_group_path': group_rep + group_followers,
        'deepagent_individual_path': deepagent_individual,
        'raw_fallback': raw_fallback_count,
        'total_llm_calls': total_llm_calls,
        'total_tokens': total_tokens,
        'elapsed_sec': round(elapsed, 2),
        'group_stats': group_stats or {},
    }
