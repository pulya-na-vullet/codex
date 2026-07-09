import os
import tempfile
import unittest

from database import Database


class DatabaseBusinessLogicTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_orders.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_seed_creates_active_period_and_services(self):
        self.assertIsNotNone(self.db.current_period_id)
        services = self.db.get_active_services()
        self.assertGreater(len(services), 0)
        prices = self.db.get_period_prices()
        self.assertGreater(len(prices), 0)

    def test_create_client_and_enforce_unique_phone(self):
        first = self.db.create_client("Иван", "+79990000001")
        second = self.db.create_client("Петр", "+79990000001")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_order_total_calculation(self):
        client_id = self.db.create_client("Анна", "+79990000002")
        self.assertIsNotNone(client_id)
        order_id, number = self.db.create_order(client_id)
        self.assertTrue(number.startswith("ORD-"))
        self.db.add_service_to_order(order_id, "Диагностика", 500, 2)
        self.db.add_service_to_order(order_id, "Чистка", 700, 1)
        total = self.db.update_order_total(order_id)
        self.assertEqual(total, 1700.0)
        order = self.db.get_order_by_id(order_id)
        self.assertEqual(float(order["total_sum"]), 1700.0)

    def test_delete_order_rolls_back_client_stats(self):
        client_id = self.db.create_client("Олег", "+79990000003")
        order_id, _ = self.db.create_order(client_id)
        self.db.add_service_to_order(order_id, "Услуга A", 1000, 1)
        total = self.db.update_order_total(order_id)
        self.db.update_client_stats(client_id, total, 1)
        client_before = self.db.find_client_by_name_phone("Олег", "+79990000003")
        self.assertEqual(int(client_before["id"]), client_id)
        self.db.delete_order(order_id)
        clients = self.db.search_clients("Олег")
        self.assertEqual(len(clients), 1)
        self.assertEqual(int(clients[0]["total_orders"]), 0)
        self.assertEqual(float(clients[0]["total_spent"]), 0.0)

    def test_create_period_from_prices_replaces_catalog(self):
        new_prices = [("Новая услуга 1", 1111.0), ("Новая услуга 2", 2222.0)]
        period_id = self.db.create_period_from_prices("Тестовый период", new_prices)
        self.assertEqual(period_id, self.db.current_period_id)
        period_prices = self.db.get_period_prices(period_id)
        self.assertEqual(len(period_prices), 2)
        names = {name for name, _ in period_prices}
        self.assertSetEqual(names, {"Новая услуга 1", "Новая услуга 2"})
        services = self.db.get_active_services()
        self.assertEqual(len(services), 2)


if __name__ == "__main__":
    unittest.main()
