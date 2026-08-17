from decimal import Decimal

from django.db import migrations, models
from django.db.models import F


def backfill_taxable_sum(apps, schema_editor):
    Order = apps.get_model("workshop", "Order")
    Order.objects.update(taxable_sum=F("total_sum"), parts_sum=Decimal("0"))


class Migration(migrations.Migration):
    dependencies = [
        ("workshop", "0025_tv_display_viewport"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="parts_sum",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=12,
                verbose_name="Комплектующие (без налога)",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="taxable_sum",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=12,
                verbose_name="Сумма для Мой налог",
            ),
        ),
        migrations.AddField(
            model_name="orderline",
            name="kind",
            field=models.CharField(
                choices=[("service", "Услуга"), ("parts", "Комплектующие")],
                db_index=True,
                default="service",
                max_length=16,
                verbose_name="Тип строки",
            ),
        ),
        migrations.RunPython(backfill_taxable_sum, migrations.RunPython.noop),
    ]
