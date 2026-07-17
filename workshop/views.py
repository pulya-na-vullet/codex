from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from workshop.models import (
    AcceptanceAct,
    Client,
    DeviceType,
    Order,
    OrderLine,
    Service,
    loyalty_discount_percent,
    next_numbered,
)
from workshop.pdf import build_acceptance_act_pdf, build_order_pdf
from workshop.services import build_service_catalog_tree, category_choices, ensure_category_path
from workshop.utils import normalize_rf_phone


def login_view(request: HttpRequest):
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    if request.session.get("workshop_authenticated"):
        return redirect(next_url)
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if username == settings.WORKSHOP_USERNAME and password == settings.WORKSHOP_PASSWORD:
            request.session.flush()
            request.session["workshop_authenticated"] = True
            request.session["workshop_username"] = username
            request.session["workshop_last_active"] = time.time()
            messages.success(request, "Вход выполнен")
            return redirect(next_url)
        messages.error(request, "Неверный логин или пароль")
    return render(request, "workshop/login.html", {"next_url": next_url, "title": "Вход"})


@require_http_methods(["GET", "POST"])
def logout_view(request: HttpRequest):
    request.session.flush()
    messages.success(request, "Вы вышли из системы")
    return redirect("login")


def dashboard(request: HttpRequest):
    recent = Order.objects.select_related("client")[:20]
    return render(request, "workshop/dashboard.html", {"recent_orders": recent})


def statistics(request: HttpRequest):
    period = request.GET.get("period", "month")
    if period not in {"week", "month", "year"}:
        period = "month"
    now = timezone.localtime()
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    orders = list(
        Order.objects.select_related("client").filter(created_at__gte=start).order_by("-id")
    )
    total_sum = sum((o.total_sum for o in orders), Decimal("0"))
    total_orders = len(orders)
    avg_check = (total_sum / total_orders) if total_orders else Decimal("0")

    # Monthly breakdown from all orders
    monthly_map: dict[str, dict] = {}
    for o in Order.objects.all().only("created_at", "total_sum"):
        local = timezone.localtime(o.created_at)
        key = local.strftime("%Y-%m")
        label = local.strftime("%m.%Y")
        bucket = monthly_map.setdefault(key, {"key": key, "label": label, "orders": 0, "revenue": Decimal("0")})
        bucket["orders"] += 1
        bucket["revenue"] += o.total_sum
    monthly = [monthly_map[k] for k in sorted(monthly_map.keys(), reverse=True)[:12]]

    # Top clients by spent (annotate)
    clients = Client.objects.all()
    top = sorted(clients, key=lambda c: c.total_spent, reverse=True)[:10]

    return render(
        request,
        "workshop/statistics.html",
        {
            "period": period,
            "stats": {
                "total_sum": total_sum,
                "total_orders": total_orders,
                "avg_check": avg_check,
                "orders": orders,
            },
            "monthly": monthly,
            "top_clients": top,
        },
    )


@require_http_methods(["GET", "POST"])
def clients(request: HttpRequest):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone_raw = request.POST.get("phone", "").strip()
        comment = request.POST.get("comment", "").strip()
        normalized = normalize_rf_phone(phone_raw)
        if not name:
            messages.warning(request, "Введите имя клиента")
        elif not normalized:
            messages.warning(request, "Введите корректный номер РФ")
        elif Client.objects.filter(phone=normalized).exists():
            messages.warning(request, "Клиент с таким телефоном уже существует")
        else:
            Client.objects.create(name=name, phone=normalized, comment=comment)
            messages.success(request, "Клиент успешно добавлен")
        return redirect("clients")

    query = request.GET.get("q", "").strip()
    qs = Client.objects.all()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(comment__icontains=query))
    rows = list(qs)
    # attach computed fields for template convenience
    enriched = []
    for c in rows:
        enriched.append(
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "comment": c.comment,
                "total_orders": c.total_orders,
                "total_spent": c.total_spent,
                "is_regular": c.is_regular,
                "discount_percent": c.discount_percent,
            }
        )
    return render(request, "workshop/clients.html", {"clients": enriched, "query": query})


