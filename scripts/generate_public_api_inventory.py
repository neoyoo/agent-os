"""从稳定性策略生成 Public API inventory。"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import TypedDict


_FROZENSET_RE = re.compile(r"frozenset\(\{(?P<items>[^{}]*)\}\)")
_OBJECT_REPR_ADDRESS_RE = re.compile(
    r"<(?P<qualified>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*) "
    r"object at 0x[0-9A-Fa-f]+>",
)


class PolicyError(ValueError):
    """稳定性策略与模块公开面不一致。"""


class ModulePolicy(TypedDict):
    stable: list[str]
    experimental: list[str]


def normalize_signature(signature: str) -> str:
    """消除 Python minor 和运行进程导致的签名文本漂移。"""

    def normalize_frozenset(match: re.Match[str]) -> str:
        values = ast.literal_eval("{" + match.group("items") + "}")
        ordered = ", ".join(repr(value) for value in sorted(values, key=repr))
        return f"frozenset({{{ordered}}})"

    normalized = signature.replace("pathlib._local.Path", "pathlib.Path")
    normalized = _FROZENSET_RE.sub(normalize_frozenset, normalized)
    return _OBJECT_REPR_ADDRESS_RE.sub(
        r"<\g<qualified> object>",
        normalized,
    )


def public_export_names(module: ModuleType) -> tuple[str, ...]:
    """读取并校验模块的显式公开导出。"""

    exports = getattr(module, "__all__", None)
    if exports is None:
        raise PolicyError(f"{module.__name__} must define __all__")
    names = tuple(str(name) for name in exports)
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise PolicyError(
            f"{module.__name__} has duplicate __all__ exports: {', '.join(duplicates)}",
        )
    return names


def classify_exports(
    module_name: str,
    public_names: tuple[str, ...],
    policy: dict[str, list[str]],
) -> dict[str, str]:
    """验证并展开模块的 stable/experimental 分类。"""

    expected_keys = {"stable", "experimental"}
    if set(policy) != expected_keys:
        raise PolicyError(
            f"{module_name} policy keys must be stable and experimental",
        )

    stable = policy["stable"]
    experimental = policy["experimental"]
    if not all(
        isinstance(group, list)
        and all(isinstance(name, str) for name in group)
        for group in (stable, experimental)
    ):
        raise PolicyError(f"{module_name} classifications must be string lists")
    classified = stable + experimental
    duplicates = sorted(
        name for name, count in Counter(classified).items() if count > 1
    )
    if duplicates:
        raise PolicyError(
            f"{module_name} duplicate classifications: {', '.join(duplicates)}",
        )

    public = set(public_names)
    governed = set(classified)
    missing = sorted(public - governed)
    if missing:
        raise PolicyError(
            f"{module_name} missing classifications: {', '.join(missing)}",
        )
    deleted = sorted(governed - public)
    if deleted:
        raise PolicyError(
            f"{module_name} deleted policy exports: {', '.join(deleted)}",
        )

    return {
        name: "stable" if name in stable else "experimental"
        for name in public_names
    }


def _signature_of(value: object) -> str:
    if not callable(value):
        return "non-callable"
    try:
        signature = inspect.signature(value, eval_str=False)
    except (TypeError, ValueError):
        return "unavailable"
    return normalize_signature(str(signature))


def _protocol_methods(value: object) -> dict[str, str] | None:
    if not getattr(value, "_is_protocol", False):
        return None
    methods = {
        name: _signature_of(method)
        for name, method in inspect.getmembers(value, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    if not methods:
        raise PolicyError(f"{value.__module__}.{value.__name__} has no public methods")
    return methods


def load_policy(path: Path) -> dict[str, ModulePolicy]:
    """读取稳定性策略，并拒绝策略层的非分类字段。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PolicyError("policy must be a JSON object")
    if set(payload) != {"schema", "schema_version", "modules"}:
        raise PolicyError("policy must contain only schema, schema_version, and modules")
    if payload["schema"] != "agentos.public_api_stability":
        raise PolicyError("unsupported public API stability policy schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise PolicyError("unsupported public API stability policy schema version")
    modules = payload["modules"]
    if not isinstance(modules, dict) or not modules:
        raise PolicyError("policy modules must be a non-empty object")
    validated: dict[str, ModulePolicy] = {}
    for module_name, module_policy in modules.items():
        if not isinstance(module_name, str) or not module_name.strip():
            raise PolicyError("policy module names must be non-empty strings")
        if not isinstance(module_policy, dict):
            raise PolicyError(f"{module_name} module policy must be an object")
        if set(module_policy) != {"stable", "experimental"}:
            raise PolicyError(
                f"{module_name} policy keys must be stable and experimental",
            )

        classifications: dict[str, list[str]] = {}
        for classification in ("stable", "experimental"):
            entries = module_policy[classification]
            if not isinstance(entries, list):
                raise PolicyError(
                    f"{module_name} {classification} classification must be a list",
                )
            if not all(isinstance(entry, str) and entry for entry in entries):
                raise PolicyError(
                    f"{module_name} {classification} classification entries "
                    "must be non-empty strings",
                )
            classifications[classification] = list(entries)
        validated[module_name] = ModulePolicy(
            stable=classifications["stable"],
            experimental=classifications["experimental"],
        )
    return validated


def build_inventory(
    policy_modules: dict[str, ModulePolicy],
) -> dict[str, object]:
    """从模块公开面与独立稳定性策略构建 inventory。"""

    modules: dict[str, object] = {}
    for module_name in sorted(policy_modules):
        module = importlib.import_module(module_name)
        public_names = public_export_names(module)
        classifications = classify_exports(
            module_name,
            public_names,
            policy_modules[module_name],
        )
        exports: dict[str, object] = {}
        for export_name in sorted(public_names):
            exported = getattr(module, export_name)
            export_payload: dict[str, object] = {
                "signature": _signature_of(exported),
                "stability": classifications[export_name],
            }
            methods = _protocol_methods(exported)
            if methods is not None:
                export_payload["methods"] = methods
            exports[export_name] = export_payload
        modules[module_name] = {"exports": exports}

    return {
        "generated_by": "scripts/generate_public_api_inventory.py",
        "modules": modules,
        "package": "agentos",
        "schema": "agentos.public_api_inventory",
        "schema_version": 1,
        "signature_format": "normalized inspect.signature string or non-callable",
    }


def write_inventory(payload: dict[str, object], output: Path) -> None:
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(output)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        policy = load_policy(args.policy)
    except OSError as error:
        print(f"error: unable to read policy: {error.strerror or error}", file=sys.stderr)
        return 2
    except UnicodeError:
        print("error: policy is not valid UTF-8", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(
            f"error: policy is not valid JSON: {error.msg} "
            f"at line {error.lineno} column {error.colno}",
            file=sys.stderr,
        )
        return 2
    except PolicyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        inventory = build_inventory(policy)
    except PolicyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        write_inventory(inventory, args.output)
    except OSError as error:
        print(
            f"error: unable to write inventory: {error.strerror or error}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
