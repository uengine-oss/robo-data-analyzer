"""LLM 생성 파서 코드의 AST 기반 안전성 검증

샌드박스 대체 (Windows 호환). import/함수호출/속성접근을 화이트리스트 vs 블랙리스트로 검사.
"""

import ast


ALLOWED_IMPORTS = {
    're', 'json', 'io', 'struct', 'csv', 'base64',
    'collections', 'dataclasses', 'datetime',
    'itertools', 'functools', 'string', 'math',
    'hashlib', 'uuid', 'binascii',
}

FORBIDDEN_CALLS = {
    'eval', 'exec', 'compile', '__import__',
    'open', 'input',
    'globals', 'locals', 'vars', 'dir',
    'setattr', 'getattr', 'delattr',
}

FORBIDDEN_ATTRS = {
    'os', 'sys', 'subprocess', 'socket', 'urllib', 'requests',
    'shutil', 'pathlib', 'tempfile', 'http', 'ftplib', 'smtplib',
    'importlib', 'pickle', 'marshal', 'ctypes', 'multiprocessing',
    'threading', 'asyncio',
}


def validate_parser_code(code: str) -> tuple[bool, str]:
    """AST 검증. 위험 요소 발견 시 (False, reason) 반환.

    Returns:
        (ok: bool, reason: str)
        reason 예시:
          - 'syntax_error: ...'
          - 'forbidden_import: os'
          - 'forbidden_call: eval'
          - 'forbidden_attr: os.remove'
          - 'missing_parse_function'
          - 'wrong_parse_signature'
          - 'ok'
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"syntax_error: {e}"

    for node in ast.walk(tree):
        # import 화이트리스트
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top not in ALLOWED_IMPORTS:
                    return False, f"forbidden_import: {alias.name}"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split('.')[0]
                if top not in ALLOWED_IMPORTS:
                    return False, f"forbidden_import: {node.module}"

        # 위험 함수 호출
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                return False, f"forbidden_call: {func.id}"
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in FORBIDDEN_ATTRS:
                    return False, f"forbidden_attr: {func.value.id}.{func.attr}"

        # dunder 호출 (__import__ 등 우회 시도)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('__') and node.attr.endswith('__'):
                # 일부 안전한 dunder 는 허용 (__name__, __class__ 등)
                if node.attr not in ('__name__', '__class__', '__dict__', '__init__'):
                    return False, f"forbidden_dunder: {node.attr}"

    # parse 함수 존재 확인
    parse_func = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == 'parse':
            parse_func = n
            break

    if parse_func is None:
        return False, "missing_parse_function"

    # parse 시그니처 체크
    if len(parse_func.args.args) < 1:
        return False, "wrong_parse_signature: parse() needs at least 1 argument"

    return True, "ok"
