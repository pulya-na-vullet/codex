from django.core.management.base import BaseCommand

from workshop.yandex_ai import run_daily_ai_report


class Command(BaseCommand):
    help = "Сформировать и отправить AI-отчёт за день администратору в Max"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Игнорировать флаг enabled / повторную отправку")

    def handle(self, *args, **options):
        result = run_daily_ai_report(force=bool(options["force"]))
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"OK ({result.get('source')}): {result.get('detail')}"))
            self.stdout.write(result.get("report", ""))
        else:
            self.stdout.write(self.style.WARNING(f"FAIL: {result.get('detail')}"))
