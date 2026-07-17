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
        self.assertEqual(loyalty_discount_percent(3), Decimal("0"))
        self.assertEqual(loyalty_discount_percent(4), Decimal("5"))
        self.assertEqual(loyalty_discount_percent(7), Decimal("7"))
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

    def test_discount_on_fourth_order(self):
        last = None
        for _ in range(4):
            last = Order.objects.create(order_number=f"ORD-{Order.objects.count()+1:06d}", client=self.client_obj)
            OrderLine.objects.create(
                order=last,
                service=self.service,
                service_name=self.service.name,
                unit_price=self.service.price,
                quantity=1,
            )
            last.recalculate_totals()
        self.assertEqual(self.client_obj.total_orders, 4)
        self.assertEqual(last.discount_percent, Decimal("5"))
        self.assertEqual(last.total_sum, Decimal("950.00"))

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


@override_settings(WORKSHOP_USERNAME="ITM", WORKSHOP_PASSWORD="pass")
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
        self.assertContains(r, "Последние заказы")

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
        order = Order.objects.create(order_number="ORD-777777", client=client, total_sum=Decimal("500"))
        OrderLine.objects.create(order=order, service=service, service_name=service.name, unit_price=Decimal("500"), quantity=1)
        order.recalculate_totals()
        r = self.http.get("/debtors")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ORD-777777")
        r = self.http.post(f"/orders/{order.id}/payment", {"payment_method": "cash"})
        self.assertEqual(r.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.payment_method, PaymentMethod.CASH)
        self.assertIsNotNone(order.payment_at)
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
            created_at=debt_tracking_start() - timedelta(days=1),
        )
        new = Order.objects.create(
            order_number="ORD-NEW0001",
            client=client,
            total_sum=Decimal("300"),
            payment_method=PaymentMethod.UNPAID,
            created_at=debt_tracking_start() + timedelta(hours=1),
        )
        self.assertFalse(old.is_debtor)
        self.assertTrue(new.is_debtor)
        r = self.http.get("/debtors")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ORD-NEW0001")
        self.assertNotContains(r, "ORD-OLD0001")
        self.assertContains(r, "300,00")
        self.assertContains(r, "16.06.2026")

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
        r = self.http.get("/services")
        self.assertContains(r, "Неактивна")
        self.assertContains(r, "Включить")
