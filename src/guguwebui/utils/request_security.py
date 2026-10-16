"""请求来源相关的安全判定：真实客户端 IP、HTTPS 识别、Origin 校验。

经 frp / nginx 等反向代理暴露到公网时，WebUI 看到的 TCP 对端是代理（通常为 127.0.0.1），
真实客户端 IP 只能从代理写入的 X-Forwarded-For / X-Real-IP 取得。这些请求头客户端
也能伪造，因此只在对端属于配置项 ``trusted_proxies`` 时才采信：

- ``trusted_proxies`` 为空（默认）：忽略所有转发头，客户端 IP = TCP 对端；
- 对端在 ``trusted_proxies`` 内：自右向左跳过可信代理，取 X-Forwarded-For 中第一个
  不可信的地址（代理追加在右侧，左侧可被客户端伪造）；无 X-Forwarded-For 时用 X-Real-IP。

独立运行时 uvicorn 以 ``proxy_headers=False`` 启动（见 __init__._start_standalone_server），
确保 ``request.client`` 是原始对端，转发头只由本模块解析。
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, List, Optional, Set, Union
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

_Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def _get_config(request: HTTPConnection) -> dict:
    config_service = getattr(request.app.state, "config_service", None)
    if config_service is None:
        return {}
    try:
        return config_service.get_config() or {}
    except Exception:
        return {}


def _parse_ip(value: str) -> Optional[ipaddress._BaseAddress]:
    """解析 IP，兼容 "1.2.3.4:5678" 与 "[::1]:80" 这类带端口写法。"""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def parse_networks(raw_items: Iterable) -> List[_Network]:
    """把配置中的 IP / CIDR 列表解析为网段；非法项忽略。"""
    networks: List[_Network] = []
    for raw in raw_items or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            networks.append(ipaddress.ip_network(raw.strip(), strict=False))
        except ValueError:
            continue
    return networks


def ip_in_networks(ip: str, networks: List[_Network]) -> bool:
    addr = _parse_ip(ip)
    if addr is None:
        return False
    return any(addr.version == net.version and addr in net for net in networks)


def _peer_ip(request: HTTPConnection) -> str:
    return request.client.host if request.client else ""


def _trusted_networks(request: HTTPConnection, config: Optional[dict] = None) -> List[_Network]:
    config = _get_config(request) if config is None else config
    return parse_networks(config.get("trusted_proxies") or [])


def is_from_trusted_proxy(request: HTTPConnection, config: Optional[dict] = None) -> bool:
    networks = _trusted_networks(request, config)
    return bool(networks) and ip_in_networks(_peer_ip(request), networks)


def get_client_ip(request: HTTPConnection, config: Optional[dict] = None) -> str:
    """返回用于限流 / 审计 / IP 白名单的客户端 IP（见模块说明）。"""
    peer = _peer_ip(request)
    networks = _trusted_networks(request, config)
    if not networks or not ip_in_networks(peer, networks):
        return peer or "unknown"

    forwarded_for = request.headers.get("x-forwarded-for") or ""
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if ip_in_networks(hop, networks):
            continue
        addr = _parse_ip(hop)
        # 最右侧不可信的一跳由可信代理写入；无法解析说明代理行为异常，退回对端地址
        return str(addr) if addr is not None else (peer or "unknown")
    if hops:
        # 整条链都是可信代理：取最左侧（最接近客户端）的一跳
        addr = _parse_ip(hops[0])
        if addr is not None:
            return str(addr)

    real_ip = _parse_ip(request.headers.get("x-real-ip") or "")
    if real_ip is not None:
        return str(real_ip)
    return peer or "unknown"


def request_is_https(request: HTTPConnection, config: Optional[dict] = None) -> bool:
    """当前请求在浏览器侧是否为 HTTPS（直连 TLS，或可信代理声明 X-Forwarded-Proto: https）。"""
    if request.url.scheme in ("https", "wss"):
        return True
    if not is_from_trusted_proxy(request, config):
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _normalize_netloc(netloc: str, scheme: str = "") -> str:
    netloc = (netloc or "").strip().lower()
    if not netloc:
        return ""
    if netloc.startswith("["):
        host, _, rest = netloc[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        host = f"[{host}]"
    elif netloc.count(":") == 1:
        host, _, port = netloc.partition(":")
    else:
        host, port = netloc, ""
    if port and port in _DEFAULT_PORTS.values() and (not scheme or _DEFAULT_PORTS.get(scheme) == port):
        port = ""
    return f"{host}:{port}" if port else host


def _allowed_hosts(request: HTTPConnection, config: dict) -> Set[str]:
    hosts: Set[str] = set()
    host = _normalize_netloc(request.headers.get("host") or "")
    if host:
        hosts.add(host)
    if is_from_trusted_proxy(request, config):
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0]
        normalized = _normalize_netloc(forwarded_host)
        if normalized:
            hosts.add(normalized)
    for item in config.get("allowed_origins") or []:
        if not isinstance(item, str) or not item.strip():
            continue
        item = item.strip()
        if "://" in item:
            parts = urlsplit(item)
            normalized = _normalize_netloc(parts.netloc, parts.scheme)
        else:
            normalized = _normalize_netloc(item)
        if normalized:
            hosts.add(normalized)
    return hosts


def origin_allowed(request: HTTPConnection, origin: str, config: Optional[dict] = None) -> bool:
    """Origin 是否与当前站点同源（或在 allowed_origins 中）。"Origin: null" 一律拒绝。"""
    origin = (origin or "").strip()
    if not origin or origin.lower() == "null":
        return False
    config = _get_config(request) if config is None else config
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return _normalize_netloc(parts.netloc, parts.scheme) in _allowed_hosts(request, config)
