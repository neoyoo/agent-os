from __future__ import annotations

from agentos.cli.application import CliHostFactory


async def run_serve(host_factory: CliHostFactory) -> None:
    """在最窄 Server host 中运行 ingress。"""

    async with host_factory.open_server_host() as host:
        await host.server.serve()


__all__ = ["run_serve"]
