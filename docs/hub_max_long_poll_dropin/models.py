from django.db import models


class SiteNode(models.Model):
    site_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    callback_base_url = models.URLField(blank=True)
    site_token = models.CharField(max_length=255)
    site_secret = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.site_id})"


class Designer(models.Model):
    max_user_id = models.CharField(max_length=64, unique=True)
    full_name = models.CharField(max_length=255)
    sbp_phone = models.CharField(max_length=32)
    experience_text = models.TextField()
    portfolio_url = models.URLField()
    web_login = models.CharField(max_length=64, unique=True, blank=True)
    web_password_hash = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.full_name


class HubBrief(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        QUEUED = "queued", "В очереди"
        ASSIGNED = "assigned", "Назначена"
        IN_PROGRESS = "in_progress", "В работе"
        NEEDS_CLARIFICATION = "needs_clarification", "Нужно уточнение"
        CLARIFICATION_PROVIDED = "clarification_provided", "Уточнение получено"
        DONE = "done", "Готово"
        CANCELLED = "cancelled", "Отменено"

    public_id = models.CharField(max_length=64, unique=True)
    site = models.ForeignKey(SiteNode, on_delete=models.PROTECT, related_name="briefs")
    local_brief_id = models.PositiveIntegerField()
    brief_number = models.CharField(max_length=64)
    client_ref = models.CharField(max_length=128)
    model_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    agreed_price = models.DecimalField(max_digits=12, decimal_places=2)
    designer_share_amount = models.DecimalField(max_digits=12, decimal_places=2)
    site_share_amount = models.DecimalField(max_digits=12, decimal_places=2)
    has_stl = models.BooleanField(default=False)
    screenshots_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    designer = models.ForeignKey(
        Designer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_briefs",
    )
    eta = models.CharField(max_length=128, blank=True)
    last_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "local_brief_id"], name="uniq_brief_per_site_local_id"
            )
        ]

    def __str__(self) -> str:
        return f"{self.public_id} ({self.get_status_display()})"


class HubBriefEvent(models.Model):
    event_id = models.CharField(max_length=64, unique=True)
    brief = models.ForeignKey(HubBrief, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(max_length=64)
    payload_json = models.JSONField(default=dict)
    delivered_ok = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class BotConversationState(models.Model):
    class State(models.TextChoices):
        WAITING_FULL_NAME = "waiting_full_name", "Ожидание ФИО"
        WAITING_SBP_PHONE = "waiting_sbp_phone", "Ожидание телефона СБП"
        WAITING_EXPERIENCE = "waiting_experience", "Ожидание опыта"
        WAITING_PORTFOLIO = "waiting_portfolio", "Ожидание портфолио"

    max_user_id = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=64, choices=State.choices)
    full_name = models.CharField(max_length=255, blank=True)
    sbp_phone = models.CharField(max_length=32, blank=True)
    experience_text = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class DesignerSessionToken(models.Model):
    key = models.CharField(max_length=64, unique=True)
    designer = models.ForeignKey(Designer, on_delete=models.CASCADE, related_name="session_tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_revoked = models.BooleanField(default=False)


class MaxBotSettings(models.Model):
    """Singleton: Max bot token + long-poll for designer registration."""

    bot_token = models.CharField("Токен бота Max", max_length=255, blank=True, default="")
    bot_username = models.CharField("Username бота", max_length=128, blank=True, default="")
    long_poll_enabled = models.BooleanField("Long Poll включён", default=True)
    updates_marker = models.BigIntegerField("Marker /updates", null=True, blank=True)
    welcome_text = models.TextField(
        "Приветствие",
        blank=True,
        default=(
            "Здравствуйте! Для регистрации дизайнера отправьте точно:\n"
            "Регистрация: Дизайнер"
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки Max-бота"
        verbose_name_plural = "Настройки Max-бота"

    def __str__(self) -> str:
        return "Max bot settings"

    @classmethod
    def get_solo(cls) -> "MaxBotSettings":
        obj = cls.objects.first()
        if obj:
            return obj
        return cls.objects.create()
