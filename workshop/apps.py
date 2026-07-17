from django.apps import AppConfig


class WorkshopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workshop"
    verbose_name = "ИТ-мастерская"

    def ready(self):
        # Start background print queue worker with the web process.
        try:
            from workshop.printing import start_print_worker

            start_print_worker()
        except Exception:
            # Avoid breaking migrate/checks if DB is not ready yet.
            pass
