from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from workshop.models import Service
from workshop.services import ensure_category_path


DEFAULTS = [
    ("Диагностика (вычитается из ремонта)", Decimal("500"), ("Диагностика", "Компьютеры и ноутбуки")),
    ("Сборка ПК под ключ", Decimal("3000"), ("Компьютеры", "Сборка и апгрейд")),
    ("Апгрейд ПК", Decimal("1500"), ("Компьютеры", "Сборка и апгрейд")),
    ("Профилактика ПК (чистка + термопаста)", Decimal("2000"), ("Компьютеры", "Чистка и охлаждение")),
    ("Чистка ноутбука + термопаста", Decimal("2500"), ("Ноутбуки", "Чистка")),
    ("Замена матрицы ноутбука", Decimal("3000"), ("Ноутбуки", "Запчасти")),
    ("Установка Windows 10/11 + драйверы", Decimal("2500"), ("Программное обеспечение", "ОС")),
    ("Удаление вирусов", Decimal("2000"), ("Программное обеспечение", "Безопасность")),
    ("Выезд мастера + диагностика", Decimal("1500"), ("Выездные услуги", "Диагностика")),
    ("3D-печать (стандартная)", Decimal("500"), ("3D-печать", "Печать")),
]


class Command(BaseCommand):
    help = "Seed default services when catalog is empty"

    def handle(self, *args, **options):
        if Service.objects.exists():
            self.stdout.write("Catalog already has services — skip")
            return
        for name, price, path in DEFAULTS:
            cat = ensure_category_path(path)
            Service.objects.create(name=name, price=price, category=cat, is_active=True)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(DEFAULTS)} services"))
