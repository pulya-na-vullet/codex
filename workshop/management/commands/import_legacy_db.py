from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from workshop.models import AcceptanceAct, Client, Order, OrderLine, Service
from workshop.services import ensure_category_path
from workshop.utils import normalize_rf_phone

CATEGORY_MAP = {
    "Диагностика компьютера/ноутбука (вычитается из ремонта)": ("Диагностика", "Компьютеры и ноутбуки"),
    "Выездная диагностика (вычитается из ремонта)": ("Диагностика", "Выезд"),
    "Экспресс-диагностика (15 мин)": ("Диагностика", "Экспресс"),
    "Сборка ПК под ключ (подбор + установка)": ("Компьютеры", "Сборка и апгрейд"),
    "Апгрейд ПК (замена компонентов)": ("Компьютеры", "Сборка и апгрейд"),
    "Ремонт/замена блока питания": ("Компьютеры", "Ремонт"),
    "Замена материнской платы": ("Компьютеры", "Ремонт"),
    "Профилактика ПК (чистка + термопаста)": ("Компьютеры", "Чистка и охлаждение"),
    "Чистка ПК от пыли": ("Компьютеры", "Чистка и охлаждение"),
    "Замена термопасты (CPU/GPU)": ("Компьютеры", "Чистка и охлаждение"),
    "Продувка системного блока": ("Компьютеры", "Чистка и охлаждение"),
    "Ремонт видеокарты": ("Компьютеры", "Ремонт"),
    "Замена кулера/системы охлаждения": ("Компьютеры", "Чистка и охлаждение"),
    "Прошивка BIOS/UEFI": ("Компьютеры", "Прошивка"),
    "Восстановление после скачков напряжения": ("Компьютеры", "Ремонт"),
    "Чистка ноутбука + замена термопасты": ("Ноутбуки", "Чистка"),
    "Замена клавиатуры ноутбука": ("Ноутбуки", "Запчасти"),
    "Замена матрицы (экрана) ноутбука": ("Ноутбуки", "Запчасти"),
    "Замена разъема питания (гнезда)": ("Ноутбуки", "Ремонт"),
    "Восстановление после залития (химчистка)": ("Ноутбуки", "Ремонт"),
    "Ремонт материнской платы (BGA-пайка)": ("Ноутбуки", "Ремонт"),
    "Ремонт шлейфов и разъемов": ("Ноутбуки", "Ремонт"),
    "Замена аккумулятора ноутбука": ("Ноутбуки", "Запчасти"),
    "Ремонт петли экрана": ("Ноутбуки", "Ремонт"),
    "Замена HDD/SDD/M2": ("Ноутбуки", "Запчасти"),
    "Сборка/разборка ноутбука": ("Ноутбуки", "Сервис"),
    "Установка Windows 10/11 + драйверы + активация": ("Программное обеспечение", "ОС"),
    "Установка пакета программ (Office, браузеры и т.д.)": ("Программное обеспечение", "ПО"),
    "Удаление вирусов + лечение системы": ("Программное обеспечение", "Безопасность"),
    "Восстановление данных с HDD/SSD": ("Программное обеспечение", "Данные"),
    "Настройка Wi-Fi сети и роутера": ("Сети и ПО", "Сети"),
    "Резервное копирование данных": ("Программное обеспечение", "Данные"),
    "Оптимизация и настройка системы": ("Программное обеспечение", "Оптимизация"),
    "Выезд мастера + диагностика": ("Выездные услуги", "Диагностика"),
    "Установка ОС и ПО с выездом": ("Выездные услуги", "ПО"),
    "Чистка ПК/ноутбука с выездом": ("Выездные услуги", "Чистка"),
    "Замена клавиатуры с выездом": ("Выездные услуги", "Ремонт"),
    "Ремонт материнской платы (BGA-пайка) с выездом": ("Выездные услуги", "Ремонт"),
    "Настройка локальной сети": ("Сети и ПО", "Сети"),
    "Установка и настройка 1С": ("Сети и ПО", "ПО"),
    "3D-печать (до 50г)": ("3D-печать", "Печать"),
    "3D-печать (50-200г)": ("3D-печать", "Печать"),
    "3D-печать (срочная, 24ч)": ("3D-печать", "Печать"),
    "3D-моделирование (простая модель)": ("3D-печать", "Моделирование"),
    "3D-моделирование (сложная модель)": ("3D-печать", "Моделирование"),
    "Замена дисплея (без рамки)": ("Телефоны", "Дисплей"),
    "Замена дисплея (с рамкой)": ("Телефоны", "Дисплей"),
    "Замена защитного стекла (поклейка)": ("Телефоны", "Аксессуары"),
    "Замена аккумулятора телефона": ("Телефоны", "Запчасти"),
    "Ремонт кнопок/шлейфов телефона": ("Телефоны", "Ремонт"),
    "Восстановление телефона после воды": ("Телефоны", "Ремонт"),
    "Ремонт блока питания телевизора": ("Телевизоры", "Ремонт"),
    "Замена LED подсветки телевизора": ("Телевизоры", "Ремонт"),
    "Замена материнской платы телевизора": ("Телевизоры", "Ремонт"),
    "Ремонт матрицы телевизора": ("Телевизоры", "Ремонт"),
    "Ремонт принтера/МФУ": ("Оргтехника", "Принтеры"),
    "Настройка умного дома": ("Дополнительно", "Умный дом"),
    "Монтаж техники на стену": ("Дополнительно", "Монтаж"),
    "Срочность выполнения (до 24ч)": ("Дополнительно", "Срочность"),
}


