from decimal import Decimal

from django.db import migrations


def backfill_regular_discounts(apps, schema_editor):
    Client = apps.get_model("workshop", "Client")
    Order = apps.get_model("workshop", "Order")
    for client in Client.objects.all():
        count = Order.objects.filter(client_id=client.id).count()
        if count >= 3 and (not client.discount_manual) and client.discount_percent < Decimal("10"):
            client.discount_percent = Decimal("10")
            client.save(update_fields=["discount_percent"])


class Migration(migrations.Migration):

    dependencies = [
        ("workshop", "0011_client_configurable_discount"),
    ]

    operations = [
        migrations.RunPython(backfill_regular_discounts, migrations.RunPython.noop),
    ]
