"""Build the reproducible v10 move map from the frozen Git tree."""
from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from pathlib import Path


BASELINE = "4a1bb126b3fe1c26c0ce54363bb4caab49863486"
OUT = Path(__file__).with_name("move-map.tsv")


def _target(path: str) -> str:
    if path == "analysis_pipelines.py":
        return "pipeline/analysis_pipelines.py"
    if path.startswith("analysis_flow/"):
        rest = path.removeprefix("analysis_flow/")
        if rest == "__init__.py":
            return "pipeline/steps/__init__.py"
        if Path(rest).name.startswith("test_"):
            return f"tests/pipeline/{rest}"
        return f"pipeline/steps/{rest}"
    if path.startswith("agent_tools/"):
        name = path.removeprefix("agent_tools/")
        if name == "filesystem.py":
            name = "filesystem_tools.py"
        return f"llm/agents/{name}"
    if path.startswith("api/"):
        name = path.removeprefix("api/")
        return f"tests/api/{name}" if name.startswith("test_") else path
    if path.startswith("cli_scripts/"):
        name = path.removeprefix("cli_scripts/")
        if name == "prompt_hashes.json":
            return "llm/prompts/prompt_hashes.json"
        if name in {"check_byte_identical.py", "refresh_pricing.py"}:
            return f"scripts/llm_maintenance/{name}"
        if name in {"reembed_graph.py", "reset_neo4j_schema.py", "resynthesize_glossary.py"}:
            return f"scripts/graph_maintenance/{name}"
        if name == "README.md":
            return "scripts/README.md"
        return f"scripts/analysis/{name}"
    if path.startswith("data_models/"):
        name = path.removeprefix("data_models/")
        if name in {"graph_nodes.py", "graph_edges.py"}:
            return f"graph/schema/{name}"
        if name == "agent_output.py":
            return "llm/agents/agent_output.py"
        if name in {"analysis_run.py", "codebase.py"}:
            return "pipeline/data_models/analysis_meta.py"
        return f"pipeline/data_models/{name}"
    if path.startswith("llm_configs/"):
        return f"llm/configs/{path.removeprefix('llm_configs/')}"
    if path.startswith("prompts/agents/"):
        return f"llm/agents/{path.removeprefix('prompts/agents/')}"
    if path.startswith("prompts/"):
        return f"llm/prompts/{path.removeprefix('prompts/')}"
    if path.startswith("skills/"):
        return f"llm/skills/{path.removeprefix('skills/')}"
    if path.startswith("search/"):
        name = path.removeprefix("search/")
        renames = {
            "config.py": "search/search_config.py",
            "neo.py": "search/corpus/neo4j_reader.py",
            "rewrite.py": "search/query/query_rewrite.py",
            "judge.py": "search/query/relevance_judge.py",
            "llm.py": "search/query/query_llm.py",
            "glossary.py": "search/query/glossary.py",
        }
        if name.startswith("test_"):
            return f"tests/search/{name}"
        if name.startswith("eval/"):
            return f"tests/eval/{name.removeprefix('eval/')}"
        if name in renames:
            return renames[name]
        if name in {"retriever.py", "recall.py", "rerank.py", "fusion.py"}:
            return f"search/ranking/{name}"
        if name in {"corpus.py", "embed_material.py"}:
            return f"search/corpus/{name}"
        return path
    if path.startswith("shared/config/"):
        return f"system/{path.removeprefix('shared/config/')}"
    direct = {
        "shared/content_text.py": "llm/client/message_content.py",
        "shared/domain_terms.py": "pipeline/domain_terms.py",
        "shared/node_registry.py": "pipeline/steps/step2_load_ast/node_identity_registry.py",
        "shared/source_file_cache.py": "pipeline/source_file_cache.py",
        "shared/test_node_registry.py": "tests/pipeline/step2_load_ast/test_node_identity_registry.py",
        "shared/__init__.py": "(remove-empty-package)",
    }
    if path in direct:
        return direct[path]
    if path.startswith("shared/test_domain_"):
        return f"tests/pipeline/{path.removeprefix('shared/')}"
    if path.startswith("shared/ddl/"):
        name = path.removeprefix("shared/ddl/")
        if name == "parser.py":
            name = "ddl_parser.py"
        return f"pipeline/steps/step1_parse_ddl/{name}"
    if path.startswith("shared/example_mapping/"):
        name = path.removeprefix("shared/example_mapping/")
        if name == "builder.py":
            return "pipeline/steps/step6_analyze_code/example_mapping_builder.py"
        if name == "graph_writer.py":
            return "graph/nodes/example_writer.py"
        return f"pipeline/steps/step6_analyze_code/{name}"
    if path.startswith("shared/external/"):
        name = path.removeprefix("shared/external/")
        return "pipeline/catalog_client.py" if name == "catalog_client.py" else f"pipeline/{name}"
    if path.startswith("shared/lang/"):
        name = path.removeprefix("shared/lang/")
        if name in {"source_extension.py", "ontology.py", "strategy_detector.py"}:
            return "pipeline/programming_languages.py"
        if name == "ontology_roles.yaml":
            return "pipeline/ontology_roles.yaml"
        if name == "call_classifier.py":
            return "pipeline/steps/step4_linking/call_classifier.py"
        if name == "test_call_classifier.py":
            return "tests/pipeline/step4_linking/test_call_classifier.py"
        if name.startswith("stdlib/"):
            return f"pipeline/steps/step4_linking/{name}"
        return "pipeline/__init__.py" if name == "__init__.py" else f"pipeline/{name}"
    if path.startswith("shared/llm/"):
        name = path.removeprefix("shared/llm/")
        renames = {"client.py": "llm_client.py", "config_loader.py": "llm_config_loader.py", "fanout.py": "llm_fanout.py"}
        if name in renames:
            return f"llm/client/{renames[name]}"
        if name in {"cost_budget.py", "pricing.py", "token_usage.py", "usage_jsonl_writer.py", "llm_pricing.yaml"}:
            return f"llm/client/usage/{name}"
        if name in {"structured_agent.py", "yaml_llm_runner.py"}:
            return f"llm/client/runners/{name}"
        return f"llm/client/{name}"
    if path.startswith("shared/log/"):
        name = path.removeprefix("shared/log/")
        if name in {"prompt_logger.py", "prompt_mask_patterns.py", "cache_message_filters.py"}:
            return f"llm/client/prompt_logging/{name}"
        return f"log/{name}"
    if path.startswith("shared/neo4j/"):
        name = path.removeprefix("shared/neo4j/")
        if name == "ids.py":
            return "graph/schema/node_ids.py"
        if name in {"rel_types.py", "schema_constraints.py"}:
            return f"graph/schema/{name}"
        if name in {"merge.py", "props.py"}:
            return "graph/node_upsert.py"
        if name == "nodes/schema.py":
            return "graph/nodes/db_schema.py"
        if name == "edges/test_calls.py":
            return "tests/graph/test_calls.py"
        return f"graph/{name}"
    if path.startswith("shared/workflow/"):
        name = path.removeprefix("shared/workflow/")
        renames = {
            "executor.py": "run_executor.py",
            "graph_builder.py": "langgraph_builder.py",
            "state.py": "workflow_state.py",
            "graph_stream.py": "graph_delta_stream.py",
            "step_utils.py": "run_executor.py",
        }
        if name == "test_graph_stream_source_identity.py":
            return f"tests/pipeline/execution/{name}"
        return f"pipeline/execution/{renames.get(name, name)}"
    return path


