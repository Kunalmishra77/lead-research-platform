"""Dev-only DNS-over-HTTPS for hosts some Indian ISPs sinkhole (*.supabase.co, ADR-0002).

Patches socket.getaddrinfo, which asyncio (and so aioboto3/httpx/asyncpg) use for name resolution.
A/IPv4 only. Never active outside development/test.
"""

import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.request
from typing import Any

_DOH = "https://1.1.1.1/dns-query?name={host}&type=A"
_SUFFIXES = (".supabase.co",)
_TTL_S = 300.0
_cache: dict[str, tuple[float, list[str]]] = {}
_lock = threading.Lock()
_installed = False
_original_getaddrinfo = socket.getaddrinfo


def _resolve(host: str) -> list[str]:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(host)
        if hit and hit[0] > now:
            return hit[1]
    request = urllib.request.Request(  # noqa: S310 - fixed https URL
        _DOH.format(host=host), headers={"accept": "application/dns-json"}
    )
    with urllib.request.urlopen(  # noqa: S310 - fixed https URL
        request, timeout=10, context=ssl.create_default_context()
    ) as response:
        answers = json.load(response).get("Answer", [])
    ips = [
        a["data"]
        for a in answers
        if a.get("type") == 1
        and isinstance(ipaddress.ip_address(a.get("data", "0")), ipaddress.IPv4Address)
    ]
    if ips:  # never cache an empty answer
        with _lock:
            _cache[host] = (now + _TTL_S, ips)
    return ips


def _patched(
    host: bytes | str | None,
    port: bytes | str | int | None,
    family: int = 0,
    *args: Any,
    **kwargs: Any,
) -> list[Any]:
    name = host.decode() if isinstance(host, bytes) else host
    if name and name.lower().rstrip(".").endswith(_SUFFIXES) and family in (0, socket.AF_INET):
        ips = _resolve(name.lower().rstrip("."))
        if ips:
            return _original_getaddrinfo(ips[0], port, socket.AF_INET, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, f"DoH found no A record for {name}")
    return _original_getaddrinfo(host, port, family, *args, **kwargs)


def install_dev_dns(*, enabled: bool, node_env: str) -> bool:
    global _installed  # noqa: PLW0603 - process-wide patch, installed once
    if _installed or not enabled or node_env not in ("development", "test"):
        return False
    socket.getaddrinfo = _patched
    _installed = True
    return True