def client_detail(request: HttpRequest, client_id: int):
    client = get_object_or_404(Client, pk=client_id)
    orders = client.orders.all()
    acts = client.acceptance_acts.all()[:20]
    ctx_client = {
        "id": client.id,
        "name": client.name,
        "phone": client.phone,
        "comment": client.comment,
        "total_orders": client.total_orders,
        "total_spent": client.total_spent,
        "is_regular": client.is_regular,
        "discount_percent": client.discount_percent,
    }
    return render(
        request,
        "workshop/client_detail.html",
        {"client": ctx_client, "orders": orders, "acts": acts},
    )


@require_POST
def client_delete(request: HttpRequest, client_id: int):
    client = get_object_or_404(Client, pk=client_id)
    if client.orders.exists() or client.acceptance_acts.exists():
        messages.warning(
            request,
            "Нельзя удалить клиента: есть связанные заказ-наряды или акты. Сначала удалите их.",
        )
        return redirect("client_detail", client_id=client_id)
    client.delete()
    messages.success(request, "Клиент удалён")
    return redirect("clients")


@require_GET
def export_clients_excel(request: HttpRequest):
    try:
        from openpyxl import Workbook
    except ImportError:
        messages.warning(request, "Установите openpyxl: pip install openpyxl")
        return redirect("clients")
    wb = Workbook()
    ws = wb.active
    ws.title = "Клиенты"
    ws.append(["номер телефона", "имя", "комментарий"])
    for c in Client.objects.order_by("id"):
        ws.append([c.phone, c.name, c.comment])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="clients.xlsx"'
    return response


@require_POST
def import_clients_excel(request: HttpRequest):
    try:
        from openpyxl import load_workbook
    except ImportError:
        messages.warning(request, "Установите openpyxl: pip install openpyxl")
        return redirect("clients")
    upload = request.FILES.get("excel_file")
    if not upload:
        messages.warning(request, "Выберите Excel-файл")
        return redirect("clients")
    wb = load_workbook(upload, read_only=True, data_only=True)
    ws = wb.active
    imported = skipped_existing = skipped_invalid = 0
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if not row:
            continue
        values = list(row) + [None, None, None]
        phone = "" if values[0] is None else str(values[0]).strip()
        name = "" if values[1] is None else str(values[1]).strip()
        comment = "" if values[2] is None else str(values[2]).strip()
        if idx == 1 and ("телефон" in phone.lower() or phone.lower() in {"phone", "номер телефона"}):
            continue
        if not phone and not name:
            continue
        if not phone or not name:
            skipped_invalid += 1
            continue
        normalized = normalize_rf_phone(phone)
        if not normalized:
            skipped_invalid += 1
            continue
        if Client.objects.filter(phone=normalized).exists():
            skipped_existing += 1
            continue
        Client.objects.create(name=name, phone=normalized, comment=comment)
        imported += 1
    messages.success(
        request,
        f"Импорт завершён: добавлено {imported}, пропущено существующих {skipped_existing}, некорректных {skipped_invalid}",
    )
    return redirect("clients")


@require_http_methods(["GET", "POST"])
def services(request: HttpRequest):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        price_raw = request.POST.get("price", "").strip().replace(",", ".")
        category = request.POST.get("category", "").strip() or "Основные"
        if not name:
            messages.warning(request, "Введите название услуги")
        else:
            try:
                price = Decimal(price_raw)
            except (InvalidOperation, ValueError):
                messages.warning(request, "Введите корректную цену")
                return redirect("services")
            if price < 0:
                messages.warning(request, "Цена не может быть отрицательной")
            elif Service.objects.filter(name=name).exists():
                messages.warning(request, "Услуга с таким названием уже существует")
            else:
                cat = ensure_category_path(category)
                Service.objects.create(name=name, price=price, category=cat, is_active=True)
                messages.success(request, "Услуга добавлена")
        return redirect("services")

    return render(
        request,
        "workshop/services.html",
        {
            "services": Service.objects.select_related("category"),
            "categories": category_choices(),
        },
    )


@require_POST
def service_delete(request: HttpRequest, service_id: int):
    service = get_object_or_404(Service, pk=service_id)
    # Soft-safe: allow delete; order lines keep snapshot via SET_NULL on service FK
    service.delete()
    messages.success(request, "Услуга удалена")
    return redirect("services")


def orders_list(request: HttpRequest):
    return render(
        request,
        "workshop/orders.html",
        {"orders": Order.objects.select_related("client")},
    )


