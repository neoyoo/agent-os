from __future__ import annotations

from importlib import import_module
import inspect

import pytest


_PUBLIC_MODULES = (
    "agentos.cli.application",
    "agentos.cli.auth",
    "agentos.cli.errors",
    "agentos.cli.main",
    "agentos.cli.output",
    "agentos.cli.parser",
    "agentos.cli.commands.artifact",
    "agentos.cli.commands.init",
    "agentos.cli.commands.migrate",
    "agentos.cli.commands.relay",
    "agentos.cli.commands.run",
    "agentos.cli.commands.serve",
    "agentos.cli.commands.worker",
    "agentos.distributed.profile",
    "agentos.distributed.migrations",
    "agentos.distributed.migrations.models",
    "agentos.distributed.migrations.protocols",
    "agentos.distributed.migrations.service",
    "agentos.distributed.postgres.migrations",
    "agentos.distributed.postgres.state",
)


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_wave5b_public_callables_have_chinese_docstrings(module_name: str) -> None:
    module = import_module(module_name)

    for name in module.__all__:
        value = getattr(module, name)
        if not (inspect.isclass(value) or inspect.isfunction(value)):
            continue
        docstring = inspect.getdoc(value)
        assert docstring is not None, f"{module_name}.{name} 缺少 docstring"
        assert any("\u4e00" <= char <= "\u9fff" for char in docstring), (
            f"{module_name}.{name} 的 docstring 必须使用中文"
        )
