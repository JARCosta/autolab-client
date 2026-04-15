from __future__ import annotations

from .logging_config import setup_logging

log = setup_logging("autolab_node")


def build_server_api_url(push_url: str, path: str) -> str:
    base = push_url.rstrip("/")
    if base.endswith("/push"):
        base = base[:-5]
    return f"{base}/{path.lstrip('/')}"


def ping_server(*, push_url: str, token: str, device: str) -> bool:
    try:
        import requests
    except ImportError:
        return False

    url = build_server_api_url(push_url, "ping")
    try:
        response = requests.post(
            url,
            json={"token": token, "device": device},
            timeout=15,
        )
        if response.status_code >= 400:
            return False
        return bool(response.json().get("pong", False))
    except (requests.RequestException, ValueError) as exc:
        log.warning("Ping failed: %s", exc)
        return False


def register_node(
    *,
    push_url: str,
    token: str,
    device: str,
    node_url: str,
    samples: list[dict],
) -> tuple[bool, str]:
    try:
        import requests
    except ImportError:
        return False, "requests not installed; cannot register with server"

    url = build_server_api_url(push_url, "register")
    payload = {
        "token": token,
        "device": device,
        "node_url": node_url,
        "samples": samples,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code >= 400:
            return False, f"status={response.status_code} body={response.text[:200]}"
        try:
            inserted = response.json().get("inserted")
        except ValueError:
            inserted = "n/a"
        return True, f"inserted={inserted}"
    except requests.RequestException as exc:
        return False, str(exc)
