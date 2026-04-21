"""파일 포맷 + 인코딩 감지

우선순위:
  1. 파일명 완전 매칭 (Dockerfile, Makefile, .env 등)
  2. 확장자 매칭
  3. 매직바이트 (앞 2KB)
  4. 내용 샘플 휴리스틱
  5. 그래도 모르면 'unknown'

인코딩 4단 fallback:
  1. BOM 확인 (UTF-8, UTF-16 LE/BE)
  2. UTF-8 strict
  3. charset-normalizer
  4. EUC-KR 감지 시 → CP949 승격 (한국어 윈도우)
"""

import os
from pathlib import Path


# =============================================================================
# 포맷 식별 테이블
# =============================================================================

EXACT_FILENAMES = {
    'Dockerfile': 'dockerfile',
    'dockerfile': 'dockerfile',
    'Makefile': 'makefile',
    'makefile': 'makefile',
    '.env': 'dotenv',
    '.env.local': 'dotenv',
    '.env.production': 'dotenv',
    '.gitignore': 'gitignore',
}

EXTENSION_MAP = {
    '.json':     'json',
    '.yaml':     'yaml',
    '.yml':      'yaml',
    '.xml':      'xml',
    '.html':     'xml',
    '.htm':      'xml',
    '.toml':     'toml',
    '.ini':      'ini',
    '.cfg':      'ini_like',      # cfg 는 ini 비슷하지만 미지로 보내서 3단계로
    '.csv':      'csv',
    '.tsv':      'csv',
    '.env':      'dotenv',
    '.properties': 'properties',
    '.dsl':      'unknown',
    '.dat':      'unknown',
    '.log':      'unknown',
    '.txt':      'unknown',
}

# 매직바이트 (파일 앞부분 패턴)
MAGIC_BYTES = [
    (b'<?xml',       'xml'),
    (b'<!DOCTYPE',   'xml'),
    (b'<html',       'xml'),
    (b'#!',          'shebang'),
]


# =============================================================================
# 인코딩 감지
# =============================================================================

def detect_encoding(data: bytes) -> str:
    """4단 fallback 으로 인코딩 판정."""
    # 1. BOM
    if data.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if data.startswith(b'\xff\xfe'):
        return 'utf-16-le'
    if data.startswith(b'\xfe\xff'):
        return 'utf-16-be'

    # 2. UTF-8 strict
    sample = data[:8192]
    try:
        sample.decode('utf-8', errors='strict')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # 3. charset-normalizer
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(sample).best()
        if result:
            enc = result.encoding.lower()
            # 4. EUC-KR → CP949 승격 (한국어 윈도우)
            if enc in ('euc-kr', 'euc_kr'):
                return 'cp949'
            return enc
    except ImportError:
        pass
    except Exception:
        pass

    # 최종 fallback
    return 'utf-8'


# =============================================================================
# 포맷 식별
# =============================================================================

def _heuristic_guess(text: str) -> str:
    """내용 샘플로 포맷 추측."""
    stripped = text.lstrip()
    if not stripped:
        return 'unknown'

    # JSON
    if stripped[0] in ('{', '['):
        return 'json'

    # XML
    if stripped.startswith('<?xml') or stripped.startswith('<'):
        return 'xml'

    # YAML document marker
    if stripped.startswith('---'):
        return 'yaml'

    # INI (section headers)
    lines = [l.strip() for l in text.split('\n') if l.strip() and not l.strip().startswith((';', '#'))]
    if lines and any(l.startswith('[') and l.endswith(']') for l in lines[:5]):
        # ini_like → 미지로 보내서 LLM 이 처리 (ini 구조 유사 but 세부 다를 수 있음)
        return 'ini_like'

    return 'unknown'


def detect(path: str, data: bytes) -> tuple[str, str]:
    """
    파일 경로와 raw bytes 를 받아 (포맷 이름, 인코딩) 반환.

    Returns:
        (format_name, encoding)
        format_name:
          'json' | 'yaml' | 'xml' | 'toml' | 'ini' | 'dotenv' | 'csv' |
          'dockerfile' | 'properties' | 'gitignore' | 'makefile' |
          'shebang' | 'ini_like' | 'binary' | 'unknown'
    """
    encoding = detect_encoding(data)

    # 바이너리 차단 (null byte 포함 시)
    if b'\x00' in data[:8192]:
        return ('binary', encoding)

    # 1. 파일명 완전 매칭
    filename = os.path.basename(path)
    if filename in EXACT_FILENAMES:
        return (EXACT_FILENAMES[filename], encoding)

    # 2. 확장자 매칭
    ext = Path(filename).suffix.lower()
    if ext in EXTENSION_MAP:
        fmt = EXTENSION_MAP[ext]
        # unknown 확장자는 매직바이트/휴리스틱으로 한 번 더 시도
        if fmt != 'unknown':
            return (fmt, encoding)

    # 3. 매직바이트
    head = data[:2048]
    for magic, fmt in MAGIC_BYTES:
        if head.startswith(magic):
            if fmt == 'shebang':
                # 쉘 스크립트류는 미지 포맷으로
                return ('unknown', encoding)
            return (fmt, encoding)

    # 4. 내용 샘플 휴리스틱
    try:
        text = head.decode(encoding, errors='replace')
    except (LookupError, UnicodeDecodeError):
        text = head.decode('utf-8', errors='replace')

    guessed = _heuristic_guess(text)
    if guessed != 'unknown':
        return (guessed, encoding)

    # 5. 최종 미지
    return ('unknown', encoding)