def guess_category(name: str) -> tuple[str, ...]:
    if name in CATEGORY_MAP:
        return CATEGORY_MAP[name]
    name_l = name.lower()
    if "3d" in name_l:
        return ("3D-печать", "Прочее")
    if "телефон" in name_l or "дисплея" in name_l:
        return ("Телефоны", "Прочее")
    if "телевизор" in name_l:
        return ("Телевизоры", "Прочее")
    if "выезд" in name_l:
        return ("Выездные услуги", "Прочее")
    if "ноутбук" in name_l:
        return ("Ноутбуки", "Прочее")
    if any(x in name_l for x in ("windows", "вирус", "1с", "программ", "данн", "оптимиз", "wi-fi", "сети")):
        return ("Программное обеспечение", "Прочее")
    if any(x in name_l for x in ("пк", "видеокарт", "bios", "блок питания", "материнск", "термопаст")):
        return ("Компьютеры", "Прочее")
    if "диагност" in name_l:
        return ("Диагностика", "Прочее")
    return ("Дополнительно", "Прочее")


def parse_dt(value: str | None):
    if not value:
        return timezone.now()
    text = str(value).strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except ValueError:
            continue
    return timezone.now()


def table_cols(cur, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


class Command(BaseCommand):
    help = "Import legacy Flask/SQLite orders.db into normalized Django models"

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", default="orders.db")
        parser.add_argument("--clear", action="store_true", help="Clear existing workshop data first")
        parser.add_argument(
            "--only-if-empty",
            action="store_true",
            help="Skip import when clients or services already exist",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        source = Path(options["source"]).expanduser()
        if not source.exists():
            raise CommandError(f"File not found: {source}")

        if options["only_if_empty"] and (Client.objects.exists() or Service.objects.exists() or Order.objects.exists()):
            self.stdout.write("Django DB already has data — skip import")
            return

        if options["clear"]:
            OrderLine.objects.all().delete()
            AcceptanceAct.objects.all().delete()
            Order.objects.all().delete()
            Service.objects.all().delete()
            Client.objects.all().delete()

        conn = sqlite3.connect(str(source))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        stats = {"clients": 0, "services": 0, "orders": 0, "lines": 0}

        # Clients
        client_map: dict[int, Client] = {}
        if "clients" in {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            cols = table_cols(cur, "clients")
            comment_expr = "client_comment" if "client_comment" in cols else "''"
            for row in cur.execute(
                f"SELECT id, name, phone, {comment_expr} AS comment, created_date FROM clients"
            ):
                phone = normalize_rf_phone(row["phone"]) or str(row["phone"] or "").strip()
                if not phone:
                    continue
                client, created = Client.objects.get_or_create(
                    phone=phone,
                    defaults={
                        "name": row["name"] or phone,
                        "comment": row["comment"] or "",
                        "created_at": parse_dt(row["created_date"]),
                    },
                )
                if created:
                    stats["clients"] += 1
                client_map[int(row["id"])] = client

        # Services
        service_map: dict[int, Service] = {}
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "services_catalog" in tables:
            sc_cols = table_cols(cur, "services_catalog")
            has_cat_id = "category_id" in sc_cols
            cat_names = {}
            if "service_categories" in tables:
                for crow in cur.execute("SELECT id, name FROM service_categories"):
                    cat_names[int(crow["id"])] = crow["name"]
            for row in cur.execute("SELECT * FROM services_catalog"):
                name = row["name"]
                path = guess_category(name)
                if has_cat_id and row["category_id"] in cat_names:
                    # Prefer mapped nested path when available
                    path = CATEGORY_MAP.get(name) or path
                category = ensure_category_path(path)
                try:
                    price = Decimal(str(row["price"]))
                except (InvalidOperation, TypeError):
                    price = Decimal("0")
                service, created = Service.objects.update_or_create(
                    name=name,
                    defaults={
                        "price": price,
                        "category": category,
                        "is_active": bool(row["is_active"]) if "is_active" in sc_cols else True,
                        "created_at": parse_dt(row["created_date"]) if "created_date" in sc_cols else timezone.now(),
                    },
                )
                if created:
                    stats["services"] += 1
                service_map[int(row["id"])] = service

        # Orders
        order_map: dict[int, Order] = {}
        if "orders" in tables:
            ocols = table_cols(cur, "orders")
            for row in cur.execute("SELECT * FROM orders"):
                number = row["order_number"]
                client = client_map.get(int(row["client_id"])) if row["client_id"] else None
                defaults = {
                    "client": client,
                    "created_at": parse_dt(row["created_date"]),
                    "status": row["status"] if "status" in ocols else "active",
                    "device_type": row["device_type"] if "device_type" in ocols and row["device_type"] else "ПК",
                    "extra_periphery": row["extra_periphery"] if "extra_periphery" in ocols else "",
                    "technical_notes": row["technical_notes"] if "technical_notes" in ocols else "",
                    "discount_percent": Decimal(str(row["discount_percent"])) if "discount_percent" in ocols else Decimal("0"),
                    "subtotal_sum": Decimal(str(row["subtotal_sum"])) if "subtotal_sum" in ocols else Decimal(str(row["total_sum"] or 0)),
                    "total_sum": Decimal(str(row["total_sum"] or 0)),
                }
                order, created = Order.objects.update_or_create(order_number=number, defaults=defaults)
                if created:
                    stats["orders"] += 1
                order_map[int(row["id"])] = order

        # Lines: prefer order_service_lines, else order_services
        if "order_service_lines" in tables:
            for row in cur.execute("SELECT * FROM order_service_lines"):
                order = order_map.get(int(row["order_id"]))
                if not order:
                    continue
                service = service_map.get(int(row["service_id"])) if row["service_id"] else None
                name = row["service_name_snapshot"]
                if OrderLine.objects.filter(order=order, service_name=name, unit_price=row["unit_price"], quantity=row["quantity"]).exists():
                    continue
                OrderLine.objects.create(
                    order=order,
                    service=service,
                    service_name=name,
                    unit_price=Decimal(str(row["unit_price"])),
                    quantity=int(row["quantity"] or 1),
                )
                stats["lines"] += 1
        elif "order_services" in tables:
            for row in cur.execute("SELECT * FROM order_services"):
                order = order_map.get(int(row["order_id"]))
                if not order:
                    continue
                service = Service.objects.filter(name=row["service_name"]).first()
                OrderLine.objects.create(
                    order=order,
                    service=service,
                    service_name=row["service_name"],
                    unit_price=Decimal(str(row["price"])),
                    quantity=int(row["quantity"] or 1),
                )
                stats["lines"] += 1

        # Recalculate totals with loyalty
        for order in Order.objects.select_related("client"):
            order.recalculate_totals()

        conn.close()
        self.stdout.write(self.style.SUCCESS(f"Import complete: {stats}"))
