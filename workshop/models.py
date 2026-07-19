from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Sum
from django.utils import timezone


def debt_tracking_start() -> datetime:
    """Заказы раньше этой даты не считаются долгом (массовый импорт истории)."""
    start_date = getattr(settings, "DEBT_TRACKING_START_DATE", date(2026, 6, 16))
    return timezone.make_aware(
        datetime.combine(start_date, time.min),
        timezone.get_current_timezone(),
    )


def debt_grace_period() -> timedelta:
    """Сколько ждать после закрытия заказ-наряда, прежде чем считать должником."""
    days = int(getattr(settings, "DEBT_GRACE_DAYS", 1) or 1)
    return timedelta(days=max(0, days))


def is_debt_tracking_active_for(created_at) -> bool:
    if created_at is None:
        return False
    return created_at >= debt_tracking_start()


def debtor_orders_queryset():
    """Неоплаченные закрытые заказы, у которых прошли сутки после закрытия."""
    from django.db.models.functions import Coalesce

    from workshop.models import Order, OrderStatus, PaymentMethod

    grace_before = timezone.now() - debt_grace_period()
    # У старых «Завершён» без closed_at берём дату создания заказа.
    return (
        Order.objects.annotate(debt_closed_at=Coalesce("closed_at", "created_at"))
        .filter(
            status=OrderStatus.DONE,
            payment_method=PaymentMethod.UNPAID,
            total_sum__gt=0,
            debt_closed_at__lte=grace_before,
            created_at__gte=debt_tracking_start(),
        )
    )


class Client(models.Model):
    name = models.CharField("Имя", max_length=200)
    phone = models.CharField("Телефон", max_length=20, unique=True)
    comment = models.TextField("Комментарий", blank=True, default="")
    allow_marketing_sms = models.BooleanField("Можно слать маркетинг в Max", default=True)
    max_user_id = models.CharField(
        "Max user_id",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="ID пользователя в Max после того, как он написал боту",
    )
    discount_percent = models.DecimalField(
        "Скидка %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(0), MaxValueValidator(15)],
        help_text="Ручная скидка клиента 0–15%. С 3-го заказа автоматически ставится 10%.",
    )
    discount_manual = models.BooleanField(
        "Скидка задана вручную",
        default=False,
        help_text="Если включено — авто-скидка 10% за постоянство не перезаписывает значение",
    )
    created_at = models.DateTimeField("Создан", default=timezone.now)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"

    @property
    def total_orders(self) -> int:
        return self.orders.count()

    @property
    def total_spent(self) -> Decimal:
        total = self.orders.aggregate(s=Sum("total_sum"))["s"]
        return (total or Decimal("0")).quantize(Decimal("0.01"))

    @property
    def is_regular(self) -> bool:
        """Постоянный клиент — от 3 заказ-нарядов."""
        return self.total_orders >= 3

    def set_discount_percent(self, value, *, manual: bool = False, save: bool = True) -> Decimal:
        self.discount_percent = clamp_discount_percent(value)
        if manual:
            self.discount_manual = True
        if save:
            self.save(update_fields=["discount_percent", "discount_manual"])
        return self.discount_percent

    def apply_auto_regular_discount(self, *, save: bool = True) -> bool:
        """С 3-го заказа автоматически 10%, если скидку не правили вручную."""
        if self.total_orders < 3 or self.discount_manual:
            return False
        target = Decimal("10")
        if self.discount_percent >= target:
            return False
        self.discount_percent = target
        if save:
            self.save(update_fields=["discount_percent"])
        return True


class ServiceCategory(models.Model):
    name = models.CharField("Название", max_length=120)
    parent = models.ForeignKey(
        "self",
        verbose_name="Родитель",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Категория услуг"
        verbose_name_plural = "Категории услуг"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"],
                name="uniq_category_name_per_parent",
            )
        ]

    def __str__(self) -> str:
        return self.path_label

    @property
    def path_label(self) -> str:
        parts: list[str] = []
        node: ServiceCategory | None = self
        seen: set[int] = set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            parts.append(node.name)
            node = node.parent
        return " / ".join(reversed(parts))


