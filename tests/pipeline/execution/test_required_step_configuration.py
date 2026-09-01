import pytest

from pipeline.execution.step_config import StepConfigurationError
from pipeline.stages.ddl.parse_ddl_step import parse_ddl_step
from pipeline.stages.ast.load_ast_step import load_ast_step
from pipeline.stages.links.tables.link_tables_by_regex_step import (
    link_deterministic_step,
)
from pipeline.stages.context.build_context_step import build_context_step
from pipeline.stages.output.enrich_table_description_step import (
    enrich_table_description_step,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step", "required_name"),
    [
        (parse_ddl_step, "codebase"),
        (load_ast_step, "codebase"),
        (link_deterministic_step, "codebase"),
        (build_context_step, "strategy"),
        (enrich_table_description_step, "strategy"),
    ],
)
async def test_required_step_configuration_never_becomes_an_empty_success(
    step, required_name: str,
) -> None:
    with pytest.raises(
        StepConfigurationError,
        match=rf"requires RunnableConfig\.configurable\['{required_name}'\]",
    ):
        await step({}, {"configurable": {}})
