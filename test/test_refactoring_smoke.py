"""리팩토링 스모크 테스트 — 모든 모듈 import + 핵심 클래스 인스턴스화 검증."""

import importlib
import pkgutil


def test_all_modules_import():
    """모든 Python 모듈이 import 에러 없이 로드되는지 검증."""
    errors = []
    for pkg_path in ['analyzer', 'util', 'config', 'service', 'api']:
        pkg = importlib.import_module(pkg_path)
        for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + '.'):
            try:
                importlib.import_module(modname)
            except Exception as e:
                errors.append(f'{modname}: {e}')
    assert not errors, f"Import errors:\n" + "\n".join(errors)


def test_factory_creates_pipelines():
    """AnalyzerFactory가 Framework/DBMS 파이프라인을 정상 생성하는지 검증."""
    from analyzer.strategy.analyzer_factory import AnalyzerFactory
    fw = AnalyzerFactory.create('framework')
    dbms = AnalyzerFactory.create('dbms')
    assert type(fw).__name__ == 'FrameworkPipeline'
    assert type(dbms).__name__ == 'DbmsPipeline'


def test_phase_context_creation():
    """PhaseContext dataclass가 정상 생성되는지 검증."""
    from analyzer.strategy.framework.pipeline.state import PhaseContext, FrameworkPipelineState
    state = FrameworkPipelineState()
    assert hasattr(state, 'clear_phase_data')
    assert hasattr(state, 'analysis_by_function')


def test_async_executor():
    """AsyncBatchExecutor + LLMRateLimiter 생성 검증."""
    from util.async_executor import AsyncBatchExecutor, LLMRateLimiter, get_executor, get_rate_limiter
    limiter = get_rate_limiter()
    executor = get_executor()
    assert limiter._spacing == 0.05
    assert executor._limiter is not None


def test_prompt_registry():
    """PromptRegistry에 모든 메서드가 등록되어 있는지 검증."""
    from util.prompt_registry import prompts
    expected_methods = [
        'call_extract', 'code_analysis', 'table_extract', 'module_analysis',
        'variable_summary', 'actor_synonym_merge', 'table_summary',
        'dbms_node_analysis', 'dbms_dml_extract', 'dbms_unit_analysis',
        'deep_agent_system_prompt', 'deep_agent_entity_prompt', 'deep_agent_varref_prompt',
    ]
    for method in expected_methods:
        assert hasattr(prompts, method), f"PromptRegistry missing: {method}"


def test_deterministic_module():
    """deterministic.py 통합 모듈에서 모든 함수가 import 가능한지 검증."""
    from analyzer.strategy.framework.linking.deterministic import (
        link_chunks_to_indexes,
        resolve_candidates,
        identify_entity_classes,
        has_external_entity_info,
        TypeMap3Layer,
        resolve_obj_to_type,
        build_type_map_3layer,
        fact_to_module_map,
    )


def test_no_old_log_prefix():
    """코드에 [FW: 접두사가 남아있지 않은지 검증."""
    import os
    from pathlib import Path
    base = Path('analyzer')
    for py_file in base.rglob('*.py'):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        assert '[FW:' not in content, f"Old log prefix [FW: found in {py_file}"


def test_no_logger_warning():
    """코드에 logger.warning이 남아있지 않은지 검증."""
    import os
    from pathlib import Path
    for base_dir in ['analyzer', 'util', 'service', 'api', 'config']:
        base = Path(base_dir)
        if not base.exists():
            continue
        for py_file in base.rglob('*.py'):
            if '__pycache__' in str(py_file):
                continue
            content = py_file.read_text(encoding='utf-8', errors='ignore')
            assert 'logger.warning' not in content, f"logger.warning found in {py_file}"


def test_key_in_code_word_boundary():
    """key_in_code가 단어 경계를 올바르게 처리하는지 검증."""
    from analyzer.strategy.framework.common.utils import key_in_code
    # 부분 매칭 방지
    assert key_in_code('userName = "test"', 'name') == False
    assert key_in_code('filename = 1', 'name') == False
    # 정확한 매칭
    assert key_in_code('name = "test"', 'name') == True
    assert key_in_code('"findUser"', 'findUser') == True
    assert key_in_code("'findUser'", 'findUser') == True
    assert key_in_code('mapper.findUser()', 'findUser') == True


def test_dependency_direction():
    """의존성 방향이 pipeline→linking→common→util 단방향인지 검증."""
    import ast
    from pathlib import Path
    base = Path('analyzer/strategy/framework')
    violations = []
    for py_file in base.rglob('*.py'):
        if '__pycache__' in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding='utf-8'))
        except:
            continue
        rel = str(py_file.relative_to(base)).replace('\\', '/')
        layer = 'unknown'
        if rel.startswith('pipeline/'): layer = 'pipeline'
        elif rel.startswith('linking/'): layer = 'linking'
        elif rel.startswith('common/'): layer = 'common'
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if layer == 'linking' and 'pipeline.' in node.module:
                    violations.append(f'{rel} imports {node.module}')
                if layer == 'common' and '.linking.' in node.module:
                    violations.append(f'{rel} imports {node.module}')
    assert not violations, f"Dependency violations:\n" + "\n".join(violations)