class Service(models.Model):
    name = models.CharField("Название", max_length=255, unique=True)
    price = models.DecimalField(
        "Цена",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    category = models.ForeignKey(
        ServiceCategory,
        verbose_name="Категория",
        related_name="services",
        on_delete=models.PROTECT,
    )
    is_active = models.BooleanField("Активна", default=True)
    created_at = models.DateTimeField("Создана", default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"

    def __str__(self) -> str:
        return self.name


class DeviceType(models.TextChoices):
    PC = "ПК", "ПК"
    LAPTOP = "Ноутбук", "Ноутбук"
    PHONE = "Телефон", "Телефон"
    TV = "Телевизор", "Телевизор"


class OrderStatus(models.TextChoices):
    ACTIVE = "active", "В работе"
    READY_CALL = "ready_call", "Работа выполнена — позвонить"
    DONE = "done", "Выполнена"
    CANCELLED = "cancelled", "Отменён"


class AcceptanceActStatus(models.TextChoices):
    DIAGNOSTICS = "diagnostics", "Диагностика идёт"
    DIAGNOSTICS_DONE = "diagnostics_done", "Диагностика выполнена"
    DONE = "done", "Выполнена"


class PaymentMethod(models.TextChoices):
    UNPAID = "unpaid", "Не оплачен"
    CASH = "cash", "Наличные"
    TRANSFER = "transfer", "Перевод (чек)"


class Order(models.Model):
    order_number = models.CharField("Номер", max_length=32, unique=True)
    client = models.ForeignKey(
        Client,
        verbose_name="Клиент",
        null=True,
        blank=True,
        related_name="orders",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField("Создан", default=timezone.now)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.ACTIVE,
    )
    closed_at = models.DateTimeField("Закрыт", null=True, blank=True, db_index=True)
    client_called_at = models.DateTimeField("Звонок клиенту", null=True, blank=True)
    device_type = models.CharField(
        "Устройство",
        max_length=32,
        choices=DeviceType.choices,
        default=DeviceType.PC,
    )
    extra_periphery = models.TextField("Доп. периферия", blank=True, default="")
    technical_notes = models.TextField("Техническая информация", blank=True, default="")
    discount_percent = models.DecimalField(
        "Скидка %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
    )
    subtotal_sum = models.DecimalField(
        "Сумма до скидки",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )
    total_sum = models.DecimalField(
        "Итого",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )

    # Оплата клиентом
    payment_method = models.CharField(
        "Способ оплаты",
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.UNPAID,
    )
    payment_at = models.DateTimeField("Дата оплаты", null=True, blank=True)
    payment_receipt = models.FileField(
        "Скриншот чека (перевод)",
        upload_to="payment_receipts/%Y/%m/",
        blank=True,
        null=True,
    )
    payment_note = models.CharField("Комментарий к оплате", max_length=255, blank=True, default="")

    # Чек в «Мой налог» (самозанятый)
    mytax_issued = models.BooleanField("Чек Мой налог выдан", default=False)
    mytax_at = models.DateTimeField("Дата чека Мой налог", null=True, blank=True)
    mytax_receipt = models.FileField(
        "Скриншот чека Мой налог",
        upload_to="mytax_receipts/%Y/%m/",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-id"]
        verbose_name = "Заказ-наряд"
        verbose_name_plural = "Заказ-наряды"

    def __str__(self) -> str:
        return self.order_number

    @property
    def is_paid(self) -> bool:
        return self.payment_method in {PaymentMethod.CASH, PaymentMethod.TRANSFER}

    @property
    def is_in_progress(self) -> bool:
        return self.status == OrderStatus.ACTIVE

    @property
    def needs_client_call(self) -> bool:
        return self.status == OrderStatus.READY_CALL

    @property
    def effective_closed_at(self):
        """Дата закрытия: closed_at или created_at для старых завершённых без даты."""
        if self.closed_at:
            return self.closed_at
        if self.status == OrderStatus.DONE:
            return self.created_at
        return None

    @property
    def is_debtor(self) -> bool:
        """Должник: заказ закрыт, не оплачен, и прошли сутки после закрытия."""
        if self.is_paid or self.total_sum <= 0:
            return False
        if self.status != OrderStatus.DONE:
            return False
        closed = self.effective_closed_at
        if not closed:
            return False
        if not is_debt_tracking_active_for(self.created_at):
            return False
        return timezone.now() >= closed + debt_grace_period()

    def apply_status(self, status: str, *, save: bool = True) -> None:
        """Update work status and closed_at / call timestamps."""
        # Работа сделана → сначала «позвонить», финальный «Выполнена» — после звонка.
        if status == OrderStatus.DONE and self.status != OrderStatus.READY_CALL and not self.client_called_at:
            status = OrderStatus.READY_CALL

        self.status = status
        if status in {OrderStatus.DONE, OrderStatus.READY_CALL}:
            if not self.closed_at:
                self.closed_at = timezone.now()
        else:
            self.closed_at = None
            self.client_called_at = None

        if status == OrderStatus.DONE and not self.client_called_at:
            self.client_called_at = timezone.now()
        elif status != OrderStatus.DONE:
            # ready_call / active / cancelled — звонок ещё не зафиксирован
            if status != OrderStatus.READY_CALL:
                self.client_called_at = None

        if save:
            self.save(update_fields=["status", "closed_at", "client_called_at"])

    def mark_client_called(self, *, save: bool = True) -> None:
        """Оператор подтвердил звонок клиенту → статус «Выполнена»."""
        self.status = OrderStatus.DONE
        now = timezone.now()
        if not self.closed_at:
            self.closed_at = now
        self.client_called_at = now
        if save:
            self.save(update_fields=["status", "closed_at", "client_called_at"])

    def recalculate_totals(self, save: bool = True) -> Decimal:
        subtotal = Decimal("0")
        for line in self.lines.all():
            subtotal += line.line_total
        if self.client_id:
            client = self.client
            if client is not None:
                client.apply_auto_regular_discount(save=True)
                client.refresh_from_db(fields=["discount_percent"])
                self.discount_percent = clamp_discount_percent(client.discount_percent)
            else:
                self.discount_percent = Decimal("0")
        else:
            self.discount_percent = Decimal("0")
        self.subtotal_sum = subtotal.quantize(Decimal("0.01"))
        factor = (Decimal("100") - self.discount_percent) / Decimal("100")
        self.total_sum = (subtotal * factor).quantize(Decimal("0.01"))
        if save:
            self.save(update_fields=["discount_percent", "subtotal_sum", "total_sum"])
        return self.total_sum

    @property
    def discount_amount(self) -> Decimal:
        """Сумма дополнительной скидки от суммы расчёта."""
        return (self.subtotal_sum - self.total_sum).quantize(Decimal("0.01"))


class OrderLine(models.Model):
    order = models.ForeignKey(
        Order,
        verbose_name="Заказ",
        related_name="lines",
        on_delete=models.CASCADE,
    )
    service = models.ForeignKey(
        Service,
        verbose_name="Услуга",
        null=True,
        blank=True,
        related_name="order_lines",
        on_delete=models.SET_NULL,
    )
    service_name = models.CharField("Название (снимок)", max_length=255)
    unit_price = models.DecimalField("Цена", max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField("Количество", default=1)

    class Meta:
        ordering = ["id"]
        verbose_name = "Строка заказа"
        verbose_name_plural = "Строки заказа"

    def __str__(self) -> str:
        return f"{self.service_name} x{self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))


class AcceptanceAct(models.Model):
    """Акт приёма-передачи техники на диагностику/ремонт."""

    act_number = models.CharField("Номер акта", max_length=32, unique=True)
    client = models.ForeignKey(
        Client,
        verbose_name="Клиент",
        related_name="acceptance_acts",
        on_delete=models.PROTECT,
    )
    order = models.ForeignKey(
        Order,
        verbose_name="Связанный заказ",
        null=True,
        blank=True,
        related_name="acceptance_acts",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField("Дата приёма", default=timezone.now)
    status = models.CharField(
        "Статус",
        max_length=32,
        choices=AcceptanceActStatus.choices,
        default=AcceptanceActStatus.DIAGNOSTICS,
        db_index=True,
    )
    client_called_at = models.DateTimeField("Звонок клиенту", null=True, blank=True)
    finished_at = models.DateTimeField("Диагностика/работа завершена", null=True, blank=True)
    device_type = models.CharField(
        "Тип устройства",
        max_length=32,
        choices=DeviceType.choices,
        default=DeviceType.PC,
    )
    brand_model = models.CharField("Марка / модель", max_length=255, blank=True, default="")
    serial_number = models.CharField("Серийный номер", max_length=120, blank=True, default="")
    accessories = models.TextField(
        "Комплектация",
        blank=True,
        default="",
        help_text="Зарядка, сумка, мышь и т.д.",
    )
    appearance = models.TextField("Внешний вид / повреждения", blank=True, default="")
    declared_defect = models.TextField("Заявленная неисправность")
    password_info = models.CharField("Пароль / PIN", max_length=255, blank=True, default="")
    notes = models.TextField("Примечания", blank=True, default="")

    class Meta:
        ordering = ["-id"]
        verbose_name = "Акт приёма-передачи"
        verbose_name_plural = "Акты приёма-передачи"

    def __str__(self) -> str:
        return self.act_number

    @property
    def is_in_diagnostics(self) -> bool:
        return self.status == AcceptanceActStatus.DIAGNOSTICS

    @property
    def needs_client_call(self) -> bool:
        return self.status == AcceptanceActStatus.DIAGNOSTICS_DONE

    def apply_status(self, status: str, *, save: bool = True) -> None:
        if status == AcceptanceActStatus.DONE and self.status != AcceptanceActStatus.DIAGNOSTICS_DONE and not self.client_called_at:
            status = AcceptanceActStatus.DIAGNOSTICS_DONE

        self.status = status
        if status in {AcceptanceActStatus.DIAGNOSTICS_DONE, AcceptanceActStatus.DONE}:
            if not self.finished_at:
                self.finished_at = timezone.now()
        else:
            self.finished_at = None
            self.client_called_at = None

        if status == AcceptanceActStatus.DONE and not self.client_called_at:
            self.client_called_at = timezone.now()

        if save:
            self.save(update_fields=["status", "finished_at", "client_called_at"])

    def mark_client_called(self, *, save: bool = True) -> None:
        self.status = AcceptanceActStatus.DONE
        now = timezone.now()
        if not self.finished_at:
            self.finished_at = now
        self.client_called_at = now
        if save:
            self.save(update_fields=["status", "finished_at", "client_called_at"])


class AuditLog(models.Model):
    created_at = models.DateTimeField("Когда", default=timezone.now, db_index=True)
    username = models.CharField("Пользователь", max_length=64, blank=True, default="")
    action = models.CharField("Действие", max_length=64)
    entity_type = models.CharField("Сущность", max_length=64, blank=True, default="")
    entity_id = models.CharField("ID сущности", max_length=64, blank=True, default="")
    details = models.TextField("Детали", blank=True, default="")
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Запись журнала"
        verbose_name_plural = "Журнал действий"

    def __str__(self) -> str:
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.username} {self.action}"


class PrintJobStatus(models.TextChoices):
    PENDING = "pending", "В очереди"
    PRINTING = "printing", "Печатается"
    DONE = "done", "Готово"
    FAILED = "failed", "Ошибка"


class PrintJob(models.Model):
    created_at = models.DateTimeField("Создано", default=timezone.now, db_index=True)
    started_at = models.DateTimeField("Начато", null=True, blank=True)
    finished_at = models.DateTimeField("Завершено", null=True, blank=True)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=PrintJobStatus.choices,
        default=PrintJobStatus.PENDING,
        db_index=True,
    )
    file_path = models.CharField("Файл", max_length=500)
    title = models.CharField("Документ", max_length=255)
    doc_type = models.CharField("Тип", max_length=32, blank=True, default="")
    entity_type = models.CharField("Сущность", max_length=64, blank=True, default="")
    entity_id = models.CharField("ID сущности", max_length=64, blank=True, default="")
    copy_index = models.PositiveSmallIntegerField("Экземпляр", default=1)
    copies_total = models.PositiveSmallIntegerField("Всего экз.", default=2)
    username = models.CharField("Пользователь", max_length=64, blank=True, default="")
    error = models.TextField("Ошибка", blank=True, default="")

    class Meta:
        ordering = ["id"]
        verbose_name = "Задание печати"
        verbose_name_plural = "Очередь печати"

    def __str__(self) -> str:
        return f"#{self.id} {self.title} ({self.copy_index}/{self.copies_total}) {self.status}"


class SmsProvider(models.TextChoices):
    LOG_ONLY = "log", "Только журнал (тест)"
    MAX = "max", "Max мессенджер"


class SmsSettings(models.Model):
    """Singleton-настройки рассылок через Max."""

    enabled = models.BooleanField("Рассылки включены", default=False)
    provider = models.CharField(
        "Канал",
        max_length=20,
        choices=SmsProvider.choices,
        default=SmsProvider.LOG_ONLY,
    )
    bot_token = models.CharField("Токен бота Max", max_length=255, blank=True, default="")
    bot_username = models.CharField("Username бота", max_length=64, blank=True, default="")
    bot_link = models.CharField(
        "Ссылка на бота",
        max_length=255,
        blank=True,
        default="",
        help_text="Например https://max.ru/your_bot — покажите клиентам для подписки",
    )
    welcome_text = models.TextField(
        "Приветствие бота",
        blank=True,
        default=(
            "Здравствуйте! Это бот ИТ-мастерской.\n"
            "Отправьте свой номер телефона в формате +79991234567 — "
            "привяжем ваш профиль для уведомлений о заказах и долгах."
        ),
    )
    debt_template = models.TextField(
        "Шаблон сообщения о долге",
        default=(
            "{name}, по заказ-наряду {order} задолженность {sum} руб. "
            "Просим оплатить. {company}, {company_phone}"
        ),
        help_text="Плейсхолдеры: {name} {phone} {order} {sum} {company} {company_phone}",
    )
    order_done_template = models.TextField(
        "Шаблон: заказ выполнен",
        default=(
            "{name}, заказ-наряд {order} выполнен. Сумма: {sum} руб. "
            "Ждём вас. {company}, {company_phone}"
        ),
        help_text="Плейсхолдеры: {name} {phone} {order} {sum} {company} {company_phone}",
    )
    diagnostics_done_template = models.TextField(
        "Шаблон: диагностика выполнена",
        default=(
            "{name}, диагностика по акту {act} выполнена. "
            "Устройство: {device}. {company}, {company_phone}"
        ),
        help_text="Плейсхолдеры: {name} {phone} {act} {device} {company} {company_phone}",
    )
    marketing_enabled = models.BooleanField("Маркетинг включён", default=False)
    marketing_default_text = models.TextField(
        "Текст маркетинга по умолчанию",
        blank=True,
        default="Здравствуйте, {name}! Спецпредложение от {company}. Тел. {company_phone}",
        help_text="Плейсхолдеры: {name} {phone} {company} {company_phone}",
    )
    long_poll_enabled = models.BooleanField(
        "Long Poll бота (для LAN без публичного HTTPS)",
        default=True,
        help_text="Фоновый опрос Max API: привязка клиентов по телефону из чата с ботом",
    )
    updates_marker = models.BigIntegerField("Маркер long poll", null=True, blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки Max-рассылок"
        verbose_name_plural = "Настройки Max-рассылок"

    def __str__(self) -> str:
        return f"Max ({self.get_provider_display()})"

    @classmethod
    def get_solo(cls) -> "SmsSettings":
        obj = cls.objects.first()
        if obj:
            return obj
        return cls.objects.create()


class YandexAiSettings(models.Model):
    """Singleton-настройки Яндекс GPT для ежедневного отчёта администратору."""

    enabled = models.BooleanField("Ежедневный AI-отчёт включён", default=False)
    api_key = models.CharField("API-ключ Yandex Cloud", max_length=255, blank=True, default="")
    folder_id = models.CharField("Folder ID каталога", max_length=64, blank=True, default="")
    model_name = models.CharField(
        "Модель",
        max_length=64,
        blank=True,
        default="yandexgpt-lite",
        help_text="Например yandexgpt-lite или yandexgpt",
    )
    admin_phone = models.CharField(
        "Телефон администратора",
        max_length=32,
        blank=True,
        default="",
        help_text="Клиент с этим телефоном должен быть привязан к Max",
    )
    admin_max_user_id = models.CharField(
        "Max user_id администратора",
        max_length=64,
        blank=True,
        default="",
        help_text="Можно указать напрямую, если телефон ещё не привязан",
    )
    report_hour_msk = models.PositiveSmallIntegerField(
        "Час отправки (МСК)",
        default=20,
        help_text="Час по Москве (0–23)",
    )
    report_minute_msk = models.PositiveSmallIntegerField(
        "Минута отправки (МСК)",
        default=0,
        help_text="Минута по Москве (0–59)",
    )
    last_report_date = models.DateField("Дата последнего отчёта", null=True, blank=True)
    last_report_at = models.DateTimeField("Когда отправляли", null=True, blank=True)
    last_report_text = models.TextField("Текст последнего отчёта", blank=True, default="")
    last_report_error = models.TextField("Ошибка последней отправки", blank=True, default="")
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки Яндекс ИИ"
        verbose_name_plural = "Настройки Яндекс ИИ"

    def __str__(self) -> str:
        return "Yandex AI report"

    @classmethod
    def get_solo(cls) -> "YandexAiSettings":
        obj = cls.objects.first()
        if obj:
            return obj
        return cls.objects.create()


class SmsKind(models.TextChoices):
    DEBT = "debt", "Долг"
    MARKETING = "marketing", "Маркетинг"
    TEST = "test", "Тест"
    SYSTEM = "system", "Системное"


class MarketingBlast(models.Model):
    """Одна масс-рассылка маркетинга (может включать несколько SmsLog)."""

    created_at = models.DateTimeField("Когда", default=timezone.now, db_index=True)
    template_text = models.TextField("Текст шаблона")
    username = models.CharField("Кто отправил", max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-id"]
        verbose_name = "Маркетинговая рассылка"
        verbose_name_plural = "Маркетинговые рассылки"

    def __str__(self) -> str:
        return f"#{self.id} {self.created_at:%d.%m.%Y %H:%M}"


class SmsLog(models.Model):
    created_at = models.DateTimeField("Когда", default=timezone.now, db_index=True)
    kind = models.CharField("Тип", max_length=20, choices=SmsKind.choices, default=SmsKind.DEBT)
    phone = models.CharField("Телефон / Max ID", max_length=64)
    text = models.TextField("Текст")
    success = models.BooleanField("Успех", default=False)
    provider = models.CharField("Канал", max_length=20, blank=True, default="")
    response = models.TextField("Ответ/ошибка", blank=True, default="")
    client = models.ForeignKey(
        Client,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sms_logs",
        verbose_name="Клиент",
    )
    order = models.ForeignKey(
        "Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sms_logs",
        verbose_name="Заказ",
    )
    blast = models.ForeignKey(
        MarketingBlast,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="Рассылка",
    )
    username = models.CharField("Кто отправил", max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-id"]
        verbose_name = "Лог сообщений"
        verbose_name_plural = "Лог сообщений"

    def __str__(self) -> str:
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.phone} {self.kind}"


def clamp_discount_percent(value) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else 0))
    except Exception:
        amount = Decimal("0")
    if amount < 0:
        amount = Decimal("0")
    if amount > 15:
        amount = Decimal("15")
    return amount.quantize(Decimal("0.01"))


def loyalty_discount_percent(orders_count: int) -> Decimal:
    """Совместимость: с 3 заказов — 10%, иначе 0."""
    return Decimal("10") if int(orders_count or 0) >= 3 else Decimal("0")


def next_numbered(prefix: str, model, field: str = "order_number") -> str:
    last = model.objects.order_by("-id").values_list(field, flat=True).first()
    if not last:
        return f"{prefix}-000001"
    try:
        value = int(str(last).split("-")[-1]) + 1
    except Exception:
        value = model.objects.count() + 1
    return f"{prefix}-{value:06d}"