@require_http_methods(["GET", "POST"])
def order_create(request: HttpRequest):
    if request.method == "POST":
        client_id_raw = request.POST.get("client_id", "").strip()
        client = None
        if client_id_raw:
            client = get_object_or_404(Client, pk=int(client_id_raw))
        discount = client.discount_percent if client else Decimal("0")
        # After creating, visits increase — refresh discount after save
        order = Order.objects.create(
            order_number=next_numbered("ORD", Order, "order_number"),
            client=client,
            discount_percent=discount,
        )
        if client:
            order.discount_percent = loyalty_discount_percent(client.total_orders)
            order.save(update_fields=["discount_percent"])
        messages.success(request, "Заказ создан")
        return redirect("order_detail", order_id=order.id)

    selected = request.GET.get("client_id")
    clients_qs = Client.objects.all()
    clients_data = [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "is_regular": c.is_regular,
            "discount_percent": c.discount_percent,
        }
        for c in clients_qs
    ]
    return render(
        request,
        "workshop/order_new.html",
        {
            "clients": clients_data,
            "selected_client_id": int(selected) if selected and selected.isdigit() else None,
        },
    )


def order_detail(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order.objects.select_related("client"), pk=order_id)
    client_ctx = None
    if order.client:
        c = order.client
        client_ctx = {
            "id": c.id,
            "is_regular": c.is_regular,
            "discount_percent": c.discount_percent,
            "total_orders": c.total_orders,
        }
    return render(
        request,
        "workshop/order_detail.html",
        {
            "order": order,
            "client": client_ctx,
            "lines": order.lines.all(),
            "catalog_tree": build_service_catalog_tree(active_only=True),
            "device_types": [c[0] for c in DeviceType.choices],
        },
    )


