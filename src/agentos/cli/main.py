from __future__ import annotations

import asyncio
import os
import sys

from agentos.cli.application import (
    CliHostFactory,
    CliInterruptedError,
    load_cli_host_factory,
    run_cli_command,
)
from agentos.cli.commands.init import run_init
from agentos.cli.errors import map_cli_error
from agentos.cli.output import write_error
from agentos.cli.parser import build_parser


def main(
    argv: list[str] | None = None,
    *,
    host_factory: CliHostFactory | None = None,
) -> int:
    """解析并执行一次 CLI 调用，返回稳定的进程退出码。"""

    try:
        args = build_parser().parse_args(argv)
        if args.command == "init":
            run_init(args.path, stdout=sys.stdout)
            return 0
        factory = (
            host_factory
            if host_factory is not None
            else load_cli_host_factory(args.factory, os.environ)
        )
        asyncio.run(run_cli_command(args, factory))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError, CliInterruptedError):
        write_error("interrupted", "operation interrupted")
        return 130
    except Exception as error:
        mapped = map_cli_error(error)
        write_error(mapped.code, mapped.message)
        return mapped.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["main"]
