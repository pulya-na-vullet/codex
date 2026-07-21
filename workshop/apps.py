from django.apps import AppConfig


class WorkshopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workshop"
    verbose_name = "ИТ-мастерская"

    def ready(self):
        import os
        import sys

        # app.py sets this while running migrate before runserver (sys.argv stays "app.py").
        if os.environ.get("IT_MASTER_SKIP_WORKERS") == "1":
            return

        # Avoid DB/network workers during migrate/test/shell bootstrap.
        skip_cmds = {"migrate", "makemigrations", "test", "collectstatic", "shell", "check"}
        if any(cmd in sys.argv for cmd in skip_cmds):
            return

        try:
            from workshop.printing import start_print_worker

            start_print_worker()
        except Exception:
            pass
        try:
            from workshop.messaging import start_max_long_poll_worker

            start_max_long_poll_worker()
        except Exception:
            pass
        try:
            from workshop.yandex_ai import start_ai_report_scheduler

            start_ai_report_scheduler()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to start Yandex AI report scheduler")
