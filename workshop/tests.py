from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.test import Client as HttpClient, TestCase, override_settings
from django.utils import timezone

from workshop.models import Client, Order, OrderLine, PaymentMethod, Service, debt_tracking_start, loyalty_discount_percent
from workshop.pdf import build_order_pdf
from workshop.services import ensure_category_path
from workshop.utils import normalize_rf_phone


class UtilsTests(TestCase):
    def test_phone(self):
        self.assertEqual(normalize_rf_phone("8 (999) 000-00-01"), "+79990000001")
        self.assertIsNone(normalize_rf_phone("123"))

    def test_loyalty(self):
        self.assertEqual(loyalty_discount_percent(2), Decimal("0"))
        self.assertEqual(loyalty_discount_percent(3), Decimal("10"))
        self.assertEqual(loyalty_discount_percent(10), Decimal("10"))

    def test_money_filter(self):
        from workshop.templatetags.workshop_extras import money

        self.assertEqual(money(0), "0,00")
        self.assertEqual(money(Decimal("117.6")), "117,60")
        self.assertEqual(money("117.600000000000"), "117,60")


class OrderLogicTests(TestCase):
    def setUp(self):
        cat = ensure_category_path(("Диагностика", "Прочее"))
        self.service = Service.objects.create(name="Тест услуга", price=Decimal("1000"), category=cat)
        self.client_obj = Client.objects.create(name="Иван", phone="+79990000001")

    def test_discount_on_third_order_auto_10(self):
        last = None
        for _ in range(3):
            last = Order.objects.create(order_number=f"ORD-{Order.objects.count()+1:06d}", client=self.client_obj)
            OrderLine.objects.create(
                order=last,
                service=self.service,
                service_name=self.service.name,
                unit_price=self.service.price,
                quantity=1,
            )
            last.recalculate_totals()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.total_orders, 3)
        self.assertTrue(self.client_obj.is_regular)
        self.assertEqual(self.client_obj.discount_percent, Decimal("10.00"))
        self.assertEqual(last.discount_percent, Decimal("10.00"))
        self.assertEqual(last.total_sum, Decimal("900.00"))
        self.assertEqual(last.discount_amount, Decimal("100.00"))

    def test_manual_discount_not_overwritten(self):
        self.client_obj.set_discount_percent(5, manual=True)
        for _ in range(3):
            order = Order.objects.create(order_number=f"ORD-{Order.objects.count()+1:06d}", client=self.client_obj)
            OrderLine.objects.create(
                order=order,
                service=self.service,
                service_name=self.service.name,
                unit_price=self.service.price,
                quantity=1,
            )
            order.recalculate_totals()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.discount_percent, Decimal("5.00"))
        self.assertTrue(self.client_obj.discount_manual)

    def test_pdf_wraps_multiline_notes(self):
        order = Order.objects.create(
            order_number="ORD-000099",
            client=self.client_obj,
            technical_notes="Строка1\nСтрока2\nОчень длинная строка " + ("слово " * 40),
        )
        OrderLine.objects.create(
            order=order,
            service=self.service,
            service_name=self.service.name,
            unit_price=Decimal("100"),
            quantity=1,
        )
        order.recalculate_totals()
        pdf = build_order_pdf(order, list(order.lines.all()))
        self.assertGreater(len(pdf), 500)
        self.assertTrue(pdf.startswith(b"%PDF"))
        from workshop.pdf import MAX_MAILING_CONSENT, build_acceptance_act_pdf
        from workshop.models import AcceptanceAct, DeviceType

        self.assertIn("мессенджере Max", MAX_MAILING_CONSENT)
        act = AcceptanceAct.objects.create(
            act_number="ACT-PDF01",
            client=self.client_obj,
            device_type=DeviceType.PC,
            declared_defect="Тест",
        )
        act_pdf = build_acceptance_act_pdf(act)
        self.assertTrue(act_pdf.startswith(b"%PDF"))


