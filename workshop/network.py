from __future__ import annotations

import socket
from ipaddress import ip_address


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
    print("=" * 60)
