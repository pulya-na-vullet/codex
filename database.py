import datetime
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ServiceItem:
    service_id: int | None
    name: str
    price: float
    quantity: int


class Database:
    def __init__(self, db_name: str = "orders.db"):
        self.db_path = self._resolve_db_path(db_name)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.current_period_id: int | None = None
        self.create_tables()
        self.migrate_database()
        self.seed_data()
        self.load_current_period()

    def _now(self) -> str:
        return datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    def _resolve_db_path(self, db_name: str) -> str:
        env_db_path = os.getenv("IT_MASTER_DB_PATH")
        if env_db_path:
            return str(Path(env_db_path).expanduser().resolve())

        path = Path(db_name).expanduser()
        if path.is_absolute():
            return str(path)

        cwd_candidate = (Path.cwd() / path).resolve()
        repo_candidate = (Path(__file__).resolve().parent / path).resolve()

        if cwd_candidate.exists() and repo_candidate.exists():
            cwd_size = cwd_candidate.stat().st_size
            repo_size = repo_candidate.stat().st_size
            return str(cwd_candidate if cwd_size >= repo_size else repo_candidate)
        if cwd_candidate.exists():
            return str(cwd_candidate)
        if repo_candidate.exists():
            return str(repo_candidate)
        return str(repo_candidate)

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        self.cursor.execute(f"PRAGMA table_info({table_name})")
        return any(row["name"] == column_name for row in self.cursor.fetchall())

    def _normalize_phone(self, phone: str) -> str | None:
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) == 11 and digits[0] in ("7", "8"):
            digits = "7" + digits[1:]
        elif len(digits) == 10 and digits[0] == "9":
            digits = "7" + digits
        else:
            return None
        return f"+{digits}"

    def _ensure_category(self, category_name: str) -> int:
        normalized = (category_name or "Основные").strip() or "Основные"
        self.cursor.execute("SELECT id FROM service_categories WHERE name = ?", (normalized,))
        row = self.cursor.fetchone()
        if row:
            return int(row["id"])
        self.cursor.execute("INSERT INTO service_categories (name) VALUES (?)", (normalized,))
        return int(self.cursor.lastrowid)

    def create_tables(self) -> None:
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                client_comment TEXT NOT NULL DEFAULT '',
                created_date TEXT NOT NULL,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS service_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS services_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                price REAL NOT NULL,
                category_id INTEGER NOT NULL,
                created_date TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (category_id) REFERENCES service_categories(id)
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS price_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_date TEXT NOT NULL
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS period_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                price REAL NOT NULL,
                FOREIGN KEY (period_id) REFERENCES price_periods(id) ON DELETE CASCADE
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS period_service_prices (
                period_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (period_id, service_id),
                FOREIGN KEY (period_id) REFERENCES price_periods(id) ON DELETE CASCADE,
                FOREIGN KEY (service_id) REFERENCES services_catalog(id) ON DELETE CASCADE
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                client_id INTEGER,
                created_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                total_sum REAL NOT NULL DEFAULT 0,
                device_type TEXT NOT NULL DEFAULT 'ПК',
                extra_periphery TEXT NOT NULL DEFAULT '',
                technical_notes TEXT NOT NULL DEFAULT '',
                period_id INTEGER,
                FOREIGN KEY (client_id) REFERENCES clients(id),
                FOREIGN KEY (period_id) REFERENCES price_periods(id)
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_service_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                service_id INTEGER,
                service_name_snapshot TEXT NOT NULL,
                unit_price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY (service_id) REFERENCES services_catalog(id) ON DELETE SET NULL
            )
            """
        )
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_client_id ON orders(client_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_period_prices_period ON period_prices(period_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_period_service_prices_period ON period_service_prices(period_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_service_lines_order ON order_service_lines(order_id)")
        self.conn.commit()

    def migrate_database(self) -> None:
        if not self._column_exists("orders", "device_type"):
            self.cursor.execute("ALTER TABLE orders ADD COLUMN device_type TEXT NOT NULL DEFAULT 'ПК'")
        if not self._column_exists("orders", "extra_periphery"):
            self.cursor.execute("ALTER TABLE orders ADD COLUMN extra_periphery TEXT NOT NULL DEFAULT ''")
        if not self._column_exists("orders", "technical_notes"):
            self.cursor.execute("ALTER TABLE orders ADD COLUMN technical_notes TEXT NOT NULL DEFAULT ''")

        if not self._column_exists("clients", "client_comment"):
            self.cursor.execute("ALTER TABLE clients ADD COLUMN client_comment TEXT NOT NULL DEFAULT ''")

        has_category_text = self._column_exists("services_catalog", "category")
        has_category_id = self._column_exists("services_catalog", "category_id")

        if has_category_text or not has_category_id:
            self.conn.execute("PRAGMA foreign_keys = OFF")
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS services_catalog_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    price REAL NOT NULL,
                    category_id INTEGER NOT NULL,
                    created_date TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (category_id) REFERENCES service_categories(id)
                )
                """
            )

            if has_category_text:
                self.cursor.execute(
                    """
                    SELECT id, name, price,
                           COALESCE(NULLIF(TRIM(category), ''), 'Основные') AS category_name,
                           created_date, is_active
                    FROM services_catalog
                    """
                )
            else:
                self.cursor.execute(
                    """
                    SELECT sc.id, sc.name, sc.price, COALESCE(cat.name, 'Основные') AS category_name,
                           sc.created_date, sc.is_active
                    FROM services_catalog sc
                    LEFT JOIN service_categories cat ON cat.id = sc.category_id
                    """
                )

            rows = self.cursor.fetchall()
            for row in rows:
                category_id = self._ensure_category(row["category_name"])
                self.cursor.execute(
                    """
                    INSERT OR REPLACE INTO services_catalog_new
                    (id, name, price, category_id, created_date, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (row["id"], row["name"], row["price"], category_id, row["created_date"], row["is_active"]),
                )

            self.cursor.execute("DROP TABLE services_catalog")
            self.cursor.execute("ALTER TABLE services_catalog_new RENAME TO services_catalog")
            self.conn.execute("PRAGMA foreign_keys = ON")

        self.cursor.execute(
            """
            INSERT OR IGNORE INTO period_service_prices (period_id, service_id, price)
            SELECT pp.period_id, sc.id, pp.price
            FROM period_prices pp
            JOIN services_catalog sc ON sc.name = pp.service_name
            """
        )
        self.cursor.execute(
            """
            INSERT OR IGNORE INTO order_service_lines (id, order_id, service_id, service_name_snapshot, unit_price, quantity)
            SELECT os.id, os.order_id, sc.id, os.service_name, os.price, os.quantity
            FROM order_services os
            LEFT JOIN services_catalog sc ON sc.name = os.service_name
            """
        )
        self.conn.commit()

    def seed_data(self) -> None:
        self.cursor.execute("SELECT COUNT(*) AS cnt FROM services_catalog")
        if int(self.cursor.fetchone()["cnt"]) == 0:
            now = self._now()
            defaults = [
                ("Диагностика (вычитается из ремонта)", 500, "Диагностика"),
                ("Сборка ПК под ключ", 3000, "Компьютеры"),
                ("Апгрейд ПК", 1500, "Компьютеры"),
                ("Профилактика ПК (чистка + термопаста)", 2000, "Компьютеры"),
                ("Чистка ноутбука + термопаста", 2500, "Ноутбуки"),
                ("Замена матрицы ноутбука", 3000, "Ноутбуки"),
                ("Установка Windows 10/11 + драйверы", 2500, "Программное обеспечение"),
                ("Удаление вирусов", 2000, "Программное обеспечение"),
                ("Выезд мастера + диагностика", 1500, "Выездные услуги"),
                ("3D-печать (стандартная)", 500, "3D-печать"),
            ]
            for name, price, category_name in defaults:
                category_id = self._ensure_category(category_name)
                self.cursor.execute(
                    """
                    INSERT INTO services_catalog (name, price, category_id, created_date, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (name, price, category_id, now),
                )

        self.cursor.execute("SELECT COUNT(*) AS cnt FROM price_periods")
        if int(self.cursor.fetchone()["cnt"]) == 0:
            self.create_period_from_prices(
                f"Период с {datetime.datetime.now().strftime('%d.%m.%Y')}",
                [(s["name"], s["price"]) for s in self.get_active_services()],
            )
        self.conn.commit()

    def load_current_period(self) -> None:
        self.cursor.execute("SELECT id FROM price_periods WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        row = self.cursor.fetchone()
        self.current_period_id = int(row["id"]) if row else None

    def get_active_services(self) -> list[sqlite3.Row]:
        self.cursor.execute(
            """
            SELECT sc.id, sc.name, sc.price, cat.name AS category, sc.is_active
            FROM services_catalog sc
            JOIN service_categories cat ON cat.id = sc.category_id
            WHERE sc.is_active = 1
            ORDER BY cat.name, sc.name
            """
        )
        return self.cursor.fetchall()

    def get_all_services(self) -> list[sqlite3.Row]:
        self.cursor.execute(
            """
            SELECT sc.id, sc.name, sc.price, cat.name AS category, sc.is_active
            FROM services_catalog sc
            JOIN service_categories cat ON cat.id = sc.category_id
            ORDER BY cat.name, sc.name
            """
        )
        return self.cursor.fetchall()

    def get_categories(self) -> list[str]:
        self.cursor.execute("SELECT name FROM service_categories ORDER BY name")
        rows = [row["name"] for row in self.cursor.fetchall()]
        return rows if rows else ["Основные"]

    def get_service_by_name(self, name: str) -> sqlite3.Row | None:
        self.cursor.execute(
            """
            SELECT sc.id, sc.name, sc.price, cat.name AS category
            FROM services_catalog sc
            JOIN service_categories cat ON cat.id = sc.category_id
            WHERE sc.name = ?
            """,
            (name,),
        )
        return self.cursor.fetchone()

    def add_service(self, name: str, price: float, category: str) -> int | None:
        try:
            category_id = self._ensure_category(category)
            self.cursor.execute(
                """
                INSERT INTO services_catalog (name, price, category_id, created_date, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (name, price, category_id, self._now()),
            )
            self.conn.commit()
            return int(self.cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def update_service(self, service_id: int, name: str, price: float, category: str) -> bool:
        try:
            category_id = self._ensure_category(category)
            self.cursor.execute(
                "UPDATE services_catalog SET name = ?, price = ?, category_id = ? WHERE id = ?",
                (name, price, category_id, service_id),
            )
            self.conn.commit()
            return self.cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def set_service_active(self, service_id: int, active: bool) -> None:
        self.cursor.execute("UPDATE services_catalog SET is_active = ? WHERE id = ?", (1 if active else 0, service_id))
        self.conn.commit()

    def delete_service(self, service_id: int) -> None:
        self.cursor.execute("DELETE FROM services_catalog WHERE id = ?", (service_id,))
        self.conn.commit()

    def get_all_clients(self) -> list[sqlite3.Row]:
        self.cursor.execute(
            "SELECT id, name, phone, client_comment, created_date, total_orders, total_spent FROM clients ORDER BY id DESC"
        )
        return self.cursor.fetchall()

    def search_clients(self, query: str) -> list[sqlite3.Row]:
        like = f"%{query.strip()}%"
        self.cursor.execute(
            """
            SELECT id, name, phone, client_comment, created_date, total_orders, total_spent
            FROM clients
            WHERE name LIKE ? OR phone LIKE ? OR client_comment LIKE ?
            ORDER BY id DESC
            """,
            (like, like, like),
        )
        return self.cursor.fetchall()

    def find_client_by_name_phone(self, name: str, phone: str) -> sqlite3.Row | None:
        normalized_phone = self._normalize_phone(phone)
        self.cursor.execute("SELECT id, name, phone FROM clients WHERE name = ? AND phone = ?", (name, normalized_phone or phone))
        return self.cursor.fetchone()

    def find_client_by_phone(self, phone: str) -> sqlite3.Row | None:
        normalized_phone = self._normalize_phone(phone)
        if normalized_phone is None:
            return None
        self.cursor.execute("SELECT id, name, phone FROM clients WHERE phone = ?", (normalized_phone,))
        return self.cursor.fetchone()

    def create_client(self, name: str, phone: str, comment: str = "") -> int | None:
        normalized_phone = self._normalize_phone(phone)
        if normalized_phone is None:
            return None
        try:
            self.cursor.execute(
                """
                INSERT INTO clients (name, phone, client_comment, created_date, total_orders, total_spent)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (name, normalized_phone, (comment or "").strip(), self._now()),
            )
            self.conn.commit()
            return int(self.cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def update_client_stats(self, client_id: int, delta_sum: float, delta_orders: int = 1) -> None:
        self.cursor.execute(
            "UPDATE clients SET total_orders = total_orders + ?, total_spent = total_spent + ? WHERE id = ?",
            (delta_orders, delta_sum, client_id),
        )
        self.conn.commit()

    def get_client_orders(self, client_id: int) -> list[sqlite3.Row]:
        self.cursor.execute(
            "SELECT id, order_number, created_date, status, total_sum FROM orders WHERE client_id = ? ORDER BY id DESC",
            (client_id,),
        )
        return self.cursor.fetchall()

    def get_all_orders(self) -> list[sqlite3.Row]:
        self.cursor.execute(
            """
            SELECT o.id, o.order_number, COALESCE(c.name, 'Без клиента') AS client_name, COALESCE(c.phone, '') AS phone,
                   o.created_date, o.status, o.total_sum
            FROM orders o
            LEFT JOIN clients c ON c.id = o.client_id
            ORDER BY o.id DESC
            """
        )
        return self.cursor.fetchall()

    def get_order_by_id(self, order_id: int) -> sqlite3.Row | None:
        self.cursor.execute(
            """
            SELECT o.id, o.order_number, COALESCE(c.name, 'Без клиента') AS client_name, COALESCE(c.phone, '') AS phone,
                   o.created_date, o.status, o.total_sum, o.client_id,
                   o.device_type, o.extra_periphery, o.technical_notes
            FROM orders o
            LEFT JOIN clients c ON c.id = o.client_id
            WHERE o.id = ?
            """,
            (order_id,),
        )
        return self.cursor.fetchone()

    def next_order_number(self) -> str:
        self.cursor.execute("SELECT order_number FROM orders ORDER BY id DESC LIMIT 1")
        row = self.cursor.fetchone()
        if not row:
            return "ORD-000001"
        try:
            value = int(str(row["order_number"]).split("-")[1]) + 1
            return f"ORD-{value:06d}"
        except Exception:
            return "ORD-000001"

    def create_order(self, client_id: int | None) -> tuple[int, str]:
        number = self.next_order_number()
        self.cursor.execute(
            """
            INSERT INTO orders (order_number, client_id, created_date, status, total_sum, period_id, device_type, extra_periphery, technical_notes)
            VALUES (?, ?, ?, 'active', 0, ?, 'ПК', '', '')
            """,
            (number, client_id, self._now(), self.current_period_id),
        )
        self.conn.commit()
        return int(self.cursor.lastrowid), number

    def update_order_meta(self, order_id: int, device_type: str, extra_periphery: str, technical_notes: str) -> None:
        allowed = {"ПК", "Ноутбук", "Телефон", "Телевизор"}
        safe_device = device_type if device_type in allowed else "ПК"
        self.cursor.execute(
            """
            UPDATE orders
            SET device_type = ?, extra_periphery = ?, technical_notes = ?
            WHERE id = ?
            """,
            (safe_device, (extra_periphery or "").strip(), (technical_notes or "").strip(), order_id),
        )
        self.conn.commit()

    def get_order_services(self, order_id: int) -> list[sqlite3.Row]:
        self.cursor.execute(
            """
            SELECT osl.id,
                   COALESCE(osl.service_name_snapshot, sc.name) AS service_name,
                   osl.unit_price AS price,
                   osl.quantity
            FROM order_service_lines osl
            LEFT JOIN services_catalog sc ON sc.id = osl.service_id
            WHERE osl.order_id = ?
            ORDER BY osl.id
            """,
            (order_id,),
        )
        rows = self.cursor.fetchall()
        if rows:
            return rows
        self.cursor.execute(
            "SELECT id, service_name, price, quantity FROM order_services WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
        return self.cursor.fetchall()

    def add_service_to_order(self, order_id: int, service_name: str, price: float, quantity: int = 1) -> int:
        self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (service_name,))
        service_row = self.cursor.fetchone()
        service_id = int(service_row["id"]) if service_row else None
        self.cursor.execute(
            """
            INSERT INTO order_service_lines (order_id, service_id, service_name_snapshot, unit_price, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, service_id, service_name, price, quantity),
        )
        line_id = int(self.cursor.lastrowid)
        self.cursor.execute(
            "INSERT INTO order_services (order_id, service_name, price, quantity) VALUES (?, ?, ?, ?)",
            (order_id, service_name, price, quantity),
        )
        self.conn.commit()
        return line_id

    def update_order_service_quantity(self, order_service_id: int, quantity: int) -> None:
        self.cursor.execute("UPDATE order_service_lines SET quantity = ? WHERE id = ?", (quantity, order_service_id))
        if self.cursor.rowcount > 0:
            self.cursor.execute("UPDATE order_services SET quantity = ? WHERE id = ?", (quantity, order_service_id))
        self.conn.commit()

    def delete_order_service(self, order_service_id: int) -> None:
        self.cursor.execute("DELETE FROM order_service_lines WHERE id = ?", (order_service_id,))
        self.cursor.execute("DELETE FROM order_services WHERE id = ?", (order_service_id,))
        self.conn.commit()

    def update_order_total(self, order_id: int) -> float:
        self.cursor.execute(
            "SELECT COALESCE(SUM(unit_price * quantity), 0) AS total FROM order_service_lines WHERE order_id = ?",
            (order_id,),
        )
        total = float(self.cursor.fetchone()["total"])
        if total == 0:
            self.cursor.execute("SELECT COALESCE(SUM(price * quantity), 0) AS total FROM order_services WHERE order_id = ?", (order_id,))
            total = float(self.cursor.fetchone()["total"])
        self.cursor.execute("UPDATE orders SET total_sum = ? WHERE id = ?", (total, order_id))
        self.conn.commit()
        return total

    def delete_order(self, order_id: int) -> None:
        self.cursor.execute("SELECT client_id, total_sum FROM orders WHERE id = ?", (order_id,))
        row = self.cursor.fetchone()
        if row and row["client_id"]:
            self.update_client_stats(int(row["client_id"]), -float(row["total_sum"]), -1)
        self.cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        self.conn.commit()

    def set_order_client(self, order_id: int, client_id: int) -> None:
        self.cursor.execute("UPDATE orders SET client_id = ? WHERE id = ?", (client_id, order_id))
        self.conn.commit()

    def get_statistics(self, period: str) -> dict:
        now = datetime.datetime.now()
        if period == "month":
            start = now.replace(day=1, hour=0, minute=0).strftime("%d.%m.%Y")
        elif period == "year":
            start = now.replace(month=1, day=1, hour=0, minute=0).strftime("%d.%m.%Y")
        else:
            start = (now - datetime.timedelta(days=7)).strftime("%d.%m.%Y")
        self.cursor.execute(
            """
            SELECT o.id, o.order_number, o.created_date, o.total_sum, COALESCE(c.name, 'Без клиента') AS client_name, COALESCE(c.phone, '') AS phone
            FROM orders o
            LEFT JOIN clients c ON c.id = o.client_id
            WHERE o.created_date >= ?
            ORDER BY o.id DESC
            """,
            (start,),
        )
        orders = self.cursor.fetchall()
        total_sum = sum(float(o["total_sum"]) for o in orders)
        total_orders = len(orders)
        avg_check = total_sum / total_orders if total_orders else 0
        return {"total_sum": total_sum, "total_orders": total_orders, "avg_check": avg_check, "orders": orders}

    def get_top_clients_by_orders(self, min_orders: int = 2) -> list[sqlite3.Row]:
        self.cursor.execute(
            "SELECT id, name, phone, total_orders, total_spent FROM clients WHERE total_orders >= ? ORDER BY total_orders DESC",
            (min_orders,),
        )
        return self.cursor.fetchall()

    def get_top_clients_by_spent(self, limit: int = 10) -> list[sqlite3.Row]:
        self.cursor.execute(
            "SELECT id, name, phone, total_orders, total_spent FROM clients WHERE total_orders > 0 ORDER BY total_spent DESC LIMIT ?",
            (limit,),
        )
        return self.cursor.fetchall()

    def get_all_periods(self) -> list[sqlite3.Row]:
        self.cursor.execute("SELECT id, name, start_date, is_active FROM price_periods ORDER BY id DESC")
        return self.cursor.fetchall()

    def get_period_prices(self, period_id: int | None = None) -> list[tuple[str, float]]:
        pid = period_id if period_id is not None else self.current_period_id
        if pid is None:
            return []
        self.cursor.execute(
            """
            SELECT sc.name AS service_name, psp.price
            FROM period_service_prices psp
            JOIN services_catalog sc ON sc.id = psp.service_id
            WHERE psp.period_id = ?
            ORDER BY sc.name
            """,
            (pid,),
        )
        rows = self.cursor.fetchall()
        if rows:
            return [(row["service_name"], float(row["price"])) for row in rows]
        self.cursor.execute("SELECT service_name, price FROM period_prices WHERE period_id = ? ORDER BY service_name", (pid,))
        return [(row["service_name"], float(row["price"])) for row in self.cursor.fetchall()]

    def create_period_from_prices(self, period_name: str, prices: list[tuple[str, float]]) -> int:
        now = self._now()
        self.cursor.execute("UPDATE price_periods SET is_active = 0 WHERE is_active = 1")
        self.cursor.execute(
            "INSERT INTO price_periods (name, start_date, is_active, created_date) VALUES (?, ?, 1, ?)",
            (period_name, now, now),
        )
        period_id = int(self.cursor.lastrowid)
        self.cursor.execute("DELETE FROM period_prices WHERE period_id = ?", (period_id,))
        self.cursor.execute("DELETE FROM period_service_prices WHERE period_id = ?", (period_id,))
        self.cursor.execute("UPDATE services_catalog SET is_active = 0")

        default_category_id = self._ensure_category("Основные")
        for service_name, price in prices:
            normalized_name = service_name.strip()
            self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (normalized_name,))
            row = self.cursor.fetchone()
            if row:
                service_id = int(row["id"])
                self.cursor.execute(
                    "UPDATE services_catalog SET price = ?, is_active = 1 WHERE id = ?",
                    (float(price), service_id),
                )
            else:
                self.cursor.execute(
                    """
                    INSERT INTO services_catalog (name, price, category_id, created_date, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (normalized_name, float(price), default_category_id, now),
                )
                service_id = int(self.cursor.lastrowid)

            self.cursor.execute(
                "INSERT INTO period_service_prices (period_id, service_id, price) VALUES (?, ?, ?)",
                (period_id, service_id, float(price)),
            )
            self.cursor.execute(
                "INSERT INTO period_prices (period_id, service_name, price) VALUES (?, ?, ?)",
                (period_id, normalized_name, float(price)),
            )

        self.conn.commit()
        self.current_period_id = period_id
        return period_id

    def activate_period(self, period_id: int) -> None:
        self.cursor.execute("UPDATE price_periods SET is_active = 0 WHERE is_active = 1")
        self.cursor.execute("UPDATE price_periods SET is_active = 1 WHERE id = ?", (period_id,))
        self.conn.commit()
        self.current_period_id = period_id

    def import_legacy_database(self, source_db_path: str) -> dict:
        imported = {"clients": 0, "orders": 0, "order_lines": 0, "services": 0, "periods": 0}
        src = sqlite3.connect(source_db_path)
        src.row_factory = sqlite3.Row
        src_cur = src.cursor()

        def src_table_exists(name: str) -> bool:
            src_cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,))
            return src_cur.fetchone() is not None

        def src_has_column(table: str, column: str) -> bool:
            src_cur.execute(f"PRAGMA table_info({table})")
            return any(row["name"] == column for row in src_cur.fetchall())

        try:
            if src_table_exists("services_catalog"):
                if src_has_column("services_catalog", "category"):
                    src_cur.execute(
                        """
                        SELECT name, price, COALESCE(NULLIF(TRIM(category), ''), 'Основные') AS category_name
                        FROM services_catalog
                        """
                    )
                else:
                    src_cur.execute(
                        """
                        SELECT sc.name, sc.price, COALESCE(cat.name, 'Основные') AS category_name
                        FROM services_catalog sc
                        LEFT JOIN service_categories cat ON cat.id = sc.category_id
                        """
                    )
                for row in src_cur.fetchall():
                    self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (row["name"],))
                    existing = self.cursor.fetchone()
                    category_id = self._ensure_category(row["category_name"])
                    if existing:
                        self.cursor.execute(
                            "UPDATE services_catalog SET price = ?, category_id = ? WHERE id = ?",
                            (float(row["price"]), category_id, int(existing["id"])),
                        )
                    else:
                        self.cursor.execute(
                            """
                            INSERT INTO services_catalog (name, price, category_id, created_date, is_active)
                            VALUES (?, ?, ?, ?, 1)
                            """,
                            (row["name"], float(row["price"]), category_id, self._now()),
                        )
                        imported["services"] += 1

            period_id_map: dict[int, int] = {}
            if src_table_exists("price_periods"):
                src_cur.execute("SELECT id, name, start_date, is_active, created_date FROM price_periods ORDER BY id")
                for row in src_cur.fetchall():
                    self.cursor.execute(
                        "SELECT id FROM price_periods WHERE name = ? AND start_date = ?",
                        (row["name"], row["start_date"]),
                    )
                    existing = self.cursor.fetchone()
                    if existing:
                        new_pid = int(existing["id"])
                    else:
                        self.cursor.execute(
                            """
                            INSERT INTO price_periods (name, start_date, is_active, created_date)
                            VALUES (?, ?, ?, ?)
                            """,
                            (row["name"], row["start_date"], int(row["is_active"]), row["created_date"]),
                        )
                        new_pid = int(self.cursor.lastrowid)
                        imported["periods"] += 1
                    period_id_map[int(row["id"])] = new_pid

            if src_table_exists("period_service_prices"):
                src_cur.execute(
                    """
                    SELECT psp.period_id, sc.name AS service_name, psp.price
                    FROM period_service_prices psp
                    JOIN services_catalog sc ON sc.id = psp.service_id
                    """
                )
                for row in src_cur.fetchall():
                    if int(row["period_id"]) not in period_id_map:
                        continue
                    self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (row["service_name"],))
                    s = self.cursor.fetchone()
                    if not s:
                        continue
                    new_pid = period_id_map[int(row["period_id"])]
                    service_id = int(s["id"])
                    price = float(row["price"])
                    self.cursor.execute(
                        """
                        INSERT OR REPLACE INTO period_service_prices (period_id, service_id, price)
                        VALUES (?, ?, ?)
                        """,
                        (new_pid, service_id, price),
                    )
                    self.cursor.execute(
                        """
                        INSERT OR IGNORE INTO period_prices (period_id, service_name, price)
                        VALUES (?, ?, ?)
                        """,
                        (new_pid, row["service_name"], price),
                    )
            elif src_table_exists("period_prices"):
                src_cur.execute("SELECT period_id, service_name, price FROM period_prices")
                for row in src_cur.fetchall():
                    if int(row["period_id"]) not in period_id_map:
                        continue
                    self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (row["service_name"],))
                    s = self.cursor.fetchone()
                    if not s:
                        continue
                    new_pid = period_id_map[int(row["period_id"])]
                    service_id = int(s["id"])
                    price = float(row["price"])
                    self.cursor.execute(
                        """
                        INSERT OR REPLACE INTO period_service_prices (period_id, service_id, price)
                        VALUES (?, ?, ?)
                        """,
                        (new_pid, service_id, price),
                    )
                    self.cursor.execute(
                        """
                        INSERT OR IGNORE INTO period_prices (period_id, service_name, price)
                        VALUES (?, ?, ?)
                        """,
                        (new_pid, row["service_name"], price),
                    )

            client_id_map: dict[int, int] = {}
            if src_table_exists("clients"):
                if src_has_column("clients", "client_comment"):
                    src_cur.execute("SELECT id, name, phone, client_comment, created_date FROM clients")
                else:
                    src_cur.execute("SELECT id, name, phone, '' AS client_comment, created_date FROM clients")
                for row in src_cur.fetchall():
                    normalized_phone = self._normalize_phone(row["phone"]) or row["phone"]
                    self.cursor.execute("SELECT id FROM clients WHERE phone = ?", (normalized_phone,))
                    existing = self.cursor.fetchone()
                    if existing:
                        new_cid = int(existing["id"])
                        self.cursor.execute(
                            """
                            UPDATE clients
                            SET name = COALESCE(NULLIF(TRIM(name), ''), ?),
                                client_comment = CASE
                                    WHEN TRIM(COALESCE(client_comment, '')) = '' THEN ?
                                    ELSE client_comment
                                END
                            WHERE id = ?
                            """,
                            (row["name"], row["client_comment"], new_cid),
                        )
                    else:
                        self.cursor.execute(
                            """
                            INSERT INTO clients (name, phone, client_comment, created_date, total_orders, total_spent)
                            VALUES (?, ?, ?, ?, 0, 0)
                            """,
                            (row["name"], normalized_phone, row["client_comment"], row["created_date"]),
                        )
                        new_cid = int(self.cursor.lastrowid)
                        imported["clients"] += 1
                    client_id_map[int(row["id"])] = new_cid

            order_id_map: dict[int, int] = {}
            if src_table_exists("orders"):
                src_cur.execute("SELECT id, order_number, client_id, created_date, status, total_sum, period_id FROM orders")
                for row in src_cur.fetchall():
                    self.cursor.execute("SELECT id FROM orders WHERE order_number = ?", (row["order_number"],))
                    existing = self.cursor.fetchone()
                    mapped_client = client_id_map.get(int(row["client_id"])) if row["client_id"] else None
                    mapped_period = period_id_map.get(int(row["period_id"])) if row["period_id"] else None
                    if existing:
                        new_oid = int(existing["id"])
                    else:
                        self.cursor.execute(
                            """
                            INSERT INTO orders (order_number, client_id, created_date, status, total_sum, period_id)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["order_number"],
                                mapped_client,
                                row["created_date"],
                                row["status"],
                                float(row["total_sum"] or 0),
                                mapped_period,
                            ),
                        )
                        new_oid = int(self.cursor.lastrowid)
                        imported["orders"] += 1
                    order_id_map[int(row["id"])] = new_oid

            line_rows = []
            if src_table_exists("order_service_lines"):
                src_cur.execute(
                    """
                    SELECT order_id, service_id, service_name_snapshot, unit_price, quantity
                    FROM order_service_lines
                    """
                )
                line_rows = [dict(row) for row in src_cur.fetchall()]
            elif src_table_exists("order_services"):
                src_cur.execute("SELECT order_id, service_name, price, quantity FROM order_services")
                line_rows = [
                    {
                        "order_id": row["order_id"],
                        "service_id": None,
                        "service_name_snapshot": row["service_name"],
                        "unit_price": row["price"],
                        "quantity": row["quantity"],
                    }
                    for row in src_cur.fetchall()
                ]

            for row in line_rows:
                src_order_id = int(row["order_id"])
                if src_order_id not in order_id_map:
                    continue
                new_order_id = order_id_map[src_order_id]
                service_name = row["service_name_snapshot"]
                service_id = None
                if row["service_id"] is not None and src_table_exists("services_catalog"):
                    src_cur.execute("SELECT name FROM services_catalog WHERE id = ?", (int(row["service_id"]),))
                    src_service = src_cur.fetchone()
                    if src_service:
                        service_name = src_service["name"]
                self.cursor.execute("SELECT id FROM services_catalog WHERE name = ?", (service_name,))
                s = self.cursor.fetchone()
                if s:
                    service_id = int(s["id"])
                unit_price = float(row["unit_price"])
                quantity = int(row["quantity"])

                self.cursor.execute(
                    """
                    SELECT 1 FROM order_service_lines
                    WHERE order_id = ? AND COALESCE(service_id, -1) = COALESCE(?, -1)
                      AND service_name_snapshot = ? AND unit_price = ? AND quantity = ?
                    LIMIT 1
                    """,
                    (new_order_id, service_id, service_name, unit_price, quantity),
                )
                if self.cursor.fetchone():
                    continue

                self.cursor.execute(
                    """
                    INSERT INTO order_service_lines (order_id, service_id, service_name_snapshot, unit_price, quantity)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (new_order_id, service_id, service_name, unit_price, quantity),
                )
                self.cursor.execute(
                    """
                    INSERT INTO order_services (order_id, service_name, price, quantity)
                    VALUES (?, ?, ?, ?)
                    """,
                    (new_order_id, service_name, unit_price, quantity),
                )
                imported["order_lines"] += 1

            self.cursor.execute(
                """
                UPDATE orders
                SET total_sum = (
                    SELECT COALESCE(SUM(unit_price * quantity), 0)
                    FROM order_service_lines osl
                    WHERE osl.order_id = orders.id
                )
                """
            )
            self.cursor.execute(
                """
                UPDATE clients
                SET total_orders = (
                        SELECT COUNT(*) FROM orders o WHERE o.client_id = clients.id
                    ),
                    total_spent = (
                        SELECT COALESCE(SUM(o.total_sum), 0) FROM orders o WHERE o.client_id = clients.id
                    )
                """
            )

            self.conn.commit()
            return imported
        finally:
            src.close()

    def close(self) -> None:
        self.conn.close()
