from django.contrib import admin

from workshop.models import (
    AcceptanceAct,
    AuditLog,
    Client,
    Order,
    OrderLine,
    PrintJob,
    Service,
    ServiceCategory,
    SmsLog,
    SmsSettings,
    YandexAiSettings,
)


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "discount_percent", "discount_manual", "max_user_id", "allow_marketing_sms", "created_at")
    search_fields = ("name", "phone", "comment", "max_user_id")
    list_filter = ("allow_marketing_sms", "discount_manual")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent")
    search_fields = ("name",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "client",
        "created_at",
        "total_sum",
        "payment_method",
        "mytax_issued",
        "status",
    )
    search_fields = ("order_number", "client__name", "client__phone")
    list_filter = ("payment_method", "mytax_issued", "status")
    inlines = [OrderLineInline]


@admin.register(AcceptanceAct)
class AcceptanceActAdmin(admin.ModelAdmin):
    list_display = ("act_number", "client", "device_type", "status", "created_at")
    search_fields = ("act_number", "client__name", "brand_model")
    list_filter = ("status", "device_type")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action", "entity_type", "entity_id", "ip_address")
    search_fields = ("username", "action", "details", "entity_id")
    list_filter = ("action", "entity_type")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "title",
        "copy_index",
        "copies_total",
        "status",
        "username",
        "finished_at",
    )
    list_filter = ("status", "doc_type")
    search_fields = ("title", "entity_id", "username", "error")


@admin.register(SmsSettings)
class SmsSettingsAdmin(admin.ModelAdmin):
    list_display = ("provider", "enabled", "marketing_enabled", "long_poll_enabled", "updated_at")


@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "phone", "success", "provider", "username")
    list_filter = ("kind", "success", "provider")
    search_fields = ("phone", "text", "response")


@admin.register(YandexAiSettings)
class YandexAiSettingsAdmin(admin.ModelAdmin):
    list_display = ("enabled", "folder_id", "model_name", "report_hour_msk", "last_report_date", "updated_at")
