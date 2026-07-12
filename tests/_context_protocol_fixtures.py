from agentos.context import ContextRenderer
from agentos.context.projection import default_system_section_registry
from agentos.tokens import HeuristicTokenCounter


def default_context_renderer() -> ContextRenderer:
    return ContextRenderer(
        registry=default_system_section_registry(),
        token_counter=HeuristicTokenCounter(),
    )
