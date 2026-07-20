from django.apps import AppConfig


class HubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hub"

    def ready(self) -> None:
        # Start Max long-poll so "Регистрация: Дизайнер" works without a public webhook.
        try:
            from django.conf import settings

            if getattr(settings, "MAX_LONG_POLL_WORKER", True):
                from .max_bot import start_max_long_poll_worker

                start_max_long_poll_worker()
        except Exception:
            # Avoid crashing migrate/collectstatic if DB is not ready yet.
            pass
