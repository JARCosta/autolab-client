from __future__ import annotations

import argparse
import os
import socket
import sys
from dataclasses import dataclass

from .hardware_client import get_local_device_name, normalize_device_name


@dataclass(frozen=True)
class NodeConfig:
    push_url: str
    push_token: str
    pull_token: str
    device: str
    node_url: str
    listen_host: str
    listen_port: int
    verbose: bool


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def detect_lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AutoLab node: sample local hardware and push to server."
    )
    parser.add_argument("--url", default="", help="Override HARDWARE_PUSH_URL")
    parser.add_argument("--token", default="", help="Override HARDWARE_TOKEN")
    parser.add_argument("--device", default="", help="Override HARDWARE_DEVICE_NAME")
    parser.add_argument(
        "--node-url",
        default="",
        help="Override HARDWARE_NODE_URL (server-reachable URL for this node)",
    )
    parser.add_argument(
        "--listen-host",
        default=os.getenv("HARDWARE_NODE_LISTEN_HOST")
        or os.getenv("HARDWARE_PULL_HOST", "0.0.0.0"),
        help="Bind host (HARDWARE_NODE_LISTEN_HOST or legacy HARDWARE_PULL_HOST)",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=int(
            os.getenv("HARDWARE_NODE_LISTEN_PORT")
            or os.getenv("HARDWARE_PULL_PORT", "8765")
        ),
        help="Bind port (HARDWARE_NODE_LISTEN_PORT or legacy HARDWARE_PULL_PORT)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-cycle result")
    return parser


def resolve_config(argv: list[str] | None = None) -> NodeConfig:
    args = build_parser().parse_args(argv)

    push_url = (args.url or os.getenv("HARDWARE_PUSH_URL", "")).strip()
    push_token = (args.token or os.getenv("HARDWARE_TOKEN", "")).strip()
    pull_token = push_token
    device = normalize_device_name(args.device) or get_local_device_name()

    if not push_url or not push_token or not device:
        print(
            "Missing required config. Set HARDWARE_PUSH_URL and HARDWARE_TOKEN "
            "(HARDWARE_DEVICE_NAME is optional).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    node_url = (
        args.node_url
        or os.getenv("HARDWARE_NODE_URL", "")
        or os.getenv("HARDWARE_CLIENT_URL", "")
    ).strip()
    if not node_url:
        listen_host = args.listen_host
        if listen_host == "0.0.0.0":
            listen_host = detect_lan_ip()
        node_url = f"http://{listen_host}:{args.listen_port}"

    return NodeConfig(
        push_url=push_url,
        push_token=push_token,
        pull_token=pull_token,
        device=device,
        node_url=node_url,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        verbose=args.verbose,
    )
