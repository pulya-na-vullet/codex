from __future__ import annotations

from flask import Flask, flash, redirect, render_template, request, url_for

from webapp.utils import normalize_rf_phone


def register_routes(app: Flask) -> None:
    @app.route("/")
    def dashboard():
        db = app.db
        orders = db.get_all_orders()
        clients = db.get_all_clients()
        services = db.get_all_services()
        stats = {
            "orders": len(orders),
            "clients": len(clients),
            "services": len(services),
            "revenue": sum(float(o["total_sum"]) for o in orders),
        }
        recent_orders = orders[:20]
        return render_template("dashboard.html", stats=stats, recent_orders=recent_orders)

    @app.route("/clients", methods=["GET", "POST"])
    def clients():
        db = app.db
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            phone_raw = request.form.get("phone", "").strip()
            comment = request.form.get("comment", "").strip()
            normalized = normalize_rf_phone(phone_raw)
            if not name:
                flash("Введите имя клиента", "warning")
                return redirect(url_for("clients"))
            if not normalized:
                flash("Введите корректный номер РФ (например, +7 962 550 7832)", "warning")
                return redirect(url_for("clients"))
            if db.find_client_by_phone(normalized):
                flash("Клиент с таким телефоном уже существует", "warning")
                return redirect(url_for("clients"))
            client_id = db.create_client(name, normalized, comment)
            if client_id is None:
                flash("Не удалось создать клиента", "danger")
            else:
                flash("Клиент успешно добавлен", "success")
            return redirect(url_for("clients"))

        query = request.args.get("q", "").strip()
        rows = db.search_clients(query) if query else db.get_all_clients()
        return render_template("clients.html", clients=rows, query=query)

    @app.route("/services", methods=["GET", "POST"])
    def services():
        db = app.db
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            price_raw = request.form.get("price", "").strip().replace(",", ".")
            category = request.form.get("category", "").strip() or "Основные"
            if not name:
                flash("Введите название услуги", "warning")
                return redirect(url_for("services"))
            try:
                price = float(price_raw)
            except ValueError:
                flash("Введите корректную цену", "warning")
                return redirect(url_for("services"))
            if price < 0:
                flash("Цена не может быть отрицательной", "warning")
                return redirect(url_for("services"))
            if db.get_service_by_name(name):
                flash("Услуга с таким названием уже существует", "warning")
                return redirect(url_for("services"))
            service_id = db.add_service(name, price, category)
            if service_id is None:
                flash("Не удалось добавить услугу", "danger")
            else:
                flash("Услуга добавлена", "success")
            return redirect(url_for("services"))

        return render_template("services.html", services=db.get_all_services(), categories=db.get_categories())

    @app.route("/orders")
    def orders():
        return render_template("orders.html", orders=app.db.get_all_orders())

    @app.route("/orders/new", methods=["GET", "POST"])
    def create_order():
        db = app.db
        if request.method == "POST":
            client_id_raw = request.form.get("client_id", "").strip()
            client_id = int(client_id_raw) if client_id_raw else None
            order_id, _ = db.create_order(client_id)
            flash("Заказ создан", "success")
            return redirect(url_for("order_detail", order_id=order_id))

        clients = db.get_all_clients()
        return render_template("order_new.html", clients=clients)

    @app.route("/orders/<int:order_id>")
    def order_detail(order_id: int):
        db = app.db
        order = db.get_order_by_id(order_id)
        if not order:
            flash("Заказ не найден", "warning")
            return redirect(url_for("orders"))
        return render_template(
            "order_detail.html",
            order=order,
            services=db.get_order_services(order_id),
            catalog=db.get_active_services(),
        )

    @app.route("/orders/<int:order_id>/add-service", methods=["POST"])
    def add_order_service(order_id: int):
        db = app.db
        order = db.get_order_by_id(order_id)
        if not order:
            flash("Заказ не найден", "warning")
            return redirect(url_for("orders"))
        service_name = request.form.get("service_name", "").strip()
        qty_raw = request.form.get("quantity", "1").strip()
        try:
            qty = max(1, int(qty_raw))
        except ValueError:
            qty = 1
        service = db.get_service_by_name(service_name)
        if not service:
            flash("Услуга не найдена", "warning")
            return redirect(url_for("order_detail", order_id=order_id))
        db.add_service_to_order(order_id, service_name, float(service["price"]), qty)
        db.update_order_total(order_id)
        flash("Услуга добавлена в заказ", "success")
        return redirect(url_for("order_detail", order_id=order_id))

    @app.route("/orders/<int:order_id>/line/<int:line_id>/delete", methods=["POST"])
    def delete_order_line(order_id: int, line_id: int):
        db = app.db
        db.delete_order_service(line_id)
        db.update_order_total(order_id)
        flash("Позиция удалена", "success")
        return redirect(url_for("order_detail", order_id=order_id))

    @app.route("/orders/<int:order_id>/delete", methods=["POST"])
    def delete_order(order_id: int):
        db = app.db
        db.delete_order(order_id)
        flash("Заказ удален", "success")
        return redirect(url_for("orders"))
