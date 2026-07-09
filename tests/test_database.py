import os
import sqlite3
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
        first = self.db.create_client("Иван", "+7 999 000 00 01", "первый")
        second = self.db.create_client("Петр", "8 (999) 000-00-01")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        client = self.db.find_client_by_phone("+79990000001")
        self.assertIsNotNone(client)

    def test_create_client_rejects_invalid_phone(self):
        self.assertIsNone(self.db.create_client("Неверный", "12345"))

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
        self.db.update_order_total(order_id)
        client_before = self.db.get_client_by_id(client_id)
        self.assertEqual(int(client_before["total_orders"]), 1)
        self.assertEqual(float(client_before["total_spent"]), 1000.0)
        self.db.delete_order(order_id)
        client_after = self.db.get_client_by_id(client_id)
        self.assertEqual(int(client_after["total_orders"]), 0)
        self.assertEqual(float(client_after["total_spent"]), 0.0)

    def test_loyalty_discount_tiers(self):
        self.assertEqual(self.db.get_loyalty_discount_percent(3), 0.0)
        self.assertEqual(self.db.get_loyalty_discount_percent(4), 5.0)
        self.assertEqual(self.db.get_loyalty_discount_percent(7), 7.0)
        self.assertEqual(self.db.get_loyalty_discount_percent(10), 10.0)

        client_id = self.db.create_client("Постоянный", "+79990000088")
        # Create 4 orders so client becomes regular ( > 3 )
        last_order_id = None
        for _ in range(4):
            last_order_id, _ = self.db.create_order(client_id)
            self.db.add_service_to_order(last_order_id, "Услуга", 1000, 1)
            self.db.update_order_total(last_order_id)

        client = self.db.get_client_by_id(client_id)
        self.assertTrue(client["is_regular"])
        self.assertEqual(client["discount_percent"], 5.0)
        self.assertEqual(int(client["total_orders"]), 4)
        self.assertEqual(float(client["total_spent"]), 3950.0)  # 3*1000 + 950 with 5% on 4th order

        order = self.db.get_order_by_id(last_order_id)
        self.assertEqual(float(order["discount_percent"]), 5.0)
        self.assertEqual(float(order["subtotal_sum"]), 1000.0)
        self.assertEqual(float(order["total_sum"]), 950.0)

        history = self.db.get_client_orders(client_id)
        self.assertEqual(len(history), 4)

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

    def test_migrates_legacy_data_to_normalized_tables(self):
        self.db.close()
        legacy_db_path = os.path.join(self.temp_dir.name, "legacy.db")

        conn = sqlite3.connect(legacy_db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE services_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                price REAL NOT NULL,
                category TEXT NOT NULL DEFAULT 'Основные',
                created_date TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE price_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_date TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE period_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                price REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                created_date TEXT NOT NULL,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                client_id INTEGER,
                created_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                total_sum REAL NOT NULL DEFAULT 0,
                period_id INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE order_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        cur.execute(
            "INSERT INTO services_catalog (name, price, category, created_date, is_active) VALUES ('Диагностика', 500, 'Сервис', '01.01.2026 10:00', 1)"
        )
        cur.execute(
            "INSERT INTO price_periods (name, start_date, is_active, created_date) VALUES ('Период 1', '01.01.2026 10:00', 1, '01.01.2026 10:00')"
        )
        cur.execute("INSERT INTO period_prices (period_id, service_name, price) VALUES (1, 'Диагностика', 500)")
        cur.execute("INSERT INTO clients (name, phone, created_date) VALUES ('Клиент', '+79990000099', '01.01.2026 10:00')")
        cur.execute(
            "INSERT INTO orders (order_number, client_id, created_date, status, total_sum, period_id) VALUES ('ORD-000001', 1, '01.01.2026 10:00', 'active', 500, 1)"
        )
        cur.execute("INSERT INTO order_services (order_id, service_name, price, quantity) VALUES (1, 'Диагностика', 500, 1)")
        conn.commit()
        conn.close()

        migrated = Database(legacy_db_path)
        category_cnt = migrated.cursor.execute("SELECT COUNT(*) FROM service_categories").fetchone()[0]
        period_cnt = migrated.cursor.execute("SELECT COUNT(*) FROM period_service_prices").fetchone()[0]
        lines_cnt = migrated.cursor.execute("SELECT COUNT(*) FROM order_service_lines").fetchone()[0]
        self.assertGreaterEqual(category_cnt, 1)
        self.assertEqual(period_cnt, 1)
        self.assertEqual(lines_cnt, 1)
        migrated.close()

    def test_respects_env_db_path(self):
        self.db.close()
        env_db = os.path.join(self.temp_dir.name, "env_orders.db")
        os.environ["IT_MASTER_DB_PATH"] = env_db
        try:
            db = Database("orders.db")
            self.assertEqual(os.path.abspath(db.db_path), os.path.abspath(env_db))
            db.close()
        finally:
            os.environ.pop("IT_MASTER_DB_PATH", None)

    def test_import_legacy_database_merges_orders(self):
        source_db = os.path.join(self.temp_dir.name, "source.db")
        con = sqlite3.connect(source_db)
        cur = con.cursor()
        cur.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE, created_date TEXT, total_orders INTEGER DEFAULT 0, total_spent REAL DEFAULT 0)")
        cur.execute("CREATE TABLE services_catalog (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, price REAL, category TEXT, created_date TEXT, is_active INTEGER DEFAULT 1)")
        cur.execute("CREATE TABLE price_periods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, start_date TEXT, is_active INTEGER DEFAULT 1, created_date TEXT)")
        cur.execute("CREATE TABLE period_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, period_id INTEGER, service_name TEXT, price REAL)")
        cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_number TEXT UNIQUE, client_id INTEGER, created_date TEXT, status TEXT, total_sum REAL, period_id INTEGER)")
        cur.execute("CREATE TABLE order_services (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, service_name TEXT, price REAL, quantity INTEGER)")
        cur.execute("INSERT INTO clients (name, phone, created_date) VALUES ('Старый Клиент', '+79991112233', '01.01.2026 10:00')")
        cur.execute("INSERT INTO services_catalog (name, price, category, created_date, is_active) VALUES ('Старая Услуга', 1200, 'Ремонт', '01.01.2026 10:00', 1)")
        cur.execute("INSERT INTO price_periods (name, start_date, is_active, created_date) VALUES ('Старый период', '01.01.2026 10:00', 1, '01.01.2026 10:00')")
        cur.execute("INSERT INTO period_prices (period_id, service_name, price) VALUES (1, 'Старая Услуга', 1200)")
        cur.execute("INSERT INTO orders (order_number, client_id, created_date, status, total_sum, period_id) VALUES ('ORD-999999', 1, '01.01.2026 10:00', 'active', 1200, 1)")
        cur.execute("INSERT INTO order_services (order_id, service_name, price, quantity) VALUES (1, 'Старая Услуга', 1200, 1)")
        con.commit()
        con.close()

        result = self.db.import_legacy_database(source_db)
        self.assertEqual(result["clients"], 1)
        self.assertEqual(result["orders"], 1)
        self.assertGreaterEqual(result["order_lines"], 1)
        orders = self.db.get_all_orders()
        self.assertTrue(any(o["order_number"] == "ORD-999999" for o in orders))

    def test_category_tree_and_redistribution(self):
        tree = self.db.get_service_catalog_tree(active_only=True)
        self.assertGreater(len(tree), 0)
        # Nested categories exist
        self.db.cursor.execute("SELECT COUNT(*) AS cnt FROM service_categories WHERE parent_id IS NOT NULL")
        self.assertGreater(int(self.db.cursor.fetchone()["cnt"]), 0)
        services = self.db.get_active_services()
        self.assertTrue(any("/" in (s.get("category_path") or "") or " " in (s.get("category_path") or "") for s in services))

    def test_monthly_statistics_and_top_clients(self):
        client_id = self.db.create_client("Топ Клиент", "+79990000077")
        order_id, _ = self.db.create_order(client_id)
        self.db.add_service_to_order(order_id, "Тест услуга", 5000, 1)
        self.db.update_order_total(order_id)
        monthly = self.db.get_monthly_statistics(12)
        self.assertGreaterEqual(len(monthly), 1)
        top = self.db.get_top_clients_by_spent(10)
        self.assertGreaterEqual(len(top), 1)
        self.assertEqual(top[0]["name"], "Топ Клиент")

    def test_import_clients_skips_existing_phone(self):
        self.db.create_client("Существующий", "+79990000055", "old")
        result = self.db.import_clients_from_rows(
            [
                ("+7 999 000 00 55", "Дубликат", "skip"),
                ("89990000066", "Новый", "ok"),
                ("bad-phone", "Плохой", ""),
            ]
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["skipped_invalid"], 1)
        self.assertIsNotNone(self.db.find_client_by_phone("+79990000066"))


if __name__ == "__main__":
    unittest.main()
