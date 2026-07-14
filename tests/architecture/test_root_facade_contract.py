import ast
import importlib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOVED_ASYNC_LOOP_NAME = "Async" + "QueryLoop"


def _root_facade_contract_violations(
    source: str,
    public_names: set[str],
) -> tuple[str, ...]:
    tree = ast.parse(source)
    violations: list[str] = []

    for index, statement in enumerate(tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue

        if isinstance(statement, ast.ImportFrom):
            module = statement.module or ""
            if statement.level != 0 or not (
                module == "agentos" or module.startswith("agentos.")
            ):
                violations.append(
                    f"non-agentos-import:{module or '<relative>'}:{statement.lineno}",
                )
                continue

            for alias in statement.names:
                if alias.name == "*":
                    violations.append(
                        f"star-import:{module}:{statement.lineno}",
                    )
                elif alias.asname is None:
                    violations.append(
                        f"non-identity-import:{alias.name}:{statement.lineno}",
                    )
                elif alias.asname != alias.name:
                    violations.append(
                        "renamed-import:"
                        f"{alias.name}:{alias.asname}:{statement.lineno}",
                    )
                elif alias.name not in public_names:
                    violations.append(
                        f"undeclared-import:{alias.name}:{statement.lineno}",
                    )
            continue

        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                if not (
                    isinstance(statement.value, ast.List)
                    and all(
                        isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                        for element in statement.value.elts
                    )
                ):
                    violations.append(f"invalid-__all__:{statement.lineno}")
                continue
            if isinstance(target, ast.Name) and target.id == "__version__":
                if not (
                    isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    violations.append(f"invalid-__version__:{statement.lineno}")
                continue

        violations.append(
            f"forbidden-statement:{type(statement).__name__}:{statement.lineno}",
        )

    return tuple(violations)


@pytest.mark.parametrize(
    ("source", "public_names", "expected_violations"),
    [
        pytest.param(
            "from agentos.runtime import *\n",
            set(),
            ("star-import:agentos.runtime:1",),
            id="star-import",
        ),
        pytest.param(
            "if enabled:\n    from agentos.runtime import Agent as Agent\n",
            {"Agent"},
            ("forbidden-statement:If:1",),
            id="nested-import-if",
        ),
        pytest.param(
            "try:\n    from agentos.runtime import Agent as Agent\n"
            "except ImportError:\n    pass\n",
            {"Agent"},
            ("forbidden-statement:Try:1",),
            id="nested-import-try",
        ),
        pytest.param(
            "while enabled:\n    from agentos.runtime import Agent as Agent\n",
            {"Agent"},
            ("forbidden-statement:While:1",),
            id="nested-import-while",
        ),
        pytest.param(
            "for item in items:\n    from agentos.runtime import Agent as Agent\n",
            {"Agent"},
            ("forbidden-statement:For:1",),
            id="nested-import-for",
        ),
        pytest.param(
            "def load_agent():\n    from agentos.runtime import Agent as Agent\n",
            {"Agent"},
            ("forbidden-statement:FunctionDef:1",),
            id="nested-import-function",
        ),
        pytest.param(
            "class AgentFacade:\n    from agentos.runtime import Agent as Agent\n",
            {"Agent"},
            ("forbidden-statement:ClassDef:1",),
            id="nested-import-class",
        ),
        pytest.param(
            "from agentos.runtime import HiddenRuntime as Agent\n",
            {"Agent"},
            ("renamed-import:HiddenRuntime:Agent:1",),
            id="renamed-alias",
        ),
        pytest.param(
            "from agentos.runtime import Agent\n",
            {"Agent"},
            ("non-identity-import:Agent:1",),
            id="implicit-alias",
        ),
        pytest.param(
            "import agentos.runtime as Agent\n",
            {"Agent"},
            ("forbidden-statement:Import:1",),
            id="plain-import",
        ),
        pytest.param(
            'import importlib\nimportlib.import_module("agentos.runtime")\n',
            set(),
            (
                "forbidden-statement:Import:1",
                "forbidden-statement:Expr:2",
            ),
            id="importlib-call",
        ),
        pytest.param(
            "from importlib import import_module\n"
            'import_module("agentos.runtime")\n',
            set(),
            (
                "non-agentos-import:importlib:1",
                "forbidden-statement:Expr:2",
            ),
            id="import-module-call",
        ),
        pytest.param(
            "from agentos.runtime import HiddenRuntime as HiddenRuntime\n",
            set(),
            ("undeclared-import:HiddenRuntime:1",),
            id="undeclared-import",
        ),
        pytest.param(
            'Agent = __import__("agentos.runtime", fromlist=["Agent"]).Agent\n',
            {"Agent"},
            ("forbidden-statement:Assign:1",),
            id="dunder-import-assignment",
        ),
        pytest.param(
            'exec("from agentos.runtime import Agent as Agent")\n',
            {"Agent"},
            ("forbidden-statement:Expr:1",),
            id="exec",
        ),
        pytest.param(
            'eval("__import__(\\\"agentos.runtime\\\")")\n',
            {"Agent"},
            ("forbidden-statement:Expr:1",),
            id="eval",
        ),
        pytest.param(
            "def __getattr__(name):\n    return object()\n",
            {"Agent"},
            ("forbidden-statement:FunctionDef:1",),
            id="module-getattr",
        ),
        pytest.param(
            'globals()["Agent"] = object()\n',
            {"Agent"},
            ("forbidden-statement:Assign:1",),
            id="globals-mutation",
        ),
        pytest.param(
            '__all__ = list(("Agent",))\n',
            {"Agent"},
            ("invalid-__all__:1",),
            id="dynamic-all",
        ),
        pytest.param(
            '__version__ = make_version()\n',
            set(),
            ("invalid-__version__:1",),
            id="dynamic-version",
        ),
    ],
)
def test_root_facade_contract_rejects_forbidden_mutations(
    source: str,
    public_names: set[str],
    expected_violations: tuple[str, ...],
) -> None:
    violations = _root_facade_contract_violations(source, public_names)

    assert violations == expected_violations


def test_root_facade_contract_allows_only_static_facade_statements() -> None:
    source = '''"""Agent OS public facade."""

from agentos.runtime import Agent as Agent

__all__ = ["Agent", "__version__"]
__version__ = "1.2.3"
'''

    assert _root_facade_contract_violations(
        source,
        {"Agent", "__version__"},
    ) == ()


def test_root_facade_import_contract_allows_explicit_identity_alias() -> None:
    violations = _root_facade_contract_violations(
        "from agentos.runtime import Agent as Agent\n",
        {"Agent"},
    )

    assert violations == ()


def test_root_facade_only_imports_declared_public_exports() -> None:
    root_init = PROJECT_ROOT / "src" / "agentos" / "__init__.py"
    agentos = importlib.import_module("agentos")

    violations = _root_facade_contract_violations(
        root_init.read_text(encoding="utf-8"),
        set(agentos.__all__),
    )

    assert violations == ()


def test_root_facade_excludes_removed_loop_and_sync_adapter_exports() -> None:
    agentos = importlib.import_module("agentos")
    sync = importlib.import_module("agentos.sync")

    assert REMOVED_ASYNC_LOOP_NAME not in agentos.__all__
    assert not hasattr(agentos, REMOVED_ASYNC_LOOP_NAME)
    assert set(agentos.__all__).isdisjoint(sync.__all__)
