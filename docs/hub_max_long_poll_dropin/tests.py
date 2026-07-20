import hashlib
import hmac
import json
import time
from django.contrib.auth.hashers import make_password

from django.test import TestCase
from rest_framework.test import APIClient

from .models import Designer, HubBrief, SiteNode


class HubApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.site = SiteNode.objects.create(
            site_id="site-1",
            name="Workshop 1",
            callback_base_url="https://example.test",
            site_token="token-123",
            site_secret="secret-456",
        )

    def _signed_headers(self, body: dict):
        timestamp = str(int(time.time()))
        raw_body = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        signature = hmac.new(
            self.site.site_secret.encode("utf-8"),
            f"{timestamp}\n{raw_body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "HTTP_AUTHORIZATION": f"Bearer {self.site.site_token}",
            "HTTP_X_SITE_ID": self.site.site_id,
            "HTTP_X_TIMESTAMP": timestamp,
            "HTTP_X_SIGNATURE": signature,
        }, raw_body

    def test_create_brief_with_hmac(self):
        payload = {
            "local_brief_id": 12,
            "brief_number": "3D-000001",
            "client_ref": "5",
            "model_url": "https://example.test/model",
            "description": "ТЗ",
            "agreed_price": "5000.00",
            "designer_share_amount": "3500.00",
            "site_share_amount": "1500.00",
            "has_stl": True,
            "screenshots_count": 2,
        }
        headers, raw_body = self._signed_headers(payload)
        response = self.client.generic(
            "POST",
            "/api/v1/briefs",
            data=raw_body,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], HubBrief.Status.QUEUED)
        self.assertTrue(HubBrief.objects.filter(site=self.site, local_brief_id=12).exists())

    def test_create_brief_invalid_signature(self):
        payload = {
            "local_brief_id": 12,
            "brief_number": "3D-000001",
            "client_ref": "5",
            "agreed_price": "5000.00",
            "designer_share_amount": "3500.00",
            "site_share_amount": "1500.00",
        }
        headers, raw_body = self._signed_headers(payload)
        headers["HTTP_X_SIGNATURE"] = "bad-signature"
        response = self.client.generic(
            "POST",
            "/api/v1/briefs",
            data=raw_body,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 401)


class MaxBotWorkflowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.site = SiteNode.objects.create(
            site_id="site-1",
            name="Workshop 1",
            callback_base_url="https://example.test",
            site_token="token-123",
            site_secret="secret-456",
        )
        self.brief = HubBrief.objects.create(
            public_id="brief-1",
            site=self.site,
            local_brief_id=88,
            brief_number="3D-000088",
            client_ref="cl-88",
            agreed_price="4000.00",
            designer_share_amount="2800.00",
            site_share_amount="1200.00",
            status=HubBrief.Status.QUEUED,
        )

    def _send_bot(self, text: str, user_id: str = "max-1") -> str:
        response = self.client.post(
            "/api/v1/max/webhook",
            {"user_id": user_id, "text": text},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data["reply"]

    def test_designer_registration_and_assign(self):
        self.assertIn("Введите ФИО", self._send_bot("Регистрация: Дизайнер"))
        self.assertIn("телефон СБП", self._send_bot("Иван Иванов"))
        self.assertIn("Опишите ваш опыт", self._send_bot("+79991112233"))
        self.assertIn("ссылку на портфолио", self._send_bot("2 года CAD"))
        self.assertIn("Регистрация завершена", self._send_bot("https://portfolio.example"))

        self.assertTrue(Designer.objects.filter(max_user_id="max-1").exists())
        queue_reply = self._send_bot("Очередь")
        self.assertIn("brief-1", queue_reply)

        take_reply = self._send_bot("Беру brief-1 2 дня")
        self.assertIn("назначена", take_reply.lower())
        self.brief.refresh_from_db()
        self.assertEqual(self.brief.status, HubBrief.Status.ASSIGNED)
        self.assertEqual(self.brief.designer.max_user_id, "max-1")


class DesignerWebQueueTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.site = SiteNode.objects.create(
            site_id="site-2",
            name="Workshop 2",
            callback_base_url="https://example.test",
            site_token="token-abc",
            site_secret="secret-def",
        )
        self.designer_1 = Designer.objects.create(
            max_user_id="mx-1",
            full_name="Анна Дизайнер",
            sbp_phone="+79990000001",
            experience_text="3 года",
            portfolio_url="https://portfolio1.example",
            web_login="anna",
            web_password_hash=make_password("pass-anna"),
        )
        self.designer_2 = Designer.objects.create(
            max_user_id="mx-2",
            full_name="Игорь Дизайнер",
            sbp_phone="+79990000002",
            experience_text="2 года",
            portfolio_url="https://portfolio2.example",
            web_login="igor",
            web_password_hash=make_password("pass-igor"),
        )
        self.brief = HubBrief.objects.create(
            public_id="brief-web-1",
            site=self.site,
            local_brief_id=55,
            brief_number="3D-000055",
            client_ref="client-55",
            agreed_price="6000.00",
            designer_share_amount="4200.00",
            site_share_amount="1800.00",
            status=HubBrief.Status.QUEUED,
        )

    def _login(self, login: str, password: str) -> str:
        response = self.client.post(
            "/api/v1/designer/auth/login",
            {"login": login, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data["token"]

    def test_queue_visible_and_claim_locked(self):
        token_1 = self._login("anna", "pass-anna")
        response = self.client.get(
            "/api/v1/designer/briefs",
            HTTP_AUTHORIZATION=f"Bearer {token_1}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["status"], HubBrief.Status.QUEUED)
        self.assertIsNone(response.data["results"][0]["designer_name"])

        claim_response_1 = self.client.post(
            "/api/v1/designer/briefs/brief-web-1/claim",
            {"eta": "48 часов"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_1}",
        )
        self.assertEqual(claim_response_1.status_code, 200)

        token_2 = self._login("igor", "pass-igor")
        claim_response_2 = self.client.post(
            "/api/v1/designer/briefs/brief-web-1/claim",
            {"eta": "24 часа"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_2}",
        )
        self.assertEqual(claim_response_2.status_code, 409)
        self.brief.refresh_from_db()
        self.assertEqual(self.brief.designer, self.designer_1)


class DesignerBootstrapPortalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.site = SiteNode.objects.create(
            site_id="site-3",
            name="Workshop 3",
            callback_base_url="https://example.test",
            site_token="token-z",
            site_secret="secret-z",
        )
        self.designer = Designer.objects.create(
            max_user_id="mx-3",
            full_name="Павел Дизайнер",
            sbp_phone="+79990000003",
            experience_text="4 года",
            portfolio_url="https://portfolio3.example",
            web_login="pavel",
            web_password_hash=make_password("pass-pavel"),
        )
        self.brief = HubBrief.objects.create(
            public_id="brief-web-portal",
            site=self.site,
            local_brief_id=56,
            brief_number="3D-000056",
            client_ref="client-56",
            agreed_price="6500.00",
            designer_share_amount="4550.00",
            site_share_amount="1950.00",
            status=HubBrief.Status.QUEUED,
        )

    def test_login_and_claim_via_bootstrap_pages(self):
        login_get = self.client.get("/designer/login")
        self.assertEqual(login_get.status_code, 200)

        login_post = self.client.post("/designer/login", {"login": "pavel", "password": "pass-pavel"})
        self.assertEqual(login_post.status_code, 302)
        self.assertEqual(login_post.url, "/designer/queue")

        queue = self.client.get("/designer/queue")
        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, "Свободные задачи")
        self.assertContains(queue, "brief-web-portal")

        claim = self.client.post("/designer/briefs/brief-web-portal/claim", {"eta": "3 дня"})
        self.assertEqual(claim.status_code, 302)
        self.brief.refresh_from_db()
        self.assertEqual(self.brief.designer, self.designer)
        self.assertEqual(self.brief.status, HubBrief.Status.ASSIGNED)


class MaxUpdateParsingTests(TestCase):
    def test_process_max_update_registration_without_sending(self):
        from .max_bot import process_max_update

        update = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 4242},
                "body": {"text": "Регистрация: Дизайнер"},
            },
        }
        reply = process_max_update(update, token="", welcome_text="")
        self.assertIsNotNone(reply)
        self.assertIn("ФИО", reply or "")

    def test_webhook_accepts_native_max_update(self):
        client = APIClient()
        response = client.post(
            "/api/v1/max/webhook",
            {
                "update_type": "message_created",
                "message": {
                    "sender": {"user_id": 777},
                    "body": {"text": "Регистрация: Дизайнер"},
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ФИО", response.data.get("reply", ""))