@override_settings(WORKSHOP_USERNAME="ITM", WORKSHOP_PASSWORD="pass", PRINT_WORKER_ENABLED=False)
class AuthAndPagesTests(TestCase):
    def setUp(self):
        self.http = HttpClient()

    def test_login_required(self):
        r = self.http.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.url)

    def test_login_and_dashboard(self):
        r = self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        self.assertEqual(r.status_code, 302)
        r = self.http.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Сейчас в работе")
        self.assertContains(r, "Задач в работе")
        self.assertContains(r, "Диагностика в работе")
        self.assertContains(r, "Нужно позвонить")
        r = self.http.get("/work-queue")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'nav-page-btn is-active')
        self.assertContains(r, 'href="/work-queue">В работе</a>')
        # Активна только «В работе», не «Заказы».
        self.assertRegex(
            r.content.decode(),
            r'nav-page-btn is-active" href="/work-queue">В работе</a>',
        )
        self.assertNotRegex(
            r.content.decode(),
            r'nav-page-btn is-active" href="/orders">Заказы</a>',
        )
        r = self.http.get("/orders")
        self.assertRegex(
            r.content.decode(),
            r'nav-page-btn is-active" href="/orders">Заказы</a>',
        )

    def test_delete_client_and_service(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        cat = ensure_category_path(("Тест",))
        service = Service.objects.create(name="Удаляемая", price=Decimal("10"), category=cat)
        client = Client.objects.create(name="Удал", phone="+79991112233")
        r = self.http.post(f"/services/{service.id}/delete")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Service.objects.filter(pk=service.id).exists())
        r = self.http.post(f"/clients/{client.id}/delete")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Client.objects.filter(pk=client.id).exists())

    def test_payment_and_debtors(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        cat = ensure_category_path(("Тест",))
        service = Service.objects.create(name="Оплата тест", price=Decimal("500"), category=cat)
        client = Client.objects.create(name="Должник", phone="+79993334455")
        order = Order.objects.create(
            order_number="ORD-777777",
            client=client,
            total_sum=Decimal("500"),
            status="done",
            closed_at=timezone.now() - timedelta(days=2),
        )
        OrderLine.objects.create(order=order, service=service, service_name=service.name, unit_price=Decimal("500"), quantity=1)
        order.recalculate_totals()
        order.status = "done"
        order.closed_at = timezone.now() - timedelta(days=2)
        order.save(update_fields=["status", "closed_at", "total_sum"])
        self.assertTrue(order.is_debtor)
        r = self.http.get("/debtors")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ORD-777777")
        r = self.http.post(f"/orders/{order.id}/payment", {"payment_method": "cash"})
        self.assertEqual(r.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.payment_method, PaymentMethod.CASH)
        self.assertIsNotNone(order.payment_at)
        self.assertFalse(order.is_debtor)
        r = self.http.get("/debtors")
        self.assertNotContains(r, "ORD-777777")
        r = self.http.post(f"/orders/{order.id}/mytax", {"mytax_issued": "1"})
        order.refresh_from_db()
        self.assertTrue(order.mytax_issued)
        self.assertIsNotNone(order.mytax_at)
        r = self.http.get("/audit-log")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "order_payment")

    def test_debtors_ignore_orders_before_cutoff(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        client = Client.objects.create(name="Старый", phone="+79995556677")
        old = Order.objects.create(
            order_number="ORD-OLD0001",
            client=client,
            total_sum=Decimal("900"),
            payment_method=PaymentMethod.UNPAID,
            status="done",
            created_at=debt_tracking_start() - timedelta(days=1),
            closed_at=timezone.now() - timedelta(days=2),
        )
        new = Order.objects.create(
            order_number="ORD-NEW0001",
            client=client,
            total_sum=Decimal("300"),
            payment_method=PaymentMethod.UNPAID,
            status="done",
            created_at=debt_tracking_start() + timedelta(hours=1),
            closed_at=timezone.now() - timedelta(days=2),
        )
        in_progress = Order.objects.create(
            order_number="ORD-PROG001",
            client=client,
            total_sum=Decimal("400"),
            payment_method=PaymentMethod.UNPAID,
            status="active",
            created_at=debt_tracking_start() + timedelta(hours=2),
        )
        just_closed = Order.objects.create(
            order_number="ORD-FRESH01",
            client=client,
            total_sum=Decimal("200"),
            payment_method=PaymentMethod.UNPAID,
            status="done",
            created_at=debt_tracking_start() + timedelta(hours=3),
            closed_at=timezone.now() - timedelta(hours=12),
        )
        legacy_done = Order.objects.create(
            order_number="ORD-LEGACY1",
            client=client,
            total_sum=Decimal("700"),
            payment_method=PaymentMethod.UNPAID,
            status="done",
            created_at=debt_tracking_start() + timedelta(days=1),
            closed_at=None,
        )
        self.assertFalse(old.is_debtor)
        self.assertTrue(new.is_debtor)
        self.assertFalse(in_progress.is_debtor)
        self.assertFalse(just_closed.is_debtor)
        self.assertTrue(legacy_done.is_debtor)
        r = self.http.get("/debtors")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ORD-NEW0001")
        self.assertContains(r, "ORD-LEGACY1")
        self.assertNotContains(r, "ORD-OLD0001")
        self.assertNotContains(r, "ORD-PROG001")
        self.assertNotContains(r, "ORD-FRESH01")
        self.assertContains(r, "10.07.2026")

    def test_orders_list_shows_payment_and_mytax_badges(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        client = Client.objects.create(name="Клиент", phone="+79990001122")
        unpaid = Order.objects.create(order_number="ORD-UNPAID1", client=client, total_sum=Decimal("100"))
        paid = Order.objects.create(
            order_number="ORD-PAID001",
            client=client,
            total_sum=Decimal("200"),
            payment_method=PaymentMethod.CASH,
            mytax_issued=True,
        )
        r = self.http.get("/orders")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, unpaid.order_number)
        self.assertContains(r, paid.order_number)
        self.assertContains(r, "Не оплачен")
        self.assertContains(r, "Наличные")
        self.assertContains(r, "Чек выдан")
        self.assertContains(r, "Нет чека")
        self.assertContains(r, "Выгрузить Excel")
        r = self.http.get("/orders/export.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            r["Content-Type"],
        )
        self.assertIn("orders +", r["Content-Disposition"])
        self.assertTrue(r.content[:2] == b"PK")  # zip/xlsx

    def test_audit_log_export(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        from workshop.models import AuditLog

        AuditLog.objects.create(username="ITM", action="test_action", details="demo")
        r = self.http.get("/audit-log")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Выгрузить .log")
        self.assertContains(r, "panel-scroll")
        r = self.http.get("/audit-log/export.log")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/plain", r["Content-Type"])
        self.assertIn("log+", r["Content-Disposition"])
        self.assertIn(b"test_action", r.content)

    def test_service_toggle_active_hides_from_order_catalog(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        cat = ensure_category_path(("Каталог",))
        service = Service.objects.create(name="Скрываемая услуга", price=Decimal("50"), category=cat, is_active=True)
        order = Order.objects.create(order_number="ORD-HIDE001")
        r = self.http.get(f"/orders/{order.id}")
        self.assertContains(r, "Скрываемая услуга")
        r = self.http.post(f"/services/{service.id}/toggle-active", follow=True)
        self.assertEqual(r.status_code, 200)
        service.refresh_from_db()
        self.assertFalse(service.is_active)
        r = self.http.get(f"/orders/{order.id}")
        self.assertContains(r, "Каталог пуст")
        self.assertNotContains(r, "Скрываемая услуга")
        r = self.http.post(f"/orders/{order.id}/add-service", {"service_name": "Скрываемая услуга", "quantity": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(order.lines.count(), 0)
        r = self.http.get("/services?status=all")
        self.assertContains(r, "Неактивна")
        self.assertContains(r, "Включить")

    def test_client_comment_edit_shows_in_list(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        client = Client.objects.create(name="Коммент", phone="+79990009988", comment="")
        r = self.http.post(f"/clients/{client.id}", {"comment": "VIP, звонить после 18"})
        self.assertEqual(r.status_code, 302)
        client.refresh_from_db()
        self.assertEqual(client.comment, "VIP, звонить после 18")
        r = self.http.get("/clients")
        self.assertContains(r, "VIP, звонить после 18")

    def test_services_status_filter(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        cat = ensure_category_path(("Фильтр",))
        Service.objects.create(name="Активная услуга", price=Decimal("10"), category=cat, is_active=True)
        Service.objects.create(name="Старая услуга", price=Decimal("20"), category=cat, is_active=False)
        r = self.http.get("/services")
        self.assertContains(r, "Активная услуга")
        self.assertNotContains(r, "Старая услуга")
        self.assertContains(r, "Печать прайса")
        r = self.http.get("/services?status=all")
        self.assertContains(r, "Активная услуга")
        self.assertContains(r, "Старая услуга")
        r = self.http.get("/services?status=active")
        self.assertContains(r, "Активная услуга")
        self.assertNotContains(r, "Старая услуга")
        r = self.http.get("/services?status=inactive")
        self.assertContains(r, "Старая услуга")
        self.assertNotContains(r, "Активная услуга")
        r = self.http.get("/services/print")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Прайс-лист")
        self.assertContains(r, "Дата публикации прайс-листа")
        self.assertContains(r, "Утверждаю")
        self.assertContains(r, "Активная услуга")
        self.assertNotContains(r, "Старая услуга")
        self.assertNotContains(r, "Подпись ответственного")
        self.assertContains(r, 'class="columns"')
        r = self.http.get("/")
        self.assertNotContains(r, ">Главная</a>")

    def test_statistics_defaults_to_week(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        client = Client.objects.create(name="Стат", phone="+79991110000")
        Order.objects.create(order_number="ORD-STAT01", client=client, total_sum=Decimal("150"))
        r = self.http.get("/statistics")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Выручка за неделю")
        self.assertContains(r, "Обратившиеся клиенты")
        self.assertContains(r, "ORD-STAT01")
        r = self.http.get("/statistics?period=month")
        self.assertContains(r, "Календарь обращений")
        self.assertContains(r, "Топ-10 клиентов за месяц")
        r = self.http.get("/statistics?period=year")
        self.assertContains(r, "Сравнение год к году")
        self.assertContains(r, "Разбивка по месяцам")

    def test_work_queue_and_status(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        from workshop.models import AcceptanceAct, AcceptanceActStatus, DeviceType

        client = Client.objects.create(name="Очередь", phone="+79998887766")
        order = Order.objects.create(
            order_number="ORD-WORK01",
            client=client,
            total_sum=Decimal("500"),
            status="active",
        )
        act = AcceptanceAct.objects.create(
            act_number="ACT-WORK01",
            client=client,
            device_type=DeviceType.PC,
            declared_defect="Не включается",
            status=AcceptanceActStatus.DIAGNOSTICS,
        )
        r = self.http.get("/work-queue")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ORD-WORK01")
        self.assertContains(r, "ACT-WORK01")
        self.assertContains(r, "Диагностика идёт")
        # Работа выполнена → «позвонить», не сразу «Выполнена»
        r = self.http.post(f"/orders/{order.id}/status", {"status": "done", "next": "/work-queue"}, follow=True)
        self.assertEqual(r.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "ready_call")
        self.assertContains(r, "Позвонить клиенту")
        self.assertContains(r, "ORD-WORK01")
        r = self.http.post(f"/orders/{order.id}/mark-called", {"next": "/work-queue"}, follow=True)
        self.assertEqual(r.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "done")
        self.assertIsNotNone(order.client_called_at)

        r = self.http.post(
            f"/acceptance/{act.id}/status",
            {"status": "diagnostics_done", "next": "/work-queue"},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        act.refresh_from_db()
        self.assertEqual(act.status, "diagnostics_done")
        self.assertContains(r, "ACT-WORK01")
        r = self.http.post(f"/acceptance/{act.id}/mark-called", {"next": "/work-queue"}, follow=True)
        act.refresh_from_db()
        self.assertEqual(act.status, "done")
        self.assertIsNotNone(act.client_called_at)
        self.assertContains(r, "Нет заказ-нарядов в работе")
        self.assertContains(r, "Нет актов на диагностике")

        r = self.http.get("/statistics")
        self.assertContains(r, "Оплачено")
        self.assertContains(r, "Долги")
        self.assertContains(r, "оплачено + долги + в работе")

    def test_max_status_notifications_for_linked_client(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        from workshop.models import AcceptanceAct, AcceptanceActStatus, DeviceType, SmsKind, SmsLog, SmsSettings

        cfg = SmsSettings.get_solo()
        cfg.enabled = True
        cfg.provider = "log"
        cfg.save()
        client = Client.objects.create(name="Уведомления", phone="+79990001100", max_user_id="555001")
        order = Order.objects.create(
            order_number="ORD-NOTIFY1",
            client=client,
            total_sum=Decimal("700"),
            status="ready_call",
        )
        act = AcceptanceAct.objects.create(
            act_number="ACT-NOTIFY1",
            client=client,
            device_type=DeviceType.PC,
            declared_defect="Тест",
            status=AcceptanceActStatus.DIAGNOSTICS,
        )
        r = self.http.post(f"/orders/{order.id}/mark-called", {"next": "/work-queue"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            SmsLog.objects.filter(kind=SmsKind.SYSTEM, client=client, success=True, text__icontains="ORD-NOTIFY1").exists()
        )
        r = self.http.post(
            f"/acceptance/{act.id}/status",
            {"status": "diagnostics_done", "next": "/work-queue"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            SmsLog.objects.filter(kind=SmsKind.SYSTEM, client=client, success=True, text__icontains="ACT-NOTIFY1").exists()
        )

    def test_sms_admin_and_debt_send(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        from workshop.models import SmsLog, SmsSettings

        cfg = SmsSettings.get_solo()
        cfg.enabled = True
        cfg.marketing_enabled = True
        cfg.provider = "log"
        cfg.bot_link = "https://max.ru/se13602985_bot"
        cfg.save()
        client = Client.objects.create(name="Max Клиент", phone="+79991234567", max_user_id="12345")
        order = Order.objects.create(
            order_number="ORD-MAX0001",
            client=client,
            total_sum=Decimal("1500"),
            status="done",
            closed_at=timezone.now() - timedelta(days=2),
        )
        r = self.http.get("/admin-panel")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Канал Max")
        self.assertContains(r, "Яндекс ИИ")
        r = self.http.post(
            "/admin-panel",
            {
                "section": "ai",
                "ai_enabled": "1",
                "api_key": "",
                "folder_id": "b1gtest",
                "model_name": "yandexgpt-lite",
                "admin_phone": "+79991234567",
                "admin_max_user_id": "12345",
                "report_time_msk": "23:59",
            },
        )
        self.assertEqual(r.status_code, 302)
        from workshop.models import YandexAiSettings
        from workshop.yandex_ai import (
            build_fallback_report,
            collect_day_facts,
            generate_day_report,
            run_daily_ai_report,
            should_send_daily_report,
        )

        ai = YandexAiSettings.get_solo()
        self.assertTrue(ai.enabled)
        self.assertEqual(ai.folder_id, "b1gtest")
        self.assertEqual(ai.report_hour_msk, 23)
        self.assertEqual(ai.report_minute_msk, 59)
        ai.report_hour_msk = 20
        ai.report_minute_msk = 15
        ai.save(update_fields=["report_hour_msk", "report_minute_msk"])
        r = self.http.get("/admin-panel")
        self.assertContains(r, 'name="report_time_msk"')
        self.assertContains(r, 'value="20:15"')
        self.assertContains(r, "Статус планировщика")
        self.assertContains(r, "Сейчас на сервере (МСК)")
        facts = collect_day_facts()
        report = build_fallback_report(facts)
        self.assertIn("День:", report)
        self.assertIn("Выручка:", report)
        self.assertIn("Аномалии работы:", report)
        text, source = generate_day_report(use_ai=False)
        self.assertEqual(source, "fallback")
        self.assertIn("День:", text)
        from datetime import datetime
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from django.test import override_settings

        from workshop.yandex_ai import ensure_due_ai_report

        msk = ZoneInfo("Europe/Moscow")
        before = datetime(2026, 7, 19, 20, 14, tzinfo=msk)
        after = datetime(2026, 7, 19, 20, 15, tzinfo=msk)
        late = datetime(2026, 7, 19, 22, 0, tzinfo=msk)
        self.assertFalse(should_send_daily_report(ai, before))
        self.assertTrue(should_send_daily_report(ai, after))
        self.assertTrue(should_send_daily_report(ai, late))
        ai.last_report_date = after.date()
        ai.save(update_fields=["last_report_date"])
        self.assertFalse(should_send_daily_report(ai, late))
        # Сброс даты — ensure_due должен отправить (мок Max).
        ai.last_report_date = None
        ai.save(update_fields=["last_report_date"])
        with override_settings(YANDEX_AI_SCHEDULER=True):
            with patch("workshop.yandex_ai.now_msk", return_value=late):
                with patch("workshop.yandex_ai.send_report_to_admin", return_value=(True, "sent")):
                    result = ensure_due_ai_report(force_check=True, min_interval_sec=0)
        self.assertIsNotNone(result)
        self.assertTrue(result.get("ok"))
        ai.refresh_from_db()
        self.assertIsNotNone(ai.last_report_date)
        # Ручная кнопка не должна блокировать расписание на день.
        ai.last_report_date = None
        ai.save(update_fields=["last_report_date"])
        with patch("workshop.yandex_ai.send_report_to_admin", return_value=(True, "sent")):
            manual = run_daily_ai_report(force=True)
        self.assertTrue(manual.get("ok"))
        ai.refresh_from_db()
        self.assertIsNone(ai.last_report_date)
        r = self.http.post(f"/debtors/{order.id}/sms")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(SmsLog.objects.filter(kind="debt", success=True).exists())
        r = self.http.get("/marketing")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Очередь последних рассылок")
        # По умолчанию сортировка по Max (с Max сверху).
        self.assertContains(r, "Max ↓")
        other = Client.objects.create(
            name="Маркет",
            phone="+79997654321",
            allow_marketing_sms=True,
            max_user_id="999",
        )
        second = Client.objects.create(
            name="Маркет2",
            phone="+79997654322",
            allow_marketing_sms=True,
            max_user_id="998",
        )
        # Один адресат — обычная строка, без «Масс-рассылка».
        r = self.http.post("/marketing", {"text": "Привет, {name}!", "client_ids": [str(other.id)]})
        self.assertEqual(r.status_code, 302)
        r = self.http.get("/marketing")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Маркет")
        self.assertContains(r, "Привет, Маркет!")
        self.assertNotContains(r, "Масс-рассылка")

        r = self.http.post(
            "/marketing",
            {"text": "Привет, {name}!", "client_ids": [str(other.id), str(second.id)]},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SmsLog.objects.filter(kind="marketing", success=True).count(), 3)
        from workshop.models import MarketingBlast

        blast = MarketingBlast.objects.latest("id")
        self.assertEqual(blast.logs.count(), 2)
        self.assertEqual(blast.template_text, "Привет, {name}!")
        r = self.http.get("/marketing?q=Маркет&sort=max")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Очередь последних рассылок")
        self.assertContains(r, "Масс-рассылка")
        self.assertContains(r, "клиентов: 2")
        self.assertContains(r, "Текст")
        self.assertContains(r, "Кому")
        self.assertContains(r, "Маркет")
        self.assertContains(r, "Маркет2")
        self.assertContains(r, "QR-код")
        # Одна строка масс-рассылки + одна одиночная.
        self.assertEqual(r.content.decode().count("Масс-рассылка"), 1)
        r = self.http.get("/marketing/bot-qr.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertTrue(r.content[:8] == b"\x89PNG\r\n\x1a\n")
        r = self.http.post(f"/marketing/blasts/{blast.id}/delete")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(MarketingBlast.objects.filter(pk=blast.id).exists())
        self.assertEqual(SmsLog.objects.filter(kind="marketing").count(), 1)
        r = self.http.get("/max/webhook")
        self.assertEqual(r.status_code, 200)

    def test_max_webhook_links_client_by_phone(self):
        from unittest.mock import patch

        from workshop.models import SmsSettings

        cfg = SmsSettings.get_solo()
        cfg.enabled = True
        cfg.provider = "max"
        cfg.bot_token = "test-token"
        cfg.save()
        client = Client.objects.create(name="Привязка", phone="+79991112233")
        payload = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 777001},
                "body": {"text": "Мой номер +7 (999) 111-22-33"},
            },
        }
        with patch("workshop.messaging.send_max_message") as mock_send:
            mock_send.return_value = {"message": {"body": {"mid": "mid1"}}}
            r = self.http.post(
                "/max/webhook",
                data=__import__("json").dumps(payload),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        client.refresh_from_db()
        self.assertEqual(client.max_user_id, "777001")
        mock_send.assert_called()

    def test_print_actions_are_logged(self):
        self.http.post("/login", {"username": "ITM", "password": "pass", "next": "/"})
        client = Client.objects.create(name="Печать", phone="+79992223344")
        order = Order.objects.create(order_number="ORD-PRINT1", client=client, total_sum=Decimal("100"))
        from workshop.models import AcceptanceAct, AuditLog, DeviceType, PrintJob, PrintJobStatus

        act = AcceptanceAct.objects.create(
            act_number="ACT-000001",
            client=client,
            declared_defect="Не включается",
            device_type=DeviceType.PC,
        )
        r = self.http.get(f"/orders/{order.id}/print")
        self.assertEqual(r.status_code, 200)
        r = self.http.get(f"/acceptance/{act.id}/print")
        self.assertEqual(r.status_code, 200)
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertIn("order_print_view", actions)
        self.assertIn("acceptance_print_view", actions)

        from unittest.mock import patch

        with patch("workshop.printing._submit_pdf_and_wait"):
            r = self.http.post(f"/orders/{order.id}/print-direct")
            self.assertEqual(r.status_code, 302)
            r = self.http.post(f"/acceptance/{act.id}/print-direct")
            self.assertEqual(r.status_code, 302)

        self.assertEqual(PrintJob.objects.count(), 4)  # 2 docs x 2 copies
        self.assertEqual(PrintJob.objects.filter(doc_type="order").count(), 2)
        self.assertEqual(PrintJob.objects.filter(doc_type="acceptance").count(), 2)
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertIn("order_print_queued", actions)
        self.assertIn("acceptance_print_queued", actions)

        # Process queue synchronously for the test.
        from workshop.printing import _claim_next_job, _process_job

        with patch("workshop.printing._submit_pdf_and_wait") as mock_submit:
            processed = 0
            while True:
                job = _claim_next_job()
                if not job:
                    break
                _process_job(job)
                processed += 1
            self.assertEqual(processed, 4)
            self.assertEqual(mock_submit.call_count, 4)
        self.assertEqual(PrintJob.objects.filter(status=PrintJobStatus.DONE).count(), 4)
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertIn("order_print_done", actions)
        self.assertIn("acceptance_print_done", actions)
