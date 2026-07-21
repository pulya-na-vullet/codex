from __future__ import annotations

import os
import sys


def _run_migrate() -> None:
    """Always apply Django migrations before serving (safe / idempotent)."""
    from django.core.management import execute_from_command_line

    print("=== Миграции БД: python manage.py migrate --noinput ===", flush=True)
    # Prevent AppConfig.ready() workers from touching DB during migrate
    # (execute_from_command_line does not replace sys.argv when called from app.py).
    prev = os.environ.get("IT_MASTER_SKIP_WORKERS")
    os.environ["IT_MASTER_SKIP_WORKERS"] = "1"
    try:
        execute_from_command_line(["manage.py", "migrate", "--noinput"])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            print(f"Ошибка миграции (код {code}). Сервер не запущен.", file=sys.stderr, flush=True)
            raise SystemExit(code) from exc
    except Exception as exc:
        print(f"Ошибка миграции: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
    finally:
        if prev is None:
            os.environ.pop("IT_MASTER_SKIP_WORKERS", None)
        else:
            os.environ["IT_MASTER_SKIP_WORKERS"] = prev


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    host = os.getenv("IT_MASTER_HOST", "0.0.0.0")
    port = int(os.getenv("IT_MASTER_PORT", "8000"))

    from workshop.network import print_access_urls

    print_access_urls(host, port)
    _run_migrate()

    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", "runserver", f"{host}:{port}", "--noreload"])


if __name__ == "__main__":
    main()
