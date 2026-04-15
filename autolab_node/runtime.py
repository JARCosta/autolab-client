from __future__ import annotations

from .cli import load_dotenv_if_available, resolve_config
from .daemon import NodeDaemon


def main() -> None:
    load_dotenv_if_available()
    config = resolve_config()
    NodeDaemon(
        push_url=config.push_url,
        push_token=config.push_token,
        pull_token=config.pull_token,
        device=config.device,
        node_url=config.node_url,
        host=config.listen_host,
        port=config.listen_port,
        verbose=config.verbose,
    ).start()