@require_POST
def order_update_meta(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    device_type = request.POST.get("device_type", DeviceType.PC)
    if device_type not in dict(DeviceType.choices):
        device_type = DeviceType.PC
    order.device_type = device_type
    order.extra_periphery = request.POST.get("extra_periphery", "").strip()
    # Preserve real newlines from textarea (including Shift+Enter as \n)
    order.technical_notes = (request.POST.get("technical_notes", "") or "").replace("\r\n", "\n").replace("\r", "\n")
    order.save(update_fields=["device_type", "extra_periphery", "technical_notes"])
    messages.success(request, "Данные заказ-наряда сохранены")
    return redirect("order_detail", order_id=order.id)


@require_POST
def order_add_service(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    service_name = request.POST.get("service_name", "").strip()
    try:
        qty = max(1, int(request.POST.get("quantity", "1")))
    except ValueError:
        qty = 1
    service = Service.objects.filter(name=service_name, is_active=True).first()
    if not service:
        messages.warning(request, "Услуга не найдена")
        return redirect("order_detail", order_id=order.id)
    OrderLine.objects.create(
        order=order,
        service=service,
        service_name=service.name,
        unit_price=service.price,
        quantity=qty,
    )
    order.recalculate_totals()
    messages.success(request, "Услуга добавлена в заказ")
    return redirect("order_detail", order_id=order.id)


@require_POST
def order_line_delete(request: HttpRequest, order_id: int, line_id: int):
    order = get_object_or_404(Order, pk=order_id)
    OrderLine.objects.filter(pk=line_id, order=order).delete()
    order.recalculate_totals()
    messages.success(request, "Строка удалена")
    return redirect("order_detail", order_id=order.id)


@require_POST
def order_delete(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order, pk=order_id)
    order.delete()
    messages.success(request, "Заказ удалён")
    return redirect("orders")


def order_print(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order.objects.select_related("client"), pk=order_id)
    return render(
        request,
        "workshop/print_order.html",
        {
            "order": order,
            "lines": order.lines.all(),
            "company_name": settings.COMPANY_NAME,
            "company_phone": settings.COMPANY_PHONE,
            "quality_phone": settings.QUALITY_PHONE,
            "company_address": settings.COMPANY_ADDRESS,
        },
    )


def order_pdf(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order.objects.select_related("client"), pk=order_id)
    pdf_bytes = build_order_pdf(order, list(order.lines.all()))
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{order.order_number}.pdf"'
    return response


@require_POST
def order_print_direct(request: HttpRequest, order_id: int):
    order = get_object_or_404(Order.objects.select_related("client"), pk=order_id)
    pdf_bytes = build_order_pdf(order, list(order.lines.all()))
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        if platform.system() == "Windows":
            os.startfile(tmp.name, "print")  # type: ignore[attr-defined]
        else:
            subprocess.run(["lp", tmp.name], check=True)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        messages.success(request, "Документ отправлен на принтер")
    except Exception as err:
        messages.error(request, f"Не удалось отправить документ на принтер: {err}")
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return redirect("order_detail", order_id=order.id)


@require_http_methods(["GET", "POST"])
def acceptance_list_create(request: HttpRequest):
    if request.method == "POST":
        client_id = request.POST.get("client_id", "").strip()
        if not client_id:
            messages.warning(request, "Выберите клиента")
            return redirect("acceptance_acts")
        client = get_object_or_404(Client, pk=int(client_id))
        device_type = request.POST.get("device_type", DeviceType.PC)
        if device_type not in dict(DeviceType.choices):
            device_type = DeviceType.PC
        defect = request.POST.get("declared_defect", "").strip()
        if not defect:
            messages.warning(request, "Укажите заявленную неисправность")
            return redirect("acceptance_acts")
        order = None
        order_id = request.POST.get("order_id", "").strip()
        if order_id:
            order = Order.objects.filter(pk=int(order_id)).first()
        act = AcceptanceAct.objects.create(
            act_number=next_numbered("ACT", AcceptanceAct, "act_number"),
            client=client,
            order=order,
            device_type=device_type,
            brand_model=request.POST.get("brand_model", "").strip(),
            serial_number=request.POST.get("serial_number", "").strip(),
            accessories=request.POST.get("accessories", "").strip(),
            appearance=request.POST.get("appearance", "").strip(),
            declared_defect=defect,
            password_info=request.POST.get("password_info", "").strip(),
            notes=request.POST.get("notes", "").strip(),
        )
        messages.success(request, f"Акт {act.act_number} создан")
        return redirect("acceptance_detail", act_id=act.id)

    return render(
        request,
        "workshop/acceptance_list.html",
        {
            "acts": AcceptanceAct.objects.select_related("client", "order")[:100],
            "clients": Client.objects.all(),
            "orders": Order.objects.select_related("client")[:100],
            "device_types": [c[0] for c in DeviceType.choices],
        },
    )


def acceptance_detail(request: HttpRequest, act_id: int):
    act = get_object_or_404(AcceptanceAct.objects.select_related("client", "order"), pk=act_id)
    return render(request, "workshop/acceptance_detail.html", {"act": act})


def acceptance_print(request: HttpRequest, act_id: int):
    act = get_object_or_404(AcceptanceAct.objects.select_related("client", "order"), pk=act_id)
    return render(
        request,
        "workshop/print_acceptance.html",
        {
            "act": act,
            "company_name": settings.COMPANY_NAME,
            "company_phone": settings.COMPANY_PHONE,
            "company_address": settings.COMPANY_ADDRESS,
            "master_sign": settings.MASTER_SIGN,
        },
    )


def acceptance_pdf(request: HttpRequest, act_id: int):
    act = get_object_or_404(AcceptanceAct.objects.select_related("client", "order"), pk=act_id)
    pdf_bytes = build_acceptance_act_pdf(act)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{act.act_number}.pdf"'
    return response


@require_POST
def acceptance_print_direct(request: HttpRequest, act_id: int):
    act = get_object_or_404(AcceptanceAct.objects.select_related("client", "order"), pk=act_id)
    pdf_bytes = build_acceptance_act_pdf(act)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        if platform.system() == "Windows":
            os.startfile(tmp.name, "print")  # type: ignore[attr-defined]
        else:
            subprocess.run(["lp", tmp.name], check=True)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        messages.success(request, "Акт отправлен на принтер")
    except Exception as err:
        messages.error(request, f"Не удалось отправить на принтер: {err}")
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return redirect("acceptance_detail", act_id=act.id)


@require_POST
def acceptance_delete(request: HttpRequest, act_id: int):
    act = get_object_or_404(AcceptanceAct, pk=act_id)
    act.delete()
    messages.success(request, "Акт удалён")
    return redirect("acceptance_acts")
