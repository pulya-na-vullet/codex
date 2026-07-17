from django.contrib import admin

from workshop.models import AcceptanceAct, Client, Order, OrderLine, Service, ServiceCategory


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "created_at")
    search_fields = ("name", "phone", "comment")


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
    list_display = ("order_number", "client", "created_at", "total_sum", "status")
    search_fields = ("order_number", "client__name", "client__phone")
    inlines = [OrderLineInline]


@admin.register(AcceptanceAct)
class AcceptanceActAdmin(admin.ModelAdmin):
    list_display = ("act_number", "client", "device_type", "created_at")
    search_fields = ("act_number", "client__name", "brand_model")
