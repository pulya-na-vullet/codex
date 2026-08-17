from __future__ import annotations

import os
import socket
from ipaddress import ip_address

TV_ADS_PATH = "/tv"


def _is_private_ipv4(value: str) -> bool:
    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return addr.version == 4 and (addr.is_private or addr.is_loopback)


def get_lan_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if _is_private_ipv4(ip):
                found.add(ip)
    except OSError:
        pass

    for probe in ("8.8.8.8", "1.1.1.1", "192.168.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 80))
                ip = sock.getsockname()[0]
                if _is_private_ipv4(ip):
                    found.add(ip)
        except OSError:
            continue

    addresses = sorted(ip for ip in found if ip != "127.0.0.1")
    if "127.0.0.1" in found:
        addresses.append("127.0.0.1")
    return addresses


def listen_port(default: int = 8000) -> int:
    raw = (os.getenv("IT_MASTER_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return int(default)


def listen_port_for_request(request=None, default: int = 8000) -> int:
    """Port to advertise for LAN clients (Smart TV). Ignore default 80/443 from tests."""
    if request is not None:
        try:
            req_port = int(request.get_port() or 0)
            if req_port and req_port not in (80, 443):
                return req_port
        except (TypeError, ValueError):
            pass
    return listen_port(default)


def tv_ads_urls(port: int | None = None) -> list[str]:
    """Ads page URLs: LAN first, then localhost (always at least one)."""
    port = int(port if port is not None else listen_port())
    urls: list[str] = []
    seen: set[str] = set()
    for ip in get_lan_ipv4_addresses():
        url = f"http://{ip}:{port}{TV_ADS_PATH}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    local = f"http://127.0.0.1:{port}{TV_ADS_PATH}"
    if local not in seen:
        urls.append(local)
    return urls


def primary_tv_ads_url(port: int | None = None) -> str:
    """Prefer a LAN address so a Smart TV can open the page without HDMI."""
    urls = tv_ads_urls(port)
    for url in urls:
        if "127.0.0.1" not in url:
            return url
    return urls[0]


def tv_ads_urls_for_request(request) -> list[str]:
    """LAN/localhost ads URLs plus the origin the admin page was opened from."""
    port = listen_port_for_request(request)
    urls = tv_ads_urls(port)
    if request is None:
        return urls
    host = (request.get_host() or "").strip()
    if not host:
        return urls
    scheme = "https" if request.is_secure() else "http"
    origin = f"{scheme}://{host}{TV_ADS_PATH}"
    if origin not in urls:
        urls.append(origin)
    return urls


def print_access_urls(host: str, port: int) -> None:
    print("=" * 60)
    print("ИТ-мастерская (Django): веб-сервер запущен")
    print(f"Локально:     http://127.0.0.1:{port}")
    lan_ips = get_lan_ipv4_addresses()
    lan_urls = [f"http://{ip}:{port}" for ip in lan_ips if ip != "127.0.0.1"]
    if lan_urls:
        print("В локальной Wi-Fi сети откройте на другом ПК:")
        for url in lan_urls:
            print(f"  {url}")
    else:
        print("LAN IP не определён. Узнайте IP (ipconfig) и откройте http://<IP>:8000")
    print("Вход: логин ITM, пароль pass")
    print("Если с другого ПК не открывается — разрешите порт 8000 в брандмауэре.")
    print("-" * 60)
    print("ТВ-реклама — два способа:")
    print("  HDMI:  Админ-панель → «По HDMI-кабелю» → выбрать монитор")
    print("  URL:   откройте на Smart TV в браузере (кабель HDMI не нужен):")
    for url in tv_ads_urls(port):
        suffix = "  (этот ПК)" if "127.0.0.1" in url else ""
        print(f"    {url}{suffix}")
    print("=" * 60)
