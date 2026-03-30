from __future__ import annotations

import argparse
import os
import sys

from .hardware_client import (
    collect_samples_over_interval,
    get_local_device_name,
    normalize_device_name,
    push_samples,
    run_push_loop,
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _get_config(args: argparse.Namespace) -> tuple[str, str, str, float]:
    url = (args.url or os.getenv("HARDWARE_PUSH_URL", "")).strip()
    token = (args.token or os.getenv("HARDWARE_PUSH_TOKEN", "")).strip()
    raw_device = (args.device or os.getenv("HARDWARE_DEVICE_NAME", "")).strip()
    if raw_device:
        device = normalize_device_name(raw_device) or get_local_device_name()
    else:
        device = get_local_device_name()
    interval = float(args.interval or os.getenv("HARDWARE_PUSH_INTERVAL", "10"))
    return url, token, device, interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoLab client: sample local hardware and push to server.")
    parser.add_argument("--url", default="", help="Override HARDWARE_PUSH_URL")
    parser.add_argument("--token", default="", help="Override HARDWARE_PUSH_TOKEN")
    parser.add_argument("--device", default="", help="Override HARDWARE_DEVICE_NAME")
    parser.add_argument("--interval", default="", help="Override HARDWARE_PUSH_INTERVAL")
    parser.add_argument("--once", action="store_true", help="Collect one batch and exit")
    parser.add_argument("--verbose", action="store_true", help="Print per-cycle result")
    return parser


def main() -> None:
    _load_dotenv()
    args = build_parser().parse_args()
    url, token, device, interval = _get_config(args)

    if not url or not token or not device:
        print(
            "Missing required config. Set HARDWARE_PUSH_URL and HARDWARE_PUSH_TOKEN "
            "(HARDWARE_DEVICE_NAME is optional).",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.once:
        samples = collect_samples_over_interval(interval)
        if not samples:
            print("No samples collected.", file=sys.stderr)
            sys.exit(2)
        success, msg = push_samples(url, token, device, samples)
        if not success:
            print(f"Push failed: {msg}", file=sys.stderr)
            sys.exit(3)
        if args.verbose:
            print(f"Pushed {len(samples)} samples for device '{device}'.")
        return

    run_push_loop(url, token, device, interval, verbose=args.verbose)


if __name__ == "__main__":
    main()

