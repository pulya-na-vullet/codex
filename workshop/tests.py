from __future__ import annotations

import os
import tempfile
from decimal import Decimal

from django.test import Client as HttpClient, TestCase, override_settings

from workshop.models import Client, Order, OrderLine, Service, loyalty_discount_percent
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
