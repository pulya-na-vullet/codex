from __future__ import annotations

import os

from workshop.network import print_access_urls


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    host = os.getenv("IT_MASTER_HOST", "0.0.0.0")
    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    print_access_urls(host, port)

    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", "runserver", f"{host}:{port}", "--noreload"])


if __name__ == "__main__":
    main()
