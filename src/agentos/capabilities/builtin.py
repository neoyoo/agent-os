from pathlib import Path

from agentos.capabilities.tools import RegisteredTool


class BuiltinToolError(RuntimeError):
    """内置工具执行失败。"""


def read_file_tool(root: str | Path = ".") -> RegisteredTool:
    """创建一个只能读取 root 内文件的 read_file 工具。"""

    root_path = Path(root).resolve()

    def _read_file(arguments: dict[str, object]) -> str:
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise BuiltinToolError("read_file requires a non-empty path")

        target = (root_path / raw_path).resolve()
        if not target.is_relative_to(root_path):
            raise BuiltinToolError("read_file path escapes the configured root")
        if not target.is_file():
            raise BuiltinToolError(f"read_file target is not a file: {raw_path}")
        return target.read_text()

    return RegisteredTool(
        name="read_file",
        description="读取项目内文本文件内容。",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对项目根目录的文件路径。",
                },
            },
            "required": ["path"],
        },
        handler=_read_file,
    )