def _imports(paths: list[str]) -> dict[str, list[str]]:
    imported_by: dict[str, set[str]] = defaultdict(set)
    for importer in (path for path in paths if path.endswith(".py")):
        try:
            tree = ast.parse(Path(importer).read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                imported_by[module].add(importer)
    result: dict[str, list[str]] = {}
    for path in (item for item in paths if item.endswith(".py")):
        module = path[:-3].replace("/", ".").removesuffix(".__init__")
        consumers: set[str] = set()
        for imported, importers in imported_by.items():
            if imported == module or imported.startswith(f"{module}."):
                consumers.update(importers)
        consumers.discard(path)
        result[path] = sorted(consumers)
    return result


def main() -> None:
    paths = subprocess.check_output(
        ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", BASELINE],
        text=True,
        encoding="utf-8",
    ).splitlines()
    importers = _imports(paths)
    rows = ["old_path\tfinal_path\taction\tstatic_importer_count\tstatic_importers_sample\tnotes"]
    for path in paths:
        if path.startswith(("specs/", ".specify/", "deploy/")):
            continue
        target = _target(path)
        users = importers.get(path, [])
        action = "keep" if target == path else "delete-empty" if target.startswith("(") else "move"
        sample = ";".join(users[:8]) or "-"
        note = f"+{len(users) - 8} more" if len(users) > 8 else "-"
        rows.append(f"{path}\t{target}\t{action}\t{len(users)}\t{sample}\t{note}")
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
