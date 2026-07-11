from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict


THRESHOLDS = {
    "responsibility_review": 300,
    "split_or_exception": 500,
    "no_new_responsibility": 800,
}


class GovernedModule(TypedDict):
    path: str
    lines: int
    gate: str


class ValidationError(Exception):
    """表示模块规模基线输入不满足生成约束。"""


def _gate_for_line_count(lines: int) -> str:
    if lines >= THRESHOLDS["no_new_responsibility"]:
        return "no_new_responsibility"
    if lines >= THRESHOLDS["split_or_exception"]:
        return "split_or_exception"
    return "responsibility_review"


def scan_governed_modules(root: Path) -> list[GovernedModule]:
    """扫描需要规模治理的 AgentOS Python 模块。"""
    source_root = root.resolve()
    if not source_root.exists():
        raise ValidationError(f"source root does not exist: {source_root}")
    if not source_root.is_dir():
        raise ValidationError(f"source root is not a directory: {source_root}")

    python_modules = tuple(source_root.rglob("*.py"))
    if not python_modules:
        raise ValidationError(
            f"source root contains no Python modules: {source_root}",
        )

    project_root = source_root.parent.parent
    modules: list[GovernedModule] = []

    for path in python_modules:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count < THRESHOLDS["responsibility_review"]:
            continue
        modules.append(
            {
                "path": path.relative_to(project_root).as_posix(),
                "lines": line_count,
                "gate": _gate_for_line_count(line_count),
            },
        )

    return sorted(modules, key=lambda item: (-item["lines"], item["path"]))


def _build_payload(root: Path) -> dict[str, object]:
    return {
        "schema": "agentos.module_size_baseline",
        "schema_version": 1,
        "thresholds": THRESHOLDS,
        "modules": scan_governed_modules(root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the governed AgentOS module-size baseline.",
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        payload = _build_payload(args.root)
    except ValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
