from django.db import migrations
from django.utils import timezone


def backfill_closed_at(apps, schema_editor):
    Order = apps.get_model("workshop", "Order")
    # Завершённые без даты закрытия — считаем закрытыми в момент создания
    # (иначе они никогда не попадали в должники).
    for order in Order.objects.filter(status="done", closed_at__isnull=True).iterator():
        order.closed_at = order.created_at or timezone.now()
        order.save(update_fields=["closed_at"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0005_order_closed_at"),
    ]

    operations = [
        migrations.RunPython(backfill_closed_at, noop_reverse),
    ]
