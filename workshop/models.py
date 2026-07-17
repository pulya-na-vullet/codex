from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, Sum
from django.utils import timezone


class Client(models.Model):
    name = models.CharField("Имя", max_length=200)
    phone = models.CharField("Телефон", max_length=20, unique=True)
    comment = models.TextField("Комментарий", blank=True, default="")
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
        return total or Decimal("0")

    @property
    def is_regular(self) -> bool:
        return self.total_orders > 3

    @property
    def discount_percent(self) -> Decimal:
        return loyalty_discount_percent(self.total_orders)


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
    ACTIVE = "active", "Активен"
    DONE = "done", "Завершён"
    CANCELLED = "cancelled", "Отменён"


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

    class Meta:
        ordering = ["-id"]
        verbose_name = "Заказ-наряд"
        verbose_name_plural = "Заказ-наряды"

    def __str__(self) -> str:
        return self.order_number

    def recalculate_totals(self, save: bool = True) -> Decimal:
        subtotal = Decimal("0")
        for line in self.lines.all():
            subtotal += line.line_total
        if self.client_id:
            # discount based on visits including this order
            visits = Order.objects.filter(client_id=self.client_id).count()
            self.discount_percent = loyalty_discount_percent(visits)
        else:
            self.discount_percent = Decimal("0")
        self.subtotal_sum = subtotal.quantize(Decimal("0.01"))
        factor = (Decimal("100") - self.discount_percent) / Decimal("100")
        self.total_sum = (subtotal * factor).quantize(Decimal("0.01"))
        if save:
            self.save(update_fields=["discount_percent", "subtotal_sum", "total_sum"])
        return self.total_sum


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


def loyalty_discount_percent(orders_count: int) -> Decimal:
    count = int(orders_count or 0)
    if count >= 10:
        return Decimal("10")
    if count >= 7:
        return Decimal("7")
    if count > 3:
        return Decimal("5")
    return Decimal("0")


def next_numbered(prefix: str, model, field: str = "order_number") -> str:
    last = model.objects.order_by("-id").values_list(field, flat=True).first()
    if not last:
        return f"{prefix}-000001"
    try:
        value = int(str(last).split("-")[-1]) + 1
    except Exception:
        value = model.objects.count() + 1
    return f"{prefix}-{value:06d}"
